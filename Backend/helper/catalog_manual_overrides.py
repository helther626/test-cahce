from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from bson import ObjectId

from Backend.helper.database import Database
from Backend.helper import auto_catalog


#----- Persistent manual overrides for auto catalogs.
#----- The auto-sync result remains the generated source, while these two
#----- arrays are the administrator's explicit decisions:
#----- manual_additions = force an item into the catalog
#----- manual_removals = force an item out of the catalog

def _media_type(value) -> str:
    return "tv" if str(value or "movie").lower() in ("tv", "series") else "movie"


def _identity(item: dict) -> Tuple[str, int, int]:
    return (
        _media_type(item.get("media_type")),
        int(item.get("tmdb_id")),
        int(item.get("db_index", 1)),
    )


def _ref(tmdb_id: int, db_index: int, media_type: str) -> dict:
    return {
        "tmdb_id": int(tmdb_id),
        "db_index": int(db_index),
        "media_type": _media_type(media_type),
    }


def _item_from_doc(doc: dict, fallback: dict = None) -> dict:
    fallback = fallback or {}
    media_type = _media_type(doc.get("media_type") or fallback.get("media_type"))
    return {
        "tmdb_id": int(doc.get("tmdb_id", fallback.get("tmdb_id"))),
        "db_index": int(fallback.get("db_index", doc.get("db_index", 1))),
        "media_type": media_type,
        "added_at": fallback.get("added_at") or doc.get("added_at") or datetime.utcnow(),
        "updated_on": doc.get("updated_on"),
        "visibility": doc.get("visibility") or fallback.get("visibility") or "public",
        "allowed_tokens": doc.get("allowed_tokens") or fallback.get("allowed_tokens") or [],
    }


async def _existing_manual_items(db: Database, additions: List[dict]) -> Dict[Tuple[str, int, int], dict]:
    if not additions:
        return {}
    docs = await db.get_documents(additions)
    result: Dict[Tuple[str, int, int], dict] = {}
    for doc in docs:
        try:
            key = _identity(doc)
            result[key] = doc
        except (TypeError, ValueError):
            continue
    return result


async def _merge_catalog(db: Database, catalog: dict, generated_items: Optional[List[dict]] = None) -> dict:
    if not catalog or not catalog.get("auto"):
        return catalog

    base_items = list(catalog.get("items") or []) if generated_items is None else list(generated_items or [])
    additions = list(catalog.get("manual_additions") or [])
    removal_refs = list(catalog.get("manual_removals") or [])

    blocked: Set[Tuple[str, int, int]] = set()
    for item in removal_refs:
        try:
            blocked.add(_identity(item))
        except (TypeError, ValueError):
            continue

    merged: List[dict] = []
    seen: Set[Tuple[str, int, int]] = set()
    for item in base_items:
        try:
            key = _identity(item)
        except (TypeError, ValueError):
            continue
        if key in blocked or key in seen:
            continue
        seen.add(key)
        merged.append(item)

    existing = await _existing_manual_items(db, additions)
    valid_additions: List[dict] = []
    for addition in additions:
        try:
            key = _identity(addition)
        except (TypeError, ValueError):
            continue
        if key in blocked:
            continue
        doc = existing.get(key)
        if not doc:
            #----- Keep the override record only while the media still exists.
            continue
        valid_additions.append(addition)
        if key in seen:
            continue
        merged.append(_item_from_doc(doc, addition))
        seen.add(key)

    #----- Clean stale additions, but never manufacture a removal just because
    #----- a media document is temporarily unavailable.
    catalog["manual_additions"] = valid_additions
    catalog["manual_removals"] = removal_refs

    if catalog.get("name") in {"Recently Added Movies", "Recently Added Series"}:
        merged.sort(key=lambda it: it.get("added_at") or datetime.min, reverse=True)
    else:
        merged.sort(key=lambda it: it.get("updated_on") or it.get("added_at") or datetime.min, reverse=True)

    catalog["items"] = merged
    catalog["item_count"] = len(merged)
    return catalog


async def _load_auto_catalog(db: Database, auto_key: str) -> Optional[dict]:
    catalog = await db.dbs["tracking"]["custom_catalogs"].find_one({"auto_key": auto_key})
    if catalog:
        catalog = dict(catalog)
        catalog["_id"] = catalog.get("_id")
        return catalog
    return None


async def _persist_merged_catalog(db: Database, catalog: dict, *, create_if_missing: bool = False) -> None:
    if not catalog or not catalog.get("auto"):
        return
    collection = db.dbs["tracking"]["custom_catalogs"]
    now = datetime.utcnow()
    merged = await _merge_catalog(db, catalog)
    payload = {
        "items": merged.get("items", []),
        "item_count": merged.get("item_count", 0),
        "manual_additions": merged.get("manual_additions", []),
        "manual_removals": merged.get("manual_removals", []),
        "updated_at": now,
    }
    if create_if_missing:
        payload.update({
            "name": merged.get("name"),
            "auto": True,
            "auto_key": merged.get("auto_key"),
            "visible": merged.get("visible", True),
        })
        await collection.update_one(
            {"auto_key": merged.get("auto_key")},
            {"$set": payload, "$setOnInsert": {
                "created_at": now,
                "visibility": "public",
                "allowed_tokens": [],
            }},
            upsert=True,
        )
    else:
        await collection.update_one({"_id": catalog["_id"]}, {"$set": payload})


async def _rebuild_with_overrides(db, catalog_items: Dict[str, List[dict]], enabled_names: Set[str]) -> None:
    #----- First let the original generator do its normal full rebuild.
    await _ORIGINAL_REBUILD(db, catalog_items, enabled_names)

    #----- Then persist the administrator's explicit additions/removals into
    #----- the final items array. This is important: reads no longer need a
    #----- monkey-patched overlay to see the correct catalog.
    for name in enabled_names:
        auto_key = auto_catalog._catalog_key(name)
        catalog = await _load_auto_catalog(db, auto_key)
        if catalog:
            await _persist_merged_catalog(db, catalog)


async def _flush_with_overrides(db, catalog_items: Dict[str, List[dict]]) -> None:
    #----- Instant sync must respect a manual removal before it writes anything.
    filtered: Dict[str, List[dict]] = {}
    for name, items in catalog_items.items():
        auto_key = auto_catalog._catalog_key(name)
        catalog = await _load_auto_catalog(db, auto_key)
        blocked = set()
        if catalog:
            for removal in catalog.get("manual_removals") or []:
                try:
                    blocked.add(_identity(removal))
                except (TypeError, ValueError):
                    continue
        filtered[name] = [
            item for item in items
            if _identity(item) not in blocked
        ]

    await _ORIGINAL_FLUSH(db, filtered)

    for name in filtered:
        catalog = await _load_auto_catalog(db, auto_catalog._catalog_key(name))
        if catalog:
            await _persist_merged_catalog(db, catalog)


_ORIGINAL_REBUILD = auto_catalog._rebuild_auto_catalogs
_ORIGINAL_FLUSH = auto_catalog._flush_quick_items
_ORIGINAL_ADD_ITEM = Database.add_item_to_custom_catalog
_ORIGINAL_REMOVE_ITEM = Database.remove_item_from_custom_catalog
_ORIGINAL_PURGE_MEDIA = Database.purge_media_from_catalogs


async def _add_item(self: Database, catalog_id: str, tmdb_id: int, db_index: int, media_type: str) -> bool:
    catalog = await self.get_custom_catalog(catalog_id)
    added = await _ORIGINAL_ADD_ITEM(self, catalog_id, tmdb_id, db_index, media_type)
    if not catalog or not catalog.get("auto"):
        return added

    normalized = _media_type(media_type)
    media = await self.get_document(normalized, int(tmdb_id), int(db_index))
    if not media:
        return added

    item = _item_from_doc(media, {
        "tmdb_id": tmdb_id,
        "db_index": db_index,
        "media_type": normalized,
        "added_at": datetime.utcnow(),
    })
    collection = self.dbs["tracking"]["custom_catalogs"]
    await collection.update_one(
        {"_id": ObjectId(str(catalog["_id"]))},
        {
            "$pull": {"manual_removals": _ref(tmdb_id, db_index, normalized)},
            "$addToSet": {"manual_additions": item},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )
    #----- Immediately persist the final visible items, not just the override.
    refreshed = await self.get_custom_catalog(catalog_id)
    if refreshed:
        await _persist_merged_catalog(self, refreshed)
    return True


async def _remove_item(self: Database, catalog_id: str, tmdb_id: int, db_index: int, media_type: str) -> bool:
    catalog = await self.get_custom_catalog(catalog_id)
    removed = await _ORIGINAL_REMOVE_ITEM(self, catalog_id, tmdb_id, db_index, media_type)
    if not catalog or not catalog.get("auto"):
        return removed

    normalized = _media_type(media_type)
    ref = _ref(tmdb_id, db_index, normalized)
    collection = self.dbs["tracking"]["custom_catalogs"]
    await collection.update_one(
        {"_id": ObjectId(str(catalog["_id"]))},
        {
            "$pull": {"manual_additions": ref},
            "$addToSet": {"manual_removals": ref},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )
    refreshed = await self.get_custom_catalog(catalog_id)
    if refreshed:
        await _persist_merged_catalog(self, refreshed)
    return True


async def _purge_media(self: Database, tmdb_id: int, media_type: str) -> int:
    count = await _ORIGINAL_PURGE_MEDIA(self, tmdb_id, media_type)
    if tmdb_id in (None, "", 0):
        return count

    normalized = _media_type(media_type)
    collection = self.dbs["tracking"]["custom_catalogs"]
    try:
        result = await collection.update_many(
            {},
            {
                "$pull": {
                    "manual_additions": {"tmdb_id": int(tmdb_id), "media_type": normalized},
                    "manual_removals": {"tmdb_id": int(tmdb_id), "media_type": normalized},
                },
                "$set": {"updated_at": datetime.utcnow()},
            },
        )
        return count + result.modified_count
    except Exception:
        return count


#----- Install only after all original implementations have been imported.
#----- The wrapper writes the final merged catalog to MongoDB; it does not
#----- alter get_custom_catalog/get_custom_catalogs, so normal reads stay normal.
auto_catalog._rebuild_auto_catalogs = _rebuild_with_overrides
auto_catalog._flush_quick_items = _flush_with_overrides
Database.add_item_to_custom_catalog = _add_item
Database.remove_item_from_custom_catalog = _remove_item
Database.purge_media_from_catalogs = _purge_media

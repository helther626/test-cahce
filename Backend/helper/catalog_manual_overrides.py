from datetime import datetime
from typing import Dict, List, Optional

from bson import ObjectId

from Backend.helper.database import Database


#----- Auto-catalog manual overrides live beside generated items so a full
#----- auto rebuild never destroys an admin's explicit add/remove decisions.
def _identity(item: dict) -> tuple:
    return (
        str(item.get("media_type") or "movie"),
        int(item.get("tmdb_id")),
        int(item.get("db_index", 1)),
    )


async def _merge_auto_overrides(db: Database, catalog: dict) -> dict:
    if not catalog or not catalog.get("auto"):
        return catalog

    items = list(catalog.get("items") or [])
    additions = list(catalog.get("manual_additions") or [])
    removals = {_identity(item) for item in (catalog.get("manual_removals") or [])}

    merged: List[dict] = []
    seen = set()
    for item in items:
        try:
            key = _identity(item)
        except (TypeError, ValueError):
            continue
        if key in removals or key in seen:
            continue
        seen.add(key)
        merged.append(item)

    #----- Manual additions are references to real storage documents. Only
    #----- expose them when the referenced media still exists.
    if additions:
        existing_docs = await db.get_documents(additions)
        existing_by_key: Dict[tuple, dict] = {}
        for doc in existing_docs:
            try:
                existing_by_key[_identity(doc)] = doc
            except (TypeError, ValueError):
                continue

        for ref in additions:
            try:
                key = _identity(ref)
            except (TypeError, ValueError):
                continue
            if key in removals or key in seen:
                continue
            doc = existing_by_key.get(key)
            if not doc:
                continue
            item = dict(ref)
            item.update({
                "tmdb_id": int(doc.get("tmdb_id")),
                "db_index": int(ref.get("db_index", doc.get("db_index", 1))),
                "media_type": "tv" if doc.get("media_type") in ("tv", "series") else "movie",
                "visibility": doc.get("visibility") or ref.get("visibility") or "public",
                "allowed_tokens": doc.get("allowed_tokens") or ref.get("allowed_tokens") or [],
                "updated_on": doc.get("updated_on"),
            })
            seen.add(key)
            merged.append(item)

    if catalog.get("name") in {"Recently Added Movies", "Recently Added Series"}:
        merged.sort(key=lambda it: it.get("added_at") or datetime.min, reverse=True)
    else:
        merged.sort(key=lambda it: it.get("updated_on") or it.get("added_at") or datetime.min, reverse=True)

    catalog["items"] = merged
    catalog["item_count"] = len(merged)
    return catalog


_ORIGINAL_GET_CUSTOM_CATALOG = Database.get_custom_catalog
_ORIGINAL_GET_CUSTOM_CATALOGS = Database.get_custom_catalogs
_ORIGINAL_ADD_ITEM = Database.add_item_to_custom_catalog
_ORIGINAL_REMOVE_ITEM = Database.remove_item_from_custom_catalog
_ORIGINAL_PURGE_MEDIA = Database.purge_media_from_catalogs


async def _get_custom_catalog_with_overrides(self: Database, catalog_id: str) -> Optional[dict]:
    catalog = await _ORIGINAL_GET_CUSTOM_CATALOG(self, catalog_id)
    return await _merge_auto_overrides(self, catalog) if catalog else catalog


async def _get_custom_catalogs_with_overrides(self: Database, visible_only: bool = False) -> List[dict]:
    catalogs = await _ORIGINAL_GET_CUSTOM_CATALOGS(self, visible_only=visible_only)
    for catalog in catalogs:
        if catalog.get("auto"):
            await _merge_auto_overrides(self, catalog)
    return catalogs


async def _add_item_with_override(self: Database, catalog_id: str, tmdb_id: int, db_index: int, media_type: str) -> bool:
    catalog = await _ORIGINAL_GET_CUSTOM_CATALOG(self, catalog_id)
    added = await _ORIGINAL_ADD_ITEM(self, catalog_id, tmdb_id, db_index, media_type)
    if not catalog or not catalog.get("auto"):
        return added

    normalized_type = "tv" if media_type in ("tv", "series") else "movie"
    media = await self.get_document(normalized_type, int(tmdb_id), int(db_index))
    item = {
        "tmdb_id": int(tmdb_id),
        "db_index": int(db_index),
        "media_type": normalized_type,
        "added_at": datetime.utcnow(),
        "visibility": (media or {}).get("visibility") or "public",
        "allowed_tokens": (media or {}).get("allowed_tokens") or [],
    }
    collection = self.dbs["tracking"]["custom_catalogs"]
    result = await collection.update_one(
        {"_id": ObjectId(str(catalog["_id"]))},
        {
            "$pull": {"manual_removals": {"tmdb_id": int(tmdb_id), "db_index": int(db_index), "media_type": normalized_type}},
            "$addToSet": {"manual_additions": item},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )
    return added or result.modified_count > 0


async def _remove_item_with_override(self: Database, catalog_id: str, tmdb_id: int, db_index: int, media_type: str) -> bool:
    catalog = await _ORIGINAL_GET_CUSTOM_CATALOG(self, catalog_id)
    removed = await _ORIGINAL_REMOVE_ITEM(self, catalog_id, tmdb_id, db_index, media_type)
    if not catalog or not catalog.get("auto"):
        return removed

    normalized_type = "tv" if media_type in ("tv", "series") else "movie"
    ref = {"tmdb_id": int(tmdb_id), "db_index": int(db_index), "media_type": normalized_type}
    collection = self.dbs["tracking"]["custom_catalogs"]
    result = await collection.update_one(
        {"_id": ObjectId(str(catalog["_id"]))},
        {
            "$pull": {"manual_additions": ref},
            "$addToSet": {"manual_removals": ref},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )
    return removed or result.modified_count > 0


async def _purge_media_with_overrides(self: Database, tmdb_id: int, media_type: str) -> int:
    count = await _ORIGINAL_PURGE_MEDIA(self, tmdb_id, media_type)
    if tmdb_id in (None, "", 0):
        return count
    normalized_type = "tv" if media_type in ("tv", "series") else "movie"
    collection = self.dbs["tracking"]["custom_catalogs"]
    result = await collection.update_many(
        {},
        {
            "$pull": {
                "manual_additions": {"tmdb_id": int(tmdb_id), "media_type": normalized_type},
                "manual_removals": {"tmdb_id": int(tmdb_id), "media_type": normalized_type},
            },
            "$set": {"updated_at": datetime.utcnow()},
        },
    )
    return count + result.modified_count


Database.get_custom_catalog = _get_custom_catalog_with_overrides
Database.get_custom_catalogs = _get_custom_catalogs_with_overrides
Database.add_item_to_custom_catalog = _add_item_with_override
Database.remove_item_from_custom_catalog = _remove_item_with_override
Database.purge_media_from_catalogs = _purge_media_with_overrides

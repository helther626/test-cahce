from copy import deepcopy
from datetime import datetime

from Backend.helper.database import Database


#----- Keep a metadata relink atomic from the user's point of view:
#----- the existing Database implementation already preserves Telegram
#----- movie/episode content. This wrapper additionally follows the identity
#----- change into tracking references and subtitle records.
_ORIGINAL_REPLACE_MEDIA_METADATA = Database.replace_media_metadata


def _media_type(value) -> str:
    return "tv" if str(value or "movie").lower() in ("tv", "series") else "movie"


def _matches_ref(item: dict, tmdb_id: int, db_index: int, media_type: str) -> bool:
    try:
        return (
            int(item.get("tmdb_id")) == int(tmdb_id)
            and int(item.get("db_index", db_index)) == int(db_index)
            and _media_type(item.get("media_type")) == media_type
        )
    except (TypeError, ValueError):
        return False


def _rewrite_ref(item: dict, new_tmdb_id: int, new_db_index: int, media_type: str) -> dict:
    updated = deepcopy(item or {})
    updated["tmdb_id"] = int(new_tmdb_id)
    updated["db_index"] = int(new_db_index)
    updated["media_type"] = media_type
    updated["updated_on"] = datetime.utcnow()
    return updated


async def _follow_identity_change(
    db: Database,
    *,
    old_tmdb_id: int,
    old_imdb_id: str | None,
    db_index: int,
    media_type: str,
    updated_doc: dict,
) -> None:
    new_tmdb_id = int(updated_doc.get("tmdb_id") or old_tmdb_id)
    new_imdb_id = updated_doc.get("imdb_id") or old_imdb_id
    new_db_index = int(updated_doc.get("db_index") or db_index)

    #----- Catalogs are stored in tracking DB as references. Follow the new
    #----- identity so a relink cannot leave a stale catalog entry behind.
    catalogs = db.dbs["tracking"]["custom_catalogs"]
    cursor = catalogs.find({})
    async for catalog in cursor:
        changed = False
        payload = {}

        for field in ("items", "manual_additions", "manual_removals"):
            values = catalog.get(field)
            if not isinstance(values, list):
                continue

            rewritten = []
            seen = set()
            for item in values:
                if _matches_ref(item, old_tmdb_id, db_index, media_type):
                    item = _rewrite_ref(item, new_tmdb_id, new_db_index, media_type)
                    changed = True
                try:
                    key = (
                        int(item.get("tmdb_id")),
                        int(item.get("db_index", new_db_index)),
                        _media_type(item.get("media_type")),
                    )
                except (TypeError, ValueError):
                    key = repr(item)
                if key in seen:
                    changed = True
                    continue
                seen.add(key)
                rewritten.append(item)

            if changed:
                payload[field] = rewritten

        if changed:
            payload["updated_at"] = datetime.utcnow()
            await catalogs.update_one({"_id": catalog["_id"]}, {"$set": payload})

    #----- Subtitles are indexed by IMDb identity. Move them to the new IMDb
    #----- id so the existing subtitle files continue to appear in Stremio.
    if old_imdb_id and new_imdb_id and old_imdb_id != new_imdb_id:
        await db.dbs["tracking"]["subtitles"].update_many(
            {"imdb_id": old_imdb_id, "media_type": media_type},
            {"$set": {"imdb_id": new_imdb_id}},
        )


async def _replace_media_metadata(
    self: Database,
    media_type: str,
    tmdb_id: int,
    db_index: int,
    metadata: dict,
):
    normalized = _media_type(media_type)
    old_doc = await self.get_document(normalized, int(tmdb_id), int(db_index))
    old_imdb_id = old_doc.get("imdb_id") if old_doc else None

    updated_doc = await _ORIGINAL_REPLACE_MEDIA_METADATA(
        self,
        media_type=normalized,
        tmdb_id=int(tmdb_id),
        db_index=int(db_index),
        metadata=metadata,
    )
    if not updated_doc:
        return updated_doc

    await _follow_identity_change(
        self,
        old_tmdb_id=int(tmdb_id),
        old_imdb_id=old_imdb_id,
        db_index=int(db_index),
        media_type=normalized,
        updated_doc=updated_doc,
    )
    return updated_doc


#----- Install after the original Database implementation is available.
Database.replace_media_metadata = _replace_media_metadata

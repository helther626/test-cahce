from copy import deepcopy
from datetime import datetime

from Backend.helper.database import Database


#----- Keep a metadata relink atomic from the user's point of view:
#----- the existing Database implementation already preserves Telegram
#----- movie/episode content. This wrapper additionally follows the identity
#----- change into tracking references and subtitle records.
_ORIGINAL_REPLACE_MEDIA_METADATA = Database.replace_media_metadata
_ORIGINAL_GET_MEDIA_DETAILS = Database.get_media_details
_ALIAS_COLLECTION = "media_identity_aliases"


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


async def _resolve_alias(db: Database, imdb_id: str | None, media_type: str) -> str | None:
    if not imdb_id:
        return imdb_id
    aliases = db.dbs["tracking"][_ALIAS_COLLECTION]
    current = str(imdb_id)
    seen = set()
    for _ in range(8):
        if current in seen:
            break
        seen.add(current)
        doc = await aliases.find_one({"old_imdb_id": current, "media_type": media_type})
        if not doc or not doc.get("new_imdb_id"):
            break
        current = str(doc["new_imdb_id"])
    return current


async def _save_identity_alias(
    db: Database,
    *,
    old_imdb_id: str | None,
    new_imdb_id: str | None,
    old_tmdb_id: int,
    new_tmdb_id: int,
    db_index: int,
    media_type: str,
) -> None:
    if not old_imdb_id or not new_imdb_id or old_imdb_id == new_imdb_id:
        return
    aliases = db.dbs["tracking"][_ALIAS_COLLECTION]
    await aliases.update_one(
        {"old_imdb_id": old_imdb_id, "media_type": media_type},
        {"$set": {
            "old_imdb_id": old_imdb_id,
            "new_imdb_id": new_imdb_id,
            "old_tmdb_id": int(old_tmdb_id),
            "new_tmdb_id": int(new_tmdb_id),
            "db_index": int(db_index),
            "media_type": media_type,
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


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

    #----- Persist the old->new identity. Stremio requests are keyed by IMDb,
    #----- so this alias is what keeps an old installed/catalog ID attached to
    #----- the newly relinked Telegram content.
    await _save_identity_alias(
        db,
        old_imdb_id=old_imdb_id,
        new_imdb_id=new_imdb_id,
        old_tmdb_id=int(old_tmdb_id),
        new_tmdb_id=new_tmdb_id,
        db_index=new_db_index,
        media_type=media_type,
    )

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


async def _get_media_details(
    self: Database,
    imdb_id: str = None,
    season_number: int = None,
    episode_number: int = None,
    kitsu_id: int = None,
    absolute_episode: int = None,
):
    #----- Resolve legacy Stremio IMDb IDs before every metadata/episode
    #----- lookup. Episode/season requests identify TV explicitly. A bare
    #----- series Meta request does not, so try the TV alias first and then
    #----- the movie alias instead of incorrectly treating every bare IMDb
    #----- ID as a movie.
    if imdb_id:
        is_tv_request = season_number is not None or episode_number is not None
        if is_tv_request:
            resolved_imdb = await _resolve_alias(self, imdb_id, "tv")
            return await _ORIGINAL_GET_MEDIA_DETAILS(
                self,
                imdb_id=resolved_imdb,
                season_number=season_number,
                episode_number=episode_number,
                kitsu_id=kitsu_id,
                absolute_episode=absolute_episode,
            )

        resolved_tv_imdb = await _resolve_alias(self, imdb_id, "tv")
        tv_result = await _ORIGINAL_GET_MEDIA_DETAILS(
            self,
            imdb_id=resolved_tv_imdb,
            season_number=season_number,
            episode_number=episode_number,
            kitsu_id=kitsu_id,
            absolute_episode=absolute_episode,
        )
        if tv_result:
            return tv_result

        resolved_movie_imdb = await _resolve_alias(self, imdb_id, "movie")
        if resolved_movie_imdb != resolved_tv_imdb:
            return await _ORIGINAL_GET_MEDIA_DETAILS(
                self,
                imdb_id=resolved_movie_imdb,
                season_number=season_number,
                episode_number=episode_number,
                kitsu_id=kitsu_id,
                absolute_episode=absolute_episode,
            )
        return tv_result

    #----- Kitsu requests have no IMDb identity to relink.
    return await _ORIGINAL_GET_MEDIA_DETAILS(
        self,
        imdb_id=None,
        season_number=season_number,
        episode_number=episode_number,
        kitsu_id=kitsu_id,
        absolute_episode=absolute_episode,
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
Database.get_media_details = _get_media_details
Database.replace_media_metadata = _replace_media_metadata

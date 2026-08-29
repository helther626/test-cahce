import re

import PTN

from Backend.fastapi.routes import stremio_routes


#----- Keep all stream generation/URL logic intact. Only replace the visible
#----- local Telegram stream label with the detected resolution.
_ORIGINAL_FORMAT_STREAM_DETAILS = stremio_routes.format_stream_details
_RESOLUTION_RE = re.compile(r"(?<!\d)(2160p|1080p|720p|480p|360p)(?!\d)", re.IGNORECASE)


def _resolution_only_format_stream_details(
    filename: str,
    quality: str,
    size: str,
    is_split: bool = False,
) -> tuple[str, str]:
    _original_name, stream_title = _ORIGINAL_FORMAT_STREAM_DETAILS(
        filename, quality, size, is_split=is_split
    )

    resolution = None
    try:
        parsed = PTN.parse(filename)
        resolution = parsed.get("resolution")
    except Exception:
        resolution = None

    if not resolution:
        match = _RESOLUTION_RE.search(str(filename or ""))
        resolution = match.group(1) if match else None

    if not resolution:
        quality_match = _RESOLUTION_RE.search(str(quality or ""))
        resolution = quality_match.group(1) if quality_match else None

    if not resolution:
        # Preserve the existing parsed label as a final fallback, but strip
        # the source/quality branding so the UI still stays clean.
        match = _RESOLUTION_RE.search(str(_original_name or ""))
        resolution = match.group(1) if match else str(quality or "HD")

    return str(resolution), stream_title


stremio_routes.format_stream_details = _resolution_only_format_stream_details

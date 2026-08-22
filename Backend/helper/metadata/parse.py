"""Filename / caption parsing helpers."""
from __future__ import annotations

import re
import traceback

import PTN
from guessit import guessit as _guessit

from Backend.helper.metadata.common import COMBINED_EPISODE_BASE, COMBINED_SEASON, first
from Backend.helper.split_files import parse_combined_episodes, parse_split_info, strip_part_suffix
from Backend.logger import LOGGER

_MULTIPART_RE = re.compile(r"(?:part|cd|disc|disk)[s._-]*\d+(?=\.\w+$)", re.IGNORECASE)

# Explicit season/episode anchors
_EXPLICIT_SXXEXX_RE = re.compile(r"(?i)\bs\d{1,2}[._\s-]*e\d{1,3}\b")
_EXPLICIT_NXNN_RE = re.compile(r"(?i)\b\d{1,2}x\d{2,3}\b")
_EXPLICIT_SEASON_WORD_RE = re.compile(r"(?i)\b(?:season|series)\s*0*\d{1,2}\b")
# New simple series forms: "Title EP1" / "Title EP01" (Season 1)
_EXPLICIT_EP_ONLY_RE = re.compile(r"(?i)(?:^|[\s._-])ep\s*0*(\d{1,4})(?=[\s._-]|$)")
# Simple movie form: "Title (2011)" / "Title (2011) 1080p"
_SIMPLE_MOVIE_YEAR_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:19|20)\d{2})\)\s*(?:[._ -].*)?$", re.IGNORECASE)


def parse_media_name(name: str) -> dict:
    try:
        ptn = PTN.parse(name) or {}
    except Exception as e:
        LOGGER.warning(f"PTN parsing failed for {name}: {e}")
        ptn = {}

    parsed = {
        "title": ptn.get("title"),
        "year": ptn.get("year"),
        "season": ptn.get("season"),
        "episode": ptn.get("episode"),
        "quality": ptn.get("resolution"),
        "excess": ptn.get("excess"),
    }

    # Explicitly support the requested simple series format without changing
    # the existing SxxExx / NxNN parsing. EP-only means Season 1.
    ep_only = _EXPLICIT_EP_ONLY_RE.search(name)
    if ep_only and parsed.get("season") is None and parsed.get("episode") is None:
        parsed["season"] = 1
        parsed["episode"] = int(ep_only.group(1))
        # Keep the title clean even if PTN did not parse EP as excess.
        title_candidate = name
        title_candidate = re.sub(r"\.[a-z0-9]{2,4}$", "", title_candidate, flags=re.IGNORECASE)
        title_candidate = _EXPLICIT_EP_ONLY_RE.sub(" ", title_candidate, count=1)
        title_candidate = re.sub(r"[\s._-]+$", "", title_candidate).strip()
        if title_candidate:
            parsed["title"] = parsed.get("title") or title_candidate

    # Explicitly support the requested simple movie form "Title (Year)".
    # Only use it when no season/episode was detected, so existing series
    # parsing is never overridden.
    if parsed.get("season") is None and parsed.get("episode") is None:
        base_name = re.sub(r"\.[a-z0-9]{2,4}$", "", name, flags=re.IGNORECASE).strip()
        simple_movie = _SIMPLE_MOVIE_YEAR_RE.match(base_name)
        if simple_movie:
            parsed["title"] = simple_movie.group("title").strip()
            parsed["year"] = int(simple_movie.group("year"))

    if _guessit:
        try:
            g = _guessit(name)
            parsed["title"] = parsed["title"] or first(g.get("title"))
            parsed["year"] = parsed["year"] or first(g.get("year"))

            if parsed["season"] is None and parsed["episode"] is None:
                has_anchor = bool(
                    _EXPLICIT_SXXEXX_RE.search(name)
                    or _EXPLICIT_NXNN_RE.search(name)
                    or _EXPLICIT_SEASON_WORD_RE.search(name)
                )
                if has_anchor:
                    g_season = first(g.get("season"))
                    if g_season is not None:
                        try:
                            g_season_int = int(g_season)
                        except (TypeError, ValueError):
                            g_season_int = None
                        if g_season_int is not None and g_season_int > 0:
                            parsed["season"] = g_season_int
                    parsed["episode"] = first(g.get("episode"))

            parsed["quality"] = parsed["quality"] or first(g.get("screen_size"))
        except Exception as e:
            LOGGER.warning(f"GuessIt parsing failed for {name}: {e}")

    try:
        if parsed.get("season") is not None and int(parsed["season"]) == 0:
            parsed["season"] = None
    except (TypeError, ValueError):
        pass

    return parsed


def apply_combined_override(payload: dict, combined: dict) -> None:
    season, start, end = combined["season"], combined["start"], combined["end"]
    payload["season_number"] = COMBINED_SEASON
    payload["episode_number"] = COMBINED_EPISODE_BASE + season
    payload["episode_title"] = f"Season {season} Combined"
    label = "Full" if start is None else f"E{start:02d}-E{end:02d}"
    payload["quality"] = f"{payload.get('quality') or 'HD'} {label}"
    if not payload.get("episode_backdrop"):
        payload["episode_backdrop"] = payload.get("backdrop") or payload.get("poster") or ""


def is_multipart_video(filename: str) -> bool:
    return bool(_MULTIPART_RE.search(filename or ""))


_SEASON_EP_RE = _EXPLICIT_SXXEXX_RE
_RES_WITH_P_RE = re.compile(r"(?i)(?<![\w])(?:240|360|480|576|720|1080|1440|2160|4320)p(?![\w])")
_RES_BARE_TRAILING_RE = re.compile(
    r"(?i)(?<![\w])(?:240|360|480|576|720|1080|1440|2160|4320)(?![\w])"
    r"(?=(?:[\s._-]*(?:\.[a-z0-9]{2,4})?)$)"
)
_QUALITY_TOKEN_RE = re.compile(
    r"(?i)(?:\d{3,4}x\d{3,4}|web-?dl|blu-?ray|bluray|hdtv|hdrip|webrip|bdrip|brrip|"
    r"x264|x265|h\.?264|h\.?265|hevc|avc|aac|"
    r"(?:ddp|dd\+?|e?ac-?3|dts(?:-?hd)?|truehd|atmos)\s*\d?(?:[\s.]\d)?|"
    r"(?<!\d)\d[\s.]\d(?!\d)|10bit|8bit|"
    r"multi(?:\s*audio)?|dual(?:\s*audio)?|esub|subs?|softsubs?|hardsubs?|"
    r"(?<![\w])(?:bd|remux|encode)(?![\w]))"
)
_RELEASE_GROUP_RE = re.compile(r"\[[^\]]{1,40}\]")
_TITLE_ABS_EP_RE = re.compile(r"(?i)^(?P<title>.+?)\s*[-–—]?\s*0*(?P<ep>\d{2,4})\s*$")
_YEAR_RE = re.compile(r"(?:^|[\s._\-(])((?:19|20)\d{2})(?:[\s._\-)]|$)")


def extract_absolute_episode(filename: str, parsed: dict | None = None) -> int | None:
    parsed = parsed or {}
    try:
        if parsed.get("season") is not None and int(parsed.get("season")) > 0:
            return None
    except (TypeError, ValueError):
        if parsed.get("season") is not None:
            return None
    if _SEASON_EP_RE.search(filename or ""):
        return None

    ep = parsed.get("episode")
    if isinstance(ep, list):
        return None
    if ep is not None:
        try:
            return int(ep)
        except (TypeError, ValueError):
            pass

    name = filename or ""
    cleaned = re.sub(r"\.[a-z0-9]{2,4}$", " ", name, flags=re.I)
    cleaned = _RELEASE_GROUP_RE.sub(" ", cleaned)
    cleaned = _RES_WITH_P_RE.sub(" ", cleaned)
    if not _RES_WITH_P_RE.search(name):
        cleaned = _RES_BARE_TRAILING_RE.sub(" ", cleaned)
    cleaned = _QUALITY_TOKEN_RE.sub(" ", cleaned)
    cleaned = _YEAR_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[\s._-]+", " ", cleaned).strip()

    prefixed = re.findall(r"(?i)(?:^|\s)(?:e|ep|episode)\s*0*(\d{1,4})(?:\s|$)", cleaned)
    if prefixed:
        return int(prefixed[-1])

    bare = re.findall(r"(?:^|\s)(0*\d{1,4})(?:\s|$)", cleaned)
    candidates = []
    for x in bare:
        try:
            n = int(x)
        except ValueError:
            continue
        if n >= 1:
            candidates.append(n)
    if not candidates:
        return None
    long = [n for n in candidates if n >= 100]
    if long:
        return long[-1]
    return candidates[-1]


def clean_anime_search_title(title: str, absolute_ep: int | None = None) -> str:
    t = (title or "").strip()
    if not t:
        return t
    t = _RELEASE_GROUP_RE.sub(" ", t)
    t = re.sub(r"[\s._]+", " ", t).strip()
    if absolute_ep is not None:
        t = re.sub(
            rf"(?i)\s*[-–—]?\s*(?:e|ep|episode)?\s*0*{int(absolute_ep)}\s*$",
            "",
            t,
        ).strip()
    if not _SEASON_EP_RE.search(t):
        t2 = re.sub(r"(?i)\s*[-–—]?\s*(?:e|ep|episode)?\s*0*\d{2,4}\s*$", "", t).strip()
        if t2:
            t = t2
    return t or (title or "").strip()


def is_absolute_episode(parsed: dict, filename: str = "") -> bool:
    try:
        if parsed.get("season") is not None and int(parsed.get("season")) > 0:
            return False
    except (TypeError, ValueError):
        if parsed.get("season") is not None:
            return False
    if _SEASON_EP_RE.search(filename or ""):
        return False
    if parsed.get("episode") is not None and not isinstance(parsed.get("episode"), list):
        return True
    return extract_absolute_episode(filename, parsed) is not None

def analyze_metadata_failure(filename: str) -> str:
    if is_multipart_video(filename or ""):
        return (
            "Looks like a multi-part video split (e.g. part1 / cd1) that can't be "
            "combined for streaming."
        )

    split_info = parse_split_info(filename or "")
    parse_target = strip_part_suffix(filename) if split_info else (filename or "")

    try:
        parsed = parse_media_name(parse_target)
    except Exception:
        return (
            "The file name / caption could not be parsed. Give it a clear name like "
            "'Movie Name (2021) 1080p'."
        )

    combined = parse_combined_episodes(parse_target)
    excess = parsed.get("excess")
    if not combined and excess and any("combined" in str(item).lower() for item in excess):
        return (
            "The caption says 'combined' but no season number could be read from it "
            "(e.g. name it 'Show S02 Combined')."
        )

    title = parsed.get("title")
    season = parsed.get("season")
    episode = parsed.get("episode")
    quality = parsed.get("quality")

    if not combined and (isinstance(season, list) or isinstance(episode, list)):
        return (
            "The name spans multiple seasons (e.g. S01-S03) that can't be filed as one entry. "
            "Upload one season per file. Combined episode packs within a single season are fine "
            "when named like 'Show S02 E01-E05' or 'Show S02 Combined'."
        )
    if not quality:
        return (
            "No video quality/resolution was found. Add one to the caption "
            "(e.g. 480p, 720p, 1080p or 2160p)."
        )
    if not title:
        return "No title could be detected. Rename or caption the file with a clear title."

    return (
        "Could not match this title on the configured providers. Fix the title/year in the "
        "caption, or add an IMDb link/id (tt...) or a TMDB link/id, then forward it again."
    )

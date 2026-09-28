"""Genre folders: songs saved with the ➕ button, grouped by detected genre.

Tracks are stored as Lavalink's raw track data, so they can be rebuilt into
wavelink.Playable objects later without searching again.
"""

import asyncio
import json
import logging
import os
import re

import aiohttp

logger = logging.getLogger(__name__)

DATA_FILE = os.getenv("LIBRARY_FILE", "data/folders.json")
ITUNES_URL = "https://itunes.apple.com/search"
DEFAULT_FOLDER = "diğer"

# Checked in order, first match wins ("Turkish Hip-Hop/Rap" -> rap, metal before rock).
GENRE_RULES = [
    (("hip-hop", "rap"), "rap"),
    (("r&b", "soul"), "rnb"),
    (("metal",), "metal"),
    (("rock", "alternative"), "rock"),
    (("electronic", "dance", "house", "techno"), "elektronik"),
    (("arabesque",), "arabesk"),
    (("pop",), "pop"),
    (("jazz",), "jazz"),
    (("classical",), "klasik"),
]
FOLDER_CHOICES = [folder for _, folder in GENRE_RULES] + [DEFAULT_FOLDER]

_lock = asyncio.Lock()


def _clean(text: str) -> str:
    """Strip YouTube noise like "(Official Video)", "VEVO" and " - Topic"."""
    text = re.sub(r"[\(\[].*?[\)\]]", " ", text)
    text = re.sub(
        r"(?i)\b(official|video|audio|lyrics?|klip|hd|4k)\b|vevo| - topic", " ", text
    )
    return " ".join(text.split())


def _norm(text: str) -> str:
    return re.sub(r"\W", "", text.casefold())


def _same_artist(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a in b or b in a)


def genre_to_folder(genre: str | None) -> str:
    if not genre:
        return DEFAULT_FOLDER
    genre = genre.casefold()
    for keywords, folder in GENRE_RULES:
        if any(k in genre for k in keywords):
            return folder
    return DEFAULT_FOLDER


async def _itunes(session: aiohttp.ClientSession, term: str, entity: str) -> list:
    params = {
        "term": term,
        "media": "music",
        "entity": entity,
        "limit": 10,
        "country": "TR",
    }
    async with session.get(ITUNES_URL, params=params) as resp:
        # iTunes answers with text/javascript, so skip the content type check.
        data = await resp.json(content_type=None)
    return data.get("results", [])


async def detect_folder(title: str, author: str) -> str:
    """Guess the genre folder for a track from its title and author."""
    artist, song = _clean(author), _clean(title)
    if " - " in song:
        artist, song = (part.strip() for part in song.split(" - ", 1))
    if not artist:
        return DEFAULT_FOLDER

    genre = None
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for result in await _itunes(session, f"{artist} {song}", "song"):
                if _same_artist(artist, result["artistName"]):
                    genre = result.get("primaryGenreName")
                    break
            if not genre:
                # Song not in the catalog: the artist's genre is still a good guess.
                for result in await _itunes(session, artist, "musicArtist"):
                    if _same_artist(artist, result["artistName"]):
                        genre = result.get("primaryGenreName")
                        break
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"Genre lookup failed for {artist} - {song}: {e}")

    return genre_to_folder(genre)


def load() -> dict[str, list[dict]]:
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _write(data: dict[str, list[dict]]) -> None:
    os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def find_folder(name: str) -> str | None:
    """Case-insensitive lookup of an existing folder name."""
    return next((f for f in load() if f.casefold() == name.casefold()), None)


async def add_track(folder: str, raw: dict) -> None:
    """Put a track into a folder, moving it out of any other folder."""
    async with _lock:
        data = load()
        identifier = raw["info"]["identifier"]
        for songs in data.values():
            songs[:] = [s for s in songs if s["info"]["identifier"] != identifier]
        data.setdefault(folder, []).append(raw)
        _write({name: songs for name, songs in data.items() if songs})


async def remove_track(folder: str, index: int) -> dict | None:
    """Remove the track at 0-based index; returns it, or None if out of range."""
    async with _lock:
        data = load()
        songs = data.get(folder, [])
        if not 0 <= index < len(songs):
            return None
        raw = songs.pop(index)
        _write({name: songs for name, songs in data.items() if songs})
        return raw

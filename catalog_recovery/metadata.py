"""Bounded metadata extraction with explicit failure states."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MAX_TAG_KEYS = 64
MAX_TAG_VALUES = 4
MAX_TAG_VALUE_CHARS = 1024

try:
    from mutagen import File as MutagenFile
    from mutagen import MutagenError
except ImportError:  # pragma: no cover - exercised by packaged dependency checks
    MutagenFile = None

    class MutagenError(Exception):
        """Fallback type used only to produce a useful dependency error."""


class MetadataDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_number: str = ""
    disc_number: str = ""
    date: str = ""
    genre: str = ""
    composer: str = ""
    comment: str = ""
    copyright: str = ""
    isrc: str = ""
    duration_seconds: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bits_per_sample: int | None = None
    container: str = ""
    tags: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_values = value
    else:
        raw_values = [value]
    values: list[str] = []
    for item in raw_values[:MAX_TAG_VALUES]:
        text = str(item).replace("\x00", "").strip()[:MAX_TAG_VALUE_CHARS]
        if text:
            values.append(text)
    return values


def _first(tags: dict[str, list[str]], *keys: str) -> str:
    normalized_keys = tuple(key.casefold() for key in keys)
    for key in normalized_keys:
        values = tags.get(key, [])
        if values:
            return values[0]
    for stored_key, values in tags.items():
        if values and any(stored_key.startswith(f"{key}:") for key in normalized_keys):
            return values[0]
    return ""


def _int_attr(info: Any, name: str) -> int | None:
    value = getattr(info, name, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _float_attr(info: Any, name: str) -> float | None:
    value = getattr(info, name, None)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(numeric, 6)


def extract_metadata(path: Path) -> AudioMetadata:
    """Read tags and technical properties without opening the source for writing."""
    if MutagenFile is None:
        raise MetadataDependencyError(
            "The 'mutagen' package is required for metadata extraction. "
            "Install the application dependencies and retry."
        )

    try:
        media = MutagenFile(path, easy=True)
        technical = MutagenFile(path, easy=False)
    except (MutagenError, OSError, ValueError, EOFError) as exc:
        raise ValueError(f"metadata could not be decoded: {exc}") from exc

    if media is None or technical is None:
        raise ValueError(
            "metadata could not be decoded: unsupported or corrupt audio container"
        )

    normalized_tags: dict[str, list[str]] = {}
    if media.tags:
        for index, (key, value) in enumerate(media.tags.items()):
            if index >= MAX_TAG_KEYS:
                break
            normalized_key = str(key).casefold().strip()[:128]
            if normalized_key:
                normalized_tags[normalized_key] = _string_values(value)

    info = getattr(technical, "info", None)
    return AudioMetadata(
        title=_first(normalized_tags, "title", "tit2"),
        artist=_first(normalized_tags, "artist", "tpe1"),
        album=_first(normalized_tags, "album", "talb"),
        album_artist=_first(normalized_tags, "albumartist", "album artist", "tpe2"),
        track_number=_first(normalized_tags, "tracknumber", "trck"),
        disc_number=_first(normalized_tags, "discnumber", "tpos"),
        date=_first(normalized_tags, "date", "year", "tdrc", "tyer"),
        genre=_first(normalized_tags, "genre", "tcon"),
        composer=_first(normalized_tags, "composer", "tcom"),
        comment=_first(normalized_tags, "comment", "description", "comm"),
        copyright=_first(normalized_tags, "copyright", "tcop"),
        isrc=_first(normalized_tags, "isrc", "tsrc"),
        duration_seconds=_float_attr(info, "length"),
        bitrate=_int_attr(info, "bitrate"),
        sample_rate=_int_attr(info, "sample_rate"),
        channels=_int_attr(info, "channels"),
        bits_per_sample=_int_attr(info, "bits_per_sample"),
        container=type(technical).__name__,
        tags=normalized_tags,
    )

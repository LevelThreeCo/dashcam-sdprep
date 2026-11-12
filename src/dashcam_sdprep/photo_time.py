"""Read when a photo was taken from its EXIF data (JPEG and HEIC, as phones write them).

Only the two tags needed are parsed: DateTimeOriginal (0x9003) and OffsetTimeOriginal (0x9011),
falling back to DateTime (0x0132). HEIC stores the same TIFF block after an "Exif\\0\\0" marker,
so both formats are handled by locating that marker.
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import SdprepError

MAX_BYTES = 64 * 1024 * 1024
EXIF_MARKER = b"Exif\x00\x00"


def _tiff_ascii(tiff: bytes, endian: str, entry: int) -> str:
    _tag, kind, count, value = struct.unpack_from(endian + "HHII", tiff, entry)
    if kind != 2:
        return ""
    raw = tiff[entry + 8: entry + 12] if count <= 4 else tiff[value: value + count]
    return raw.split(b"\0")[0].decode("ascii", "replace").strip()


def _ifd(tiff: bytes, endian: str, offset: int) -> dict[int, int]:
    """Tag -> byte offset of its 12-byte entry."""
    if offset + 2 > len(tiff):
        raise SdprepError("EXIF directory points outside the data")
    count, = struct.unpack_from(endian + "H", tiff, offset)
    entries = {}
    for i in range(count):
        pos = offset + 2 + 12 * i
        if pos + 12 > len(tiff):
            break
        entries[struct.unpack_from(endian + "H", tiff, pos)[0]] = pos
    return entries


def parse_exif(tiff: bytes) -> datetime:
    """Capture time from a TIFF/EXIF block. Aware when the photo recorded its UTC offset."""
    if tiff[:4] == b"MM\x00*":
        endian = ">"
    elif tiff[:4] == b"II*\x00":
        endian = "<"
    else:
        raise SdprepError("EXIF block has no TIFF header")
    ifd0 = _ifd(tiff, endian, struct.unpack_from(endian + "I", tiff, 4)[0])
    stamp = offset = ""
    if 0x8769 in ifd0:
        exif_offset = struct.unpack_from(endian + "I", tiff, ifd0[0x8769] + 8)[0]
        exif = _ifd(tiff, endian, exif_offset)
        if 0x9003 in exif:
            stamp = _tiff_ascii(tiff, endian, exif[0x9003])
        if 0x9011 in exif:
            offset = _tiff_ascii(tiff, endian, exif[0x9011])
    if not stamp and 0x0132 in ifd0:
        stamp = _tiff_ascii(tiff, endian, ifd0[0x0132])
    if not stamp:
        raise SdprepError("photo has no capture time in its EXIF data")
    try:
        when = datetime.strptime(stamp, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        raise SdprepError(f"photo capture time '{stamp}' is not readable") from None
    if offset:
        sign = -1 if offset.startswith("-") else 1
        try:
            hours, minutes = (int(v) for v in offset.lstrip("+-").split(":"))
        except ValueError:
            raise SdprepError(f"photo time zone '{offset}' is not readable") from None
        when = when.replace(tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes)))
    return when


def capture_time(path: Path) -> datetime:
    if not path.is_file():
        raise SdprepError(f"{path} is not a file")
    if path.stat().st_size > MAX_BYTES:
        raise SdprepError(f"{path.name} is larger than {MAX_BYTES // (1024 * 1024)} MB; is it a photo?")
    data = path.read_bytes()
    # The marker can also appear in HEIC's item index ("Exif" item type); use the one that is
    # actually followed by a TIFF header.
    pos = data.find(EXIF_MARKER)
    while pos >= 0:
        start = pos + len(EXIF_MARKER)
        if data[start:start + 4] in (b"MM\x00*", b"II*\x00"):
            return parse_exif(data[start:])
        pos = data.find(EXIF_MARKER, pos + 1)
    raise SdprepError(f"{path.name} has no EXIF data (screenshots and edited copies often lose it)")

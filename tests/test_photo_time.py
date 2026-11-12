from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone

import pytest

from dashcam_sdprep import photo_time
from dashcam_sdprep.errors import SdprepError


def tiff(stamp: str, offset: str = "", endian: str = ">") -> bytes:
    """Minimal TIFF: IFD0 with an ExifIFD pointer; ExifIFD with DateTimeOriginal (+ OffsetTimeOriginal)."""
    head = (b"MM\x00*" if endian == ">" else b"II*\x00") + struct.pack(endian + "I", 8)
    exif_entries = [(0x9003, stamp.encode() + b"\0")] + ([(0x9011, offset.encode() + b"\0")] if offset else [])
    ifd0_size = 2 + 12 + 4
    exif_at = 8 + ifd0_size
    exif_size = 2 + 12 * len(exif_entries) + 4
    data_at = exif_at + exif_size
    ifd0 = struct.pack(endian + "H", 1) + struct.pack(endian + "HHII", 0x8769, 4, 1, exif_at) + bytes(4)
    exif, blobs = struct.pack(endian + "H", len(exif_entries)), b""
    for tag, blob in exif_entries:
        exif += struct.pack(endian + "HHII", tag, 2, len(blob), data_at + len(blobs))
        blobs += blob
    return head + ifd0 + exif + bytes(4) + blobs


def test_jpeg_with_offset(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe1\x00\x00" + b"Exif\x00\x00" + tiff("2026:11:12 09:27:52", "+09:00"))
    assert photo_time.capture_time(photo) == datetime(2026, 11, 12, 9, 27, 52, tzinfo=timezone(timedelta(hours=9)))


def test_little_endian_negative_offset(tmp_path):
    photo = tmp_path / "b.jpg"
    photo.write_bytes(b"\xff\xd8" + b"Exif\x00\x00" + tiff("2026:11:12 20:00:00", "-05:00", endian="<"))
    assert photo_time.capture_time(photo).utcoffset() == timedelta(hours=-5)


def test_heic_skips_item_index_marker_and_reads_the_real_block(tmp_path):
    # HEIC has an "Exif" item type in its index before the actual EXIF payload.
    photo = tmp_path / "c.heic"
    photo.write_bytes(b"ftypheic" + b"infe" + b"Exif\x00\x00\x00junk" + b"\x00\x00\x00\x06"
                      + b"Exif\x00\x00" + tiff("2026:11:11 22:39:59", "+09:00"))
    assert photo_time.capture_time(photo).hour == 22


def test_no_offset_gives_naive_time(tmp_path):
    photo = tmp_path / "d.jpg"
    photo.write_bytes(b"Exif\x00\x00" + tiff("2026:11:12 09:00:00"))
    assert photo_time.capture_time(photo).tzinfo is None


def test_missing_exif_is_explained(tmp_path):
    photo = tmp_path / "e.png"
    photo.write_bytes(b"\x89PNG" + bytes(100))
    with pytest.raises(SdprepError, match="no EXIF"):
        photo_time.capture_time(photo)

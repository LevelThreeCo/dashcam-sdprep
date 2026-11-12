from __future__ import annotations

import struct
from dataclasses import dataclass, field

import pytest

from dashcam_sdprep import profile

SECTOR = 512


@dataclass
class SparseCard:
    """A card image holding only the sectors a test writes; everything else reads as zeros."""

    sectors: dict[int, bytes] = field(default_factory=dict)

    def write(self, lba: int, data: bytes) -> None:
        for i in range(0, len(data), SECTOR):
            self.sectors[lba + i // SECTOR] = data[i:i + SECTOR].ljust(SECTOR, b"\0")

    def read(self, lba: int, count: int) -> bytes:
        return b"".join(self.sectors.get(lba + i, bytes(SECTOR)) for i in range(count))


def mbr(part_type: int = 0x0C, start: int = 8192, sectors: int = 62325760) -> bytes:
    data = bytearray(SECTOR)
    data[446 + 4] = part_type
    struct.pack_into("<II", data, 446 + 8, start, sectors)
    data[510:512] = b"\x55\xaa"
    return bytes(data)


def fat32_boot(total: int = 62325760, spc: int = 128, bps: int = 512, fs_type: bytes = b"FAT32   ",
               reserved: int = 32, nfats: int = 2, fat_size: int = 3804, root_cluster: int = 2) -> bytes:
    data = bytearray(SECTOR)
    data[0:3] = b"\xeb\x58\x90"
    struct.pack_into("<HBHBHH", data, 0x0B, bps, spc, reserved, nfats, 0, 0)
    struct.pack_into("<H", data, 0x16, 0)
    struct.pack_into("<IIII", data, 0x20, total, fat_size, 0, root_cluster)
    data[0x52:0x5A] = fs_type
    data[510:512] = b"\x55\xaa"
    return bytes(data)


def dir_entry(short: bytes, attr: int = 0x20) -> bytes:
    entry = bytearray(32)
    entry[0:11] = short
    entry[11] = attr
    return bytes(entry)


def lfn_entries(name: str, short: bytes) -> list[bytes]:
    """VFAT long-name fragments for `name`, in on-disk order (last fragment first)."""
    units = name.encode("utf-16-le") + b"\0\0"
    chunks = [units[i:i + 26] for i in range(0, len(units), 26)]
    checksum = 0
    for byte in short:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 0xFF
    out = []
    for seq, chunk in enumerate(chunks, 1):
        chunk = chunk.ljust(26, b"\xff")
        e = bytearray(32)
        e[0] = seq | (0x40 if seq == len(chunks) else 0)
        e[1:11], e[11], e[13], e[14:26], e[28:32] = chunk[0:10], 0x0F, checksum, chunk[10:22], chunk[22:26]
        out.append(bytes(e))
    return list(reversed(out))


@pytest.fixture
def vg701() -> profile.Profile:
    return profile.load("vugera-vg-701v3")

from __future__ import annotations

import struct

import pytest

from conftest import SparseCard, dir_entry, fat32_boot, lfn_entries, mbr
from dashcam_sdprep import fat
from dashcam_sdprep.errors import SdprepError


def test_card_formatted_like_the_camera_passes(vg701):
    assert fat.check(vg701, mbr(), fat32_boot()) == []


def test_sd_formatter_32k_clusters_fail_like_the_camera(vg701):
    # What SD Formatter / Disk Utility produce on a 32 GB card: FAT32 but 64 sectors per cluster.
    failures = fat.check(vg701, mbr(), fat32_boot(spc=64))
    assert any("requires 128" in f for f in failures)
    assert any("65536" in f for f in failures)


def test_exfat_card_fails(vg701):
    boot = bytearray(fat32_boot())
    boot[0x52:0x5A] = b"\0" * 8
    boot[3:11] = b"EXFAT   "
    failures = fat.check(vg701, mbr(part_type=0x07), bytes(boot))
    assert any("0x07" in f for f in failures)
    assert any("FAT32" in f for f in failures)


def test_fat16_layout_fails(vg701):
    boot = bytearray(fat32_boot())
    struct.pack_into("<H", boot, 0x16, 200)  # FAT16 fat size field set
    assert any("FAT32 layout" in f for f in fat.check(vg701, mbr(), bytes(boot)))


def test_nonzero_reserved_bpb_fails(vg701):
    boot = bytearray(fat32_boot())
    boot[0x3A] = 1
    assert any("0x34-0x3F" in f for f in fat.check(vg701, mbr(), bytes(boot)))


def test_filesystem_larger_than_partition_fails(vg701):
    assert any("larger than its partition" in f for f in fat.check(vg701, mbr(sectors=1000), fat32_boot()))


def test_missing_partition_raises(vg701):
    empty = bytearray(mbr())
    empty[446:462] = bytes(16)
    with pytest.raises(SdprepError, match="partition 1 is empty"):
        fat.check(vg701, bytes(empty), fat32_boot())


def test_superfloppy_without_mbr_raises(vg701):
    with pytest.raises(SdprepError, match="no MBR"):
        fat.check(vg701, bytes(512), fat32_boot())


@pytest.mark.parametrize("total,expected", [
    (0x1FFFF, 1), (0x20000, 2), (0x7FFFF, 4), (0x1FFFFF, 8), (0x3FFFFF, 32), (0x7FFFFF, 64),
    (0x800000, 128), (62325760, 128), (500_000_000, 128),
])
def test_cluster_table_matches_firmware(vg701, total, expected):
    assert vg701.required_sectors_per_cluster(total) == expected


def _card_with_root(entries: list[bytes]) -> tuple[SparseCard, fat.Partition, bytes]:
    card = SparseCard()
    boot = fat32_boot()
    part = fat.Partition(1, 0x0C, 8192, 62325760)
    data_start = part.start + 32 + 2 * 3804
    card.write(data_start, b"".join(entries))
    fat_sector = bytearray(512)
    struct.pack_into("<I", fat_sector, 2 * 4, 0x0FFFFFFF)  # root is a single cluster
    card.write(part.start + 32, bytes(fat_sector))
    return card, part, boot


def test_root_listing_empty_and_skips_label():
    card, part, boot = _card_with_root([dir_entry(b"DASHCAM    ", attr=0x08)])
    assert fat.root_entries(card.read, part, boot) == []


def test_root_listing_decodes_long_names_and_skips_deleted():
    short_rec = b"REC0_2~1AVI"
    deleted = bytearray(dir_entry(b"OLD     AVI"))
    deleted[0] = 0xE5
    entries = [
        *lfn_entries(".fseventsd", b"FSEVEN~1   "), dir_entry(b"FSEVEN~1   ", attr=0x10),
        bytes(deleted),
        *lfn_entries("REC0_20261111_210000.avi", short_rec), dir_entry(short_rec),
        dir_entry(b"SETUP   INI"),
    ]
    card, part, boot = _card_with_root(entries)
    assert fat.root_entries(card.read, part, boot) == [".fseventsd", "REC0_20261111_210000.avi", "SETUP.INI"]


def test_root_cluster_loop_is_bounded():
    card, part, boot = _card_with_root([dir_entry(b"A       TXT")] * 16 * 128)  # fills the cluster, no terminator
    fat_sector = bytearray(512)
    struct.pack_into("<I", fat_sector, 2 * 4, 2)  # cluster 2 points to itself
    card.write(part.start + 32, bytes(fat_sector))
    with pytest.raises(SdprepError, match="does not terminate"):
        fat.root_entries(card.read, part, boot)

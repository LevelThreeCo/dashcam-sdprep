"""MBR and FAT32 parsing plus profile checks. Pure functions over bytes, no disk access."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .errors import SdprepError
from .profile import Profile

SECTOR = 512  # MBR and boot sectors are addressed in 512-byte units
SIGNATURE = b"\x55\xaa"

ReadSectors = Callable[[int, int], bytes]  # (lba, count) -> bytes


@dataclass(frozen=True)
class Partition:
    index: int
    type_id: int
    start: int
    sectors: int


@dataclass(frozen=True)
class BootSector:
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    num_fats: int
    root_entries: int
    total_sectors_16: int
    fat_size_16: int
    total_sectors_32: int
    fat_size_32: int
    root_cluster: int
    fs_type: bytes
    reserved_bpb: bytes
    signature: bytes

    @classmethod
    def parse(cls, data: bytes) -> BootSector:
        if len(data) < SECTOR:
            raise SdprepError("boot sector read was short")
        bps, spc, reserved, nfats, root_entries, tot16 = struct.unpack_from("<HBHBHH", data, 0x0B)
        fat16 = struct.unpack_from("<H", data, 0x16)[0]
        tot32, fat32, _flags, root_cluster = struct.unpack_from("<IIII", data, 0x20)
        return cls(bps, spc, reserved, nfats, root_entries, tot16, fat16, tot32, fat32, root_cluster,
                   bytes(data[0x52:0x5A]), bytes(data[0x34:0x40]), bytes(data[510:512]))

    @property
    def total_sectors(self) -> int:
        # FAT32 stores the count in the 32-bit field; FAT12/16 may use either.
        if self.fat_size_16 == 0:
            return self.total_sectors_32
        return self.total_sectors_16 or self.total_sectors_32


def parse_mbr(mbr: bytes) -> list[Partition]:
    if len(mbr) < SECTOR:
        raise SdprepError("MBR read was short")
    if mbr[510:512] != SIGNATURE:
        raise SdprepError("sector 0 has no 0x55AA signature, so there is no MBR partition table")
    parts = []
    for i in range(4):
        entry = mbr[446 + 16 * i: 462 + 16 * i]
        start, sectors = struct.unpack_from("<II", entry, 8)
        if entry[4]:
            parts.append(Partition(i + 1, entry[4], start, sectors))
    return parts


def target_partition(profile: Profile, mbr: bytes) -> Partition:
    for part in parse_mbr(mbr):
        if part.index == profile.partition_index:
            return part
    raise SdprepError(f"partition {profile.partition_index} is empty; the camera mounts that partition")


def check(profile: Profile, mbr: bytes, boot_data: bytes) -> list[str]:
    """Every reason this profile's camera would reject the card. Empty means it passes."""
    failures: list[str] = []
    part = target_partition(profile, mbr)
    if part.type_id not in profile.partition_types:
        failures.append(f"partition {part.index} type 0x{part.type_id:02X} is not one the camera accepts "
                        f"({', '.join(f'0x{t:02X}' for t in sorted(profile.partition_types))}); exFAT/NTFS is 0x07")
    boot = BootSector.parse(boot_data)

    if boot.bytes_per_sector != profile.bytes_per_sector:
        failures.append(f"bytes/sector is {boot.bytes_per_sector}, camera requires {profile.bytes_per_sector}")
    if boot.signature != SIGNATURE:
        failures.append("filesystem boot sector lacks the 0x55AA signature")
    need = profile.required_sectors_per_cluster(boot.total_sectors)
    if boot.sectors_per_cluster != need:
        failures.append(f"sectors/cluster is {boot.sectors_per_cluster}, camera requires {need} "
                        f"for a {boot.total_sectors}-sector filesystem")
    cluster = boot.bytes_per_sector * boot.sectors_per_cluster
    if profile.cluster_bytes is not None and cluster != profile.cluster_bytes:
        failures.append(f"cluster is {cluster} bytes, camera requires {profile.cluster_bytes}")

    if profile.filesystem == "FAT32":
        if not boot.fs_type.startswith(b"FAT32"):
            failures.append(f"filesystem type field is {boot.fs_type!r}, camera requires FAT32 (exFAT cards fail here)")
        fat32_layout = (boot.fat_size_16 == 0 and boot.fat_size_32 != 0 and boot.total_sectors_16 == 0
                        and boot.total_sectors_32 != 0 and boot.root_entries == 0)
        if not fat32_layout:
            failures.append("boot sector is not a FAT32 layout (FAT16/FAT12 fields are set)")
        if any(boot.reserved_bpb):
            failures.append("boot sector reserved bytes 0x34-0x3F are not zero")

    # Not a firmware rule: a filesystem that disagrees with its partition is broken on any device.
    if boot.total_sectors > part.sectors:
        failures.append(f"filesystem ({boot.total_sectors} sectors) is larger than its partition ({part.sectors})")
    return failures


def root_entries(read: ReadSectors, part: Partition, boot_data: bytes) -> list[str]:
    """Names in a FAT32 root directory, long names decoded, volume label and deleted entries skipped."""
    boot = BootSector.parse(boot_data)
    if boot.bytes_per_sector != SECTOR:
        raise SdprepError("root directory listing supports 512-byte sectors only")
    fat_start = part.start + boot.reserved_sectors
    data_start = fat_start + boot.num_fats * boot.fat_size_32
    names: list[str] = []
    long_name: list[bytes] = []  # VFAT fragments precede their 8.3 entry, last fragment first
    cluster = boot.root_cluster
    for _ in range(4096):  # bound the walk (4096 clusters is millions of entries) so a corrupt chain cannot loop forever
        if cluster < 2:
            raise SdprepError(f"root directory points at invalid cluster {cluster}")
        data = read(data_start + (cluster - 2) * boot.sectors_per_cluster, boot.sectors_per_cluster)
        for off in range(0, len(data), 32):
            entry = data[off: off + 32]
            if entry[0] == 0x00:
                return names
            attr = entry[11]
            if entry[0] == 0xE5:
                long_name.clear()
            elif attr == 0x0F:
                long_name.insert(0, entry[1:11] + entry[14:26] + entry[28:32])
            elif attr & 0x08:
                long_name.clear()
            else:
                if long_name:
                    name = b"".join(long_name).decode("utf-16-le", "replace").split("\0")[0]
                else:
                    base = entry[:8].decode("latin-1").rstrip()
                    ext = entry[8:11].decode("latin-1").rstrip()
                    name = f"{base}.{ext}" if ext else base
                names.append(name)
                long_name.clear()
        fat_sector = read(fat_start + cluster * 4 // SECTOR, 1)
        cluster = struct.unpack_from("<I", fat_sector, cluster * 4 % SECTOR)[0] & 0x0FFFFFFF
        if cluster >= 0x0FFFFFF8:
            return names
    raise SdprepError("root directory cluster chain does not terminate")

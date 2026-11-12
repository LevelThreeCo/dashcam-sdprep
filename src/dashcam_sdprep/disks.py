"""Platform disk backends (macOS, Linux) and the shared format/verify flow built on them.

Safety model: a backend only accepts a whole disk on an SD or USB bus that the OS reports as
removable, never the disk holding the running system. Disk images and loop devices are refused
unless the caller passes allow_test_device, which exists for the integration tests.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fat
from .errors import SdprepError
from .profile import Profile


def run(cmd: list[str], stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SdprepError(f"required command '{cmd[0]}' is not installed") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise SdprepError(f"`{' '.join(cmd)}` failed ({proc.returncode}): {detail}")
    return proc.stdout


@dataclass(frozen=True)
class Disk:
    ident: str        # what the user typed and sees, e.g. disk4 or sdb
    device: str       # whole-disk node used for raw reads and writes
    size: int
    description: str
    bus: str


@dataclass(frozen=True)
class Report:
    partition: fat.Partition
    root: list[str]


class Backend:
    """Interface each platform implements."""

    def list_disks(self) -> list[Disk]:
        raise NotImplementedError

    def resolve(self, ident: str, allow_test_device: bool) -> Disk:
        raise NotImplementedError

    def unmount(self, disk: Disk) -> None:
        raise NotImplementedError

    def create_partition(self, disk: Disk, label: str) -> None:
        """Write a fresh MBR with one FAT32 partition spanning the card."""
        raise NotImplementedError

    def partition_device(self, disk: Disk, index: int) -> str:
        raise NotImplementedError

    def make_filesystem(self, disk: Disk, part: fat.Partition, sectors_per_cluster: int, label: str) -> None:
        raise NotImplementedError

    def release(self, disk: Disk) -> None:
        """Leave the card unmounted and safe to remove, so the OS writes nothing to it."""
        raise NotImplementedError

    def mount_point(self, disk: Disk) -> Path | None:
        """Where partition 1 is mounted, or None."""
        raise NotImplementedError

    def mount_readonly(self, disk: Disk) -> Path:
        """Mount partition 1 read-only (so importing cannot change the card) and return where."""
        raise NotImplementedError

    def mounted_card(self, disk: Disk) -> Path:
        return self.mount_point(disk) or self.mount_readonly(disk)


def check_size(profile: Profile, ident: str, size: int) -> None:
    if not profile.min_card_bytes <= size <= profile.max_card_bytes:
        raise SdprepError(f"{ident} is {size / 1e9:.1f} GB; {profile.name} supports "
                          f"{profile.min_card_bytes / 1e9:.1f} GB to {profile.max_card_bytes / 1e9:.0f} GB")


def read_sectors(device: str, lba: int, count: int) -> bytes:
    with open(device, "rb", buffering=0) as fh:
        fh.seek(lba * fat.SECTOR)
        data = fh.read(count * fat.SECTOR)
    if len(data) != count * fat.SECTOR:
        raise SdprepError(f"short read at sector {lba} of {device}")
    return data


def verify(backend: Backend, disk: Disk, profile: Profile) -> Report:
    backend.unmount(disk)
    mbr = read_sectors(disk.device, 0, 1)
    part = fat.target_partition(profile, mbr)
    boot = read_sectors(disk.device, part.start, 1)
    failures = fat.check(profile, mbr, boot)
    if failures:
        raise SdprepError(f"{profile.name} would reject this card:\n  - " + "\n  - ".join(failures))
    root = fat.root_entries(lambda lba, n: read_sectors(disk.device, lba, n), part, boot)
    return Report(part, root)


def format_card(backend: Backend, disk: Disk, profile: Profile, label: str) -> Report:
    if profile.partition_index != 1:
        raise SdprepError("formatting is implemented for cameras that use partition 1 only")
    check_size(profile, disk.ident, disk.size)
    backend.unmount(disk)
    backend.create_partition(disk, label)
    backend.unmount(disk)
    part = fat.target_partition(profile, read_sectors(disk.device, 0, 1))
    spc = profile.required_sectors_per_cluster(part.sectors)
    if profile.cluster_bytes is not None and spc * profile.bytes_per_sector != profile.cluster_bytes:
        raise SdprepError(f"{profile.name}: a {part.sectors}-sector partition needs {spc} sectors/cluster, "
                          f"which contradicts the required {profile.cluster_bytes}-byte cluster")
    backend.make_filesystem(disk, part, spc, label)
    report = verify(backend, disk, profile)
    if report.root:
        raise SdprepError(f"fresh filesystem is not empty: {report.root}")
    backend.release(disk)
    return report


# ---------------------------------------------------------------------------------------- macOS


class MacBackend(Backend):
    """diskutil for partitioning, newfs_msdos for the filesystem (diskutil cannot set cluster size)."""

    BUSES = frozenset({"Secure Digital", "USB"})

    def _info(self, target: str) -> dict[str, Any]:
        return plistlib.loads(run(["diskutil", "info", "-plist", target]).encode())

    def list_disks(self) -> list[Disk]:
        # Built-in SD readers report as "internal", so filter on bus, not location.
        listing = plistlib.loads(run(["diskutil", "list", "-plist", "physical"]).encode())
        disks = []
        for entry in listing.get("AllDisksAndPartitions", []):
            info = self._info(entry["DeviceIdentifier"])
            if info.get("BusProtocol") in self.BUSES:
                disks.append(self._disk(info, entry))
        return disks

    def _disk(self, info: dict[str, Any], entry: dict[str, Any] | None = None) -> Disk:
        ident = str(info["DeviceIdentifier"])
        vols = ", ".join(p["VolumeName"] for p in (entry or {}).get("Partitions", []) if p.get("VolumeName"))
        name = str(info.get("MediaName") or info.get("IORegistryEntryName") or "?")
        return Disk(ident, f"/dev/r{ident}", int(info["TotalSize"]), f"{name} {vols}".strip(),
                    str(info.get("BusProtocol", "")))

    def resolve(self, ident: str, allow_test_device: bool) -> Disk:
        if not re.fullmatch(r"(/dev/)?disk\d+", ident):
            raise SdprepError(f"'{ident}' is not a whole-disk identifier like disk4")
        try:
            info = self._info(ident)
        except SdprepError:
            raise SdprepError(f"no disk '{ident}' is attached. Insert the card and run `list`") from None
        disk = self._disk(info)
        if not info.get("WholeDisk"):
            raise SdprepError(f"{disk.ident} is a partition; pass the whole disk")
        if disk.ident == str(self._info("/").get("ParentWholeDisk", "")) or info.get("APFSPhysicalStores"):
            raise SdprepError(f"{disk.ident} holds the running system or an APFS container; refusing")
        removable = bool(info.get("RemovableMediaOrExternalDevice") or info.get("Removable") or info.get("Ejectable"))
        if disk.bus == "Disk Image":
            if not allow_test_device:
                raise SdprepError(f"{disk.ident} is a disk image; refusing")
        elif disk.bus not in self.BUSES or not removable:
            raise SdprepError(f"{disk.ident} is on bus '{disk.bus}', removable={removable}; only SD/USB cards are allowed")
        return disk

    def unmount(self, disk: Disk) -> None:
        run(["diskutil", "unmountDisk", "force", disk.ident])

    def create_partition(self, disk: Disk, label: str) -> None:
        run(["diskutil", "partitionDisk", disk.ident, "1", "MBR", "MS-DOS FAT32", label, "R"])

    def partition_device(self, disk: Disk, index: int) -> str:
        return f"{disk.device}s{index}"

    def make_filesystem(self, disk: Disk, part: fat.Partition, sectors_per_cluster: int, label: str) -> None:
        run(["newfs_msdos", "-F", "32", "-S", str(fat.SECTOR), "-c", str(sectors_per_cluster),
             "-o", str(part.start), "-v", label, self.partition_device(disk, part.index)])

    def release(self, disk: Disk) -> None:
        # Mounting would let macOS write .fseventsd and .Spotlight-V100; eject without mounting.
        run(["diskutil", "eject", disk.ident])

    def mount_point(self, disk: Disk) -> Path | None:
        try:
            mp = str(self._info(f"{disk.ident}s1").get("MountPoint") or "")
        except SdprepError:
            return None
        return Path(mp) if mp else None

    def mount_readonly(self, disk: Disk) -> Path:
        run(["diskutil", "mount", "readOnly", f"{disk.ident}s1"])
        mp = self.mount_point(disk)
        if mp is None:
            raise SdprepError(f"{disk.ident}s1 did not mount")
        return mp


# ---------------------------------------------------------------------------------------- Linux


def _flag(value: Any) -> bool:
    """lsblk prints booleans as true/false or "1"/"0" depending on util-linux version."""
    return value in (True, 1, "1", "true")


class LinuxBackend(Backend):
    """lsblk for discovery, sfdisk for the MBR, mkfs.fat (dosfstools) for the filesystem."""

    BUSES = frozenset({"usb", "mmc"})
    ALIGN = 8192  # first partition at 4 MiB, a multiple of every SD allocation unit size

    def _lsblk(self, *args: str) -> list[dict[str, Any]]:
        out = run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,TRAN,RM,HOTPLUG,MODEL,MOUNTPOINT,PKNAME", *args])
        return json.loads(out).get("blockdevices", [])

    def _disk(self, dev: dict[str, Any]) -> Disk:
        bus = dev.get("tran") or ("mmc" if str(dev["name"]).startswith("mmcblk") else "")
        return Disk(str(dev["name"]), str(dev["path"]), int(dev["size"]), str(dev.get("model") or "").strip(), bus)

    def _system_disks(self) -> set[str]:
        """Whole disks backing /, /boot, /usr, /var or swap; never formattable."""
        held: set[str] = set()
        for dev in self._lsblk():
            stack = [(dev, dev["name"])]
            while stack:
                node, top = stack.pop()
                mp = node.get("mountpoint") or ""
                if mp in ("/", "/boot", "/boot/efi", "/usr", "/var", "[SWAP]"):
                    held.add(top)
                stack.extend((child, top) for child in node.get("children", []))
        return held

    def list_disks(self) -> list[Disk]:
        system = self._system_disks()
        return [self._disk(d) for d in self._lsblk("-d")
                if d.get("type") == "disk" and self._disk(d).bus in self.BUSES
                and (_flag(d.get("rm")) or _flag(d.get("hotplug"))) and d["name"] not in system]

    def resolve(self, ident: str, allow_test_device: bool) -> Disk:
        name = ident[len("/dev/"):] if ident.startswith("/dev/") else ident
        if not re.fullmatch(r"(sd[a-z]+|mmcblk\d+|loop\d+)", name):
            raise SdprepError(f"'{ident}' is not a whole-disk name like sdb or mmcblk0")
        try:
            devs = self._lsblk("-d", f"/dev/{name}")
        except SdprepError:
            raise SdprepError(f"no disk '{ident}' is attached. Insert the card and run `list`") from None
        dev = devs[0]
        disk = self._disk(dev)
        if name in self._system_disks():
            raise SdprepError(f"{name} holds the running system; refusing")
        if dev.get("type") == "loop":
            if not allow_test_device:
                raise SdprepError(f"{name} is a loop device; refusing")
        elif dev.get("type") != "disk":
            raise SdprepError(f"{name} is a {dev.get('type')}, not a whole disk")
        elif disk.bus not in self.BUSES or not (_flag(dev.get("rm")) or _flag(dev.get("hotplug"))):
            raise SdprepError(f"{name} is on bus '{disk.bus or 'unknown'}' and not removable; "
                              "only SD/USB cards are allowed")
        return disk

    def unmount(self, disk: Disk) -> None:
        for dev in self._lsblk(disk.device):
            for child in dev.get("children", []):
                if child.get("mountpoint"):
                    run(["umount", str(child["path"])])

    def create_partition(self, disk: Disk, label: str) -> None:
        run(["sfdisk", "--wipe", "always", "--wipe-partitions", "always", disk.device],
            stdin=f"label: dos\nstart={self.ALIGN}, type=c\n")
        self._settle(disk)

    def _settle(self, disk: Disk) -> None:
        try:
            subprocess.run(["udevadm", "settle"], capture_output=True)
        except FileNotFoundError:
            pass  # no udev (minimal systems); the poll below still waits for the node
        node = self.partition_device(disk, 1)
        sysfs = f"/sys/class/block/{os.path.basename(node)}/dev"
        for _ in range(100):
            if self._node_current(node, sysfs):
                return
            time.sleep(0.1)
        raise SdprepError(f"partition node {node} did not appear after writing the partition table")

    @staticmethod
    def _node_current(node: str, sysfs: str) -> bool:
        """The node exists and, where sysfs says which device it should be, points at that device.

        Rewriting a table can give the partition a new device number; a stale node left behind
        would then open nothing (or the wrong thing).
        """
        try:
            rdev = os.stat(node).st_rdev
        except FileNotFoundError:
            return False
        try:
            with open(sysfs, encoding="ascii") as fh:
                major, minor = (int(v) for v in fh.read().strip().split(":"))
        except FileNotFoundError:
            return True  # no sysfs entry to compare against; existence is all we can check
        return os.major(rdev) == major and os.minor(rdev) == minor

    def partition_device(self, disk: Disk, index: int) -> str:
        sep = "p" if disk.device[-1].isdigit() else ""  # mmcblk0p1 / loop0p1, but sdb1
        return f"{disk.device}{sep}{index}"

    def make_filesystem(self, disk: Disk, part: fat.Partition, sectors_per_cluster: int, label: str) -> None:
        run(["mkfs.fat", "-F", "32", "-S", str(fat.SECTOR), "-s", str(sectors_per_cluster),
             "-h", str(part.start), "-n", label, self.partition_device(disk, part.index)])

    def release(self, disk: Disk) -> None:
        run(["sync"])
        self.unmount(disk)

    def mount_point(self, disk: Disk) -> Path | None:
        for dev in self._lsblk(disk.device):
            for child in dev.get("children", []):
                if child.get("path") == self.partition_device(disk, 1) and child.get("mountpoint"):
                    return Path(child["mountpoint"])
        return None

    def mount_readonly(self, disk: Disk) -> Path:
        # udisks lets a desktop user mount removable media without root.
        run(["udisksctl", "mount", "--no-user-interaction", "-o", "ro", "-b", self.partition_device(disk, 1)])
        mp = self.mount_point(disk)
        if mp is None:
            raise SdprepError(f"{self.partition_device(disk, 1)} did not mount")
        return mp


def backend() -> Backend:
    if sys.platform == "darwin":
        return MacBackend()
    if sys.platform.startswith("linux"):
        return LinuxBackend()
    raise SdprepError(f"disk commands support macOS and Linux, not {sys.platform}; `timefix` works everywhere")

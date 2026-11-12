"""Camera profiles: what a specific dashcam firmware requires of its SD card.

A profile is a JSON file in the `profiles/` package directory. Every rule in it should be
backed by evidence (firmware analysis or vendor documentation) linked from `evidence`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from .errors import SdprepError

SUPPORTED_FILESYSTEMS = frozenset({"FAT32"})


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    firmware: tuple[str, ...]
    evidence: str
    partition_index: int
    partition_types: frozenset[int]
    filesystem: str
    bytes_per_sector: int
    cluster_table: tuple[tuple[int, int], ...]
    cluster_default: int
    cluster_bytes: int | None
    min_card_bytes: int
    max_card_bytes: int
    recording_pattern: re.Pattern[str]

    def required_sectors_per_cluster(self, total_sectors: int) -> int:
        """Sectors per cluster the firmware demands for a filesystem of this many sectors."""
        for limit, spc in self.cluster_table:
            if total_sectors < limit:
                return spc
        return self.cluster_default


def _field(data: dict[str, Any], key: str, kind: type) -> Any:
    if key not in data:
        raise SdprepError(f"profile is missing '{key}'")
    value = data[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise SdprepError(f"profile field '{key}' must be {kind.__name__}, got {value!r}")
    return value


def parse(data: dict[str, Any]) -> Profile:
    """Build a Profile from decoded JSON, rejecting anything malformed."""
    part = _field(data, "partition", dict)
    fs = _field(data, "filesystem", dict)
    rec = _field(data, "recordings", dict)

    fs_type = _field(fs, "type", str)
    if fs_type not in SUPPORTED_FILESYSTEMS:
        raise SdprepError(f"filesystem '{fs_type}' is not supported (supported: {sorted(SUPPORTED_FILESYSTEMS)})")
    if _field(part, "scheme", str) != "mbr":
        raise SdprepError("only MBR partition tables are supported")

    table = []
    for row in _field(fs, "cluster_table", list):
        if not (isinstance(row, list) and len(row) == 2 and all(isinstance(v, int) for v in row)):
            raise SdprepError(f"cluster_table rows must be [max_total_sectors_exclusive, sectors_per_cluster], got {row!r}")
        table.append((row[0], row[1]))
    if [r[0] for r in table] != sorted(r[0] for r in table):
        raise SdprepError("cluster_table limits must be ascending")

    cluster_bytes = fs.get("cluster_bytes")
    if cluster_bytes is not None and not isinstance(cluster_bytes, int):
        raise SdprepError("cluster_bytes must be an integer or absent")

    try:
        pattern = re.compile(_field(rec, "pattern", str), re.IGNORECASE)
    except re.error as exc:
        raise SdprepError(f"recordings.pattern is not a valid regex: {exc}") from exc
    missing = {"date", "time"} - set(pattern.groupindex)
    if missing:
        raise SdprepError(f"recordings.pattern must define named groups {sorted(missing)}")

    return Profile(
        id=_field(data, "id", str),
        name=_field(data, "name", str),
        firmware=tuple(_field(data, "firmware", list)),
        evidence=_field(data, "evidence", str),
        partition_index=_field(part, "index", int),
        partition_types=frozenset(_field(part, "types", list)),
        filesystem=fs_type,
        bytes_per_sector=_field(fs, "bytes_per_sector", int),
        cluster_table=tuple(table),
        cluster_default=_field(fs, "cluster_default", int),
        cluster_bytes=cluster_bytes,
        min_card_bytes=_field(fs, "min_card_bytes", int),
        max_card_bytes=_field(fs, "max_card_bytes", int),
        recording_pattern=pattern,
    )


def available() -> list[str]:
    files = resources.files(__package__).joinpath("profiles")
    return sorted(p.name[: -len(".json")] for p in files.iterdir() if p.name.endswith(".json"))


def load(profile_id: str) -> Profile:
    if profile_id not in available():
        raise SdprepError(f"unknown profile '{profile_id}'. Available: {', '.join(available())}")
    text = resources.files(__package__).joinpath("profiles", f"{profile_id}.json").read_text(encoding="utf-8")
    profile = parse(json.loads(text))
    if profile.id != profile_id:
        raise SdprepError(f"profile file {profile_id}.json declares id '{profile.id}'")
    return profile

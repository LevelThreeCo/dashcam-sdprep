"""Correct recording timestamps from a dashcam whose clock is wrong, without touching the originals.

A reference point pairs what the camera showed with the true time at the same instant, for
example a phone photo of the camera's screen (the photo's own timestamp is the true time).

Modes:
  offset  Each recording takes the offset of the nearest reference at or before it (the first
          reference covers anything earlier). One reference means one fixed offset. Use one
          reference per drive when the camera's clock stops while the car is off.
  linear  Least-squares fit over two or more references, correcting a clock that runs at the
          wrong rate as well as starting at the wrong time.
"""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .errors import SdprepError
from .profile import Profile

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
FALLBACK_SECONDS = 600  # subtitle length when a recording's duration cannot be read


@dataclass(frozen=True)
class Recording:
    path: Path
    camera_time: datetime
    kind: str
    channel: str
    locked: bool


@dataclass(frozen=True)
class Reference:
    camera: datetime
    real: datetime


Corrector = Callable[[datetime], datetime]


def parse_time(text: str) -> datetime:
    try:
        return datetime.strptime(text.strip(), TIME_FORMAT)
    except ValueError:
        raise SdprepError(f"'{text}' is not a time like 2026-10-28 02:56:47") from None


def parse_reference(text: str) -> Reference:
    if text.count("=") != 1:
        raise SdprepError(f"reference '{text}' must look like 'CAMERA_TIME=REAL_TIME'")
    camera, real = text.split("=")
    return Reference(parse_time(camera), parse_time(real))


def scan(directory: Path, profile: Profile) -> list[Recording]:
    if not directory.is_dir():
        raise SdprepError(f"{directory} is not a directory")
    found = []
    for path in sorted(directory.iterdir()):
        m = profile.recording_pattern.match(path.name)
        if not m or not path.is_file():
            continue
        try:
            when = datetime.strptime(m["date"] + m["time"], "%Y%m%d%H%M%S")
        except ValueError:
            continue  # a name that matches the pattern but holds an impossible date is not a recording
        groups = m.groupdict()
        found.append(Recording(path, when, groups.get("kind") or "", groups.get("channel") or "",
                               bool(groups.get("locked"))))
    return sorted(found, key=lambda r: (r.camera_time, r.path.name))


def offset_corrector(refs: list[Reference]) -> Corrector:
    if not refs:
        raise SdprepError("at least one reference is required")
    ordered = sorted(refs, key=lambda r: r.camera)

    def correct(camera: datetime) -> datetime:
        chosen = ordered[0]
        for ref in ordered:
            if ref.camera <= camera:
                chosen = ref
        return camera + (chosen.real - chosen.camera)

    return correct


def linear_corrector(refs: list[Reference]) -> Corrector:
    if len({r.camera for r in refs}) < 2:
        raise SdprepError("linear mode needs at least two references at different camera times")
    epoch = min(r.camera for r in refs)
    xs = [(r.camera - epoch).total_seconds() for r in refs]
    ys = [(r.real - epoch).total_seconds() for r in refs]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    intercept = my - slope * mx

    def correct(camera: datetime) -> datetime:
        x = (camera - epoch).total_seconds()
        return epoch + timedelta(seconds=round(slope * x + intercept))

    return correct


def avi_duration(path: Path) -> float | None:
    """Duration from the AVI main header, or None for files the camera never finalised."""
    with path.open("rb") as fh:
        head = fh.read(256)
    if len(head) < 64 or head[0:4] != b"RIFF" or head[8:12] != b"AVI ":
        return None
    pos = head.find(b"avih")
    if pos < 0 or pos + 28 > len(head):
        return None
    usec_per_frame, = struct.unpack_from("<I", head, pos + 8)
    total_frames, = struct.unpack_from("<I", head, pos + 24)
    if not usec_per_frame or not total_frames:
        return None
    return usec_per_frame * total_frames / 1e6


def srt(start: datetime, seconds: float, label: str) -> str:
    def stamp(s: int) -> str:
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d},000"

    suffix = f" {label}" if label else ""
    cues = []
    for i in range(max(1, int(seconds + 0.999))):
        cues.append(f"{i + 1}\n{stamp(i)} --> {stamp(i + 1)}\n{(start + timedelta(seconds=i)).strftime(TIME_FORMAT)}{suffix}\n")
    return "\n".join(cues)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_verified(src: Path, dest: Path, digest: str) -> None:
    """Copy via a temporary name, check the hash, then rename, so no partial file ever has the final name."""
    tmp = dest.with_name(dest.name + ".part")
    try:
        shutil.copy2(src, tmp)
        if sha256(tmp) != digest:
            raise SdprepError(f"copy of {src.name} does not match the original; nothing was kept")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():  # interrupted, failed or mismatched: never leave a partial file behind
            tmp.unlink()


def corrected_name(rec: Recording, real: datetime) -> str:
    tag = f"_{rec.kind}{rec.channel}" if rec.kind else ""
    return f"{real:%Y-%m-%d_%H-%M-%S}{tag}{'_KEP' if rec.locked else ''}{rec.path.suffix.lower()}"


def run(source: Path, out: Path, profile: Profile, refs: list[Reference], mode: str,
        copy: bool, subtitles: bool, label: str) -> tuple[Path, int]:
    source, out = source.resolve(), out.resolve()
    if out == source or source in out.parents:
        raise SdprepError("output folder must be outside the recordings folder; originals are never modified")
    if out.exists() and any(out.iterdir()):
        raise SdprepError(f"output folder {out} is not empty")
    correct = {"offset": offset_corrector, "linear": linear_corrector}[mode](refs)
    recordings = scan(source, profile)
    if not recordings:
        raise SdprepError(f"no {profile.name} recordings found in {source}")
    out.mkdir(parents=True, exist_ok=True)

    report = out / "timefix.csv"
    with report.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["original", "camera_time", "real_time", "correction_seconds", "duration_seconds",
                         "kind", "channel", "locked", "sha256", "corrected_name"])
        for rec in recordings:
            real = correct(rec.camera_time)
            duration = avi_duration(rec.path) if rec.path.suffix.lower() == ".avi" else None
            name = corrected_name(rec, real)
            digest = sha256(rec.path)
            if copy:
                copy_verified(rec.path, out / name, digest)
            if subtitles:
                (out / (Path(name).stem + ".srt")).write_text(
                    srt(real, duration or FALLBACK_SECONDS, label), encoding="utf-8")
            writer.writerow([rec.path.name, rec.camera_time.strftime(TIME_FORMAT), real.strftime(TIME_FORMAT),
                             int((real - rec.camera_time).total_seconds()),
                             "" if duration is None else f"{duration:.1f}", rec.kind, rec.channel,
                             "yes" if rec.locked else "no", digest, name])
    return report, len(recordings)

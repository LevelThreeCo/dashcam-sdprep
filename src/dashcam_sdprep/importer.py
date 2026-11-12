"""Import recordings from a card into a dated library, with real times and subtitles.

Layout:  <import folder>/<YYYY-MM-DD>/<YYYY-MM-DD_HH-MM-SS>_<KIND><ch>[_KEP].avi (+ .srt)
         <import folder>/import-reports/import_<when>.csv
Times are the calibrated real time in the chosen zone. Files already imported (same name, same
content) are skipped, so importing the same card twice is safe. The card is only read.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Callable, Optional

from . import timefix
from .errors import SdprepError
from .profile import Profile
from .settings import Calibration

Progress = Callable[[int, int, str, str], None]  # (index, total, new_name, status)


@dataclass(frozen=True)
class Plan:
    recordings: list[timefix.Recording]
    first_real: datetime
    last_real: datetime


@dataclass(frozen=True)
class Result:
    imported: int
    skipped: int
    report: Path


def real_time(rec: timefix.Recording, calibration: Calibration, zone: tzinfo) -> datetime:
    return calibration.real_time(rec.camera_time).astimezone(zone)


def plan(source: Path, profile: Profile, calibration: Calibration, zone: tzinfo) -> Plan:
    recordings = timefix.scan(source, profile)
    if not recordings:
        raise SdprepError(f"no {profile.name} recordings found in {source}")
    times = [real_time(r, calibration, zone) for r in recordings]
    return Plan(recordings, min(times), max(times))


def run(source: Path, library: Path, profile: Profile, calibration: Calibration, zone: tzinfo,
        progress: Optional[Progress] = None, now: Optional[datetime] = None) -> Result:
    source, library = source.resolve(), library.resolve()
    if library == source or source in library.parents:
        raise SdprepError("the import folder must not be on the card itself")
    recordings = plan(source, profile, calibration, zone).recordings
    reports = library / "import-reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    report = reports / f"import_{stamp}.csv"

    imported = skipped = 0
    with report.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["status", "original", "camera_time", "real_time", "time_zone", "duration_seconds",
                         "sha256", "imported_as"])
        for i, rec in enumerate(recordings, 1):
            real = real_time(rec, calibration, zone)
            day = library / real.strftime("%Y-%m-%d")
            day.mkdir(exist_ok=True)
            dest = day / timefix.corrected_name(rec, real)
            digest = timefix.sha256(rec.path)
            if dest.exists():
                if timefix.sha256(dest) != digest:
                    raise SdprepError(f"{dest} already exists with different content; move it aside and retry")
                status = "skipped"
                skipped += 1
            else:
                timefix.copy_verified(rec.path, dest, digest)
                status = "imported"
                imported += 1
            duration = timefix.avi_duration(rec.path) if rec.path.suffix.lower() == ".avi" else None
            srt = dest.with_suffix(".srt")
            if not srt.exists():
                srt.write_text(timefix.srt(real.replace(tzinfo=None), duration or timefix.FALLBACK_SECONDS,
                                           real.tzname() or ""), encoding="utf-8")
            writer.writerow([status, rec.path.name, rec.camera_time.strftime(timefix.TIME_FORMAT),
                             real.isoformat(), str(zone), "" if duration is None else f"{duration:.1f}",
                             digest, str(dest.relative_to(library))])
            if progress:
                progress(i, len(recordings), dest.name, status)
    return Result(imported, skipped, report)

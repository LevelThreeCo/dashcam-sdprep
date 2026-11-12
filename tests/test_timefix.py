from __future__ import annotations

import csv
import hashlib
import struct
from datetime import datetime
from pathlib import Path

import pytest

from dashcam_sdprep import timefix
from dashcam_sdprep.errors import SdprepError

T = timefix.parse_time


def avi(path: Path, usec_per_frame: int = 33333, frames: int = 1800) -> Path:
    """Minimal AVI header: enough for the main header (avih) to be read."""
    avih = struct.pack("<IIIIIIIIII", usec_per_frame, 0, 0, 0, frames, 0, 1, 0, 1920, 1080) + bytes(16)
    hdrl = b"LIST" + struct.pack("<I", 4 + 8 + len(avih)) + b"hdrl" + b"avih" + struct.pack("<I", len(avih)) + avih
    body = b"AVI " + hdrl
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body + b"\0" * 1024)
    return path


def test_reference_parsing():
    ref = timefix.parse_reference("2026-10-28 02:56:47=2026-11-11 22:39:59")
    assert ref.real - ref.camera == datetime(2026, 11, 11, 22, 39, 59) - datetime(2026, 10, 28, 2, 56, 47)
    with pytest.raises(SdprepError):
        timefix.parse_reference("2026-10-28 02:56:47")
    with pytest.raises(SdprepError):
        timefix.parse_reference("yesterday=today")


def test_single_reference_is_a_fixed_offset():
    correct = timefix.offset_corrector([timefix.parse_reference("2026-10-28 02:56:47=2026-11-11 22:39:59")])
    # 14 d 19 h 43 m 12 s, applied to any camera time
    assert correct(T("2026-10-28 18:00:00")) == T("2026-11-12 13:43:12")
    assert correct(T("2026-10-01 00:00:00")) == T("2026-10-15 19:43:12")


def test_offset_mode_uses_each_drives_own_reference():
    # A clock that stops while parked: every drive gets its own reference.
    refs = [timefix.parse_reference("2026-10-28 03:00:00=2026-11-11 23:00:00"),
            timefix.parse_reference("2026-10-28 03:30:00=2026-11-12 09:00:00")]
    correct = timefix.offset_corrector(refs)
    assert correct(T("2026-10-28 03:10:00")) == T("2026-11-11 23:10:00")
    assert correct(T("2026-10-28 03:40:00")) == T("2026-11-12 09:10:00")
    assert correct(T("2026-10-28 02:50:00")) == T("2026-11-11 22:50:00")  # before the first reference


def test_linear_mode_corrects_drift():
    # Clock 1 hour behind and losing 10 s per day.
    refs = [timefix.parse_reference("2026-11-01 00:00:00=2026-11-01 01:00:00"),
            timefix.parse_reference("2026-11-10 23:58:20=2026-11-11 01:00:00")]
    correct = timefix.linear_corrector(refs)
    assert correct(T("2026-11-06 00:00:00")) == T("2026-11-06 01:00:50")
    with pytest.raises(SdprepError, match="two references"):
        timefix.linear_corrector(refs[:1])


def test_avi_duration(tmp_path):
    assert timefix.avi_duration(avi(tmp_path / "a.avi")) == pytest.approx(60.0, abs=0.1)
    assert timefix.avi_duration(avi(tmp_path / "b.avi", frames=0)) is None  # never finalised
    (tmp_path / "c.avi").write_bytes(b"not a video")
    assert timefix.avi_duration(tmp_path / "c.avi") is None


def test_srt_cues():
    text = timefix.srt(T("2026-11-11 23:59:58"), 3, "KST")
    assert "1\n00:00:00,000 --> 00:00:01,000\n2026-11-11 23:59:58 KST" in text
    assert "3\n00:00:02,000 --> 00:00:03,000\n2026-11-12 00:00:00 KST" in text


def test_run_writes_report_copies_and_subtitles_without_touching_originals(tmp_path, vg701):
    src = tmp_path / "card"
    src.mkdir()
    rec = avi(src / "REC0_20261028_180000.avi")
    avi(src / "EVT1_20261028_180100_KEP.avi", frames=0)
    (src / "setup.ini").write_text("[Model]\nModel = VG-701V3\n")
    (src / "REC0_20261399_000000.avi").write_bytes(b"impossible date, skipped")
    before = hashlib.sha256(rec.read_bytes()).hexdigest()

    out = tmp_path / "fixed"
    refs = [timefix.parse_reference("2026-10-28 02:56:47=2026-11-11 22:39:59")]
    report, count = timefix.run(src, out, vg701, refs, "offset", copy=True, subtitles=True, label="KST")

    assert count == 2
    rows = list(csv.DictReader(report.open()))
    assert [r["real_time"] for r in rows] == ["2026-11-12 13:43:12", "2026-11-12 13:44:12"]
    assert rows[0]["sha256"] == before and rows[0]["duration_seconds"] == "60.0"
    assert rows[1]["locked"] == "yes" and rows[1]["duration_seconds"] == ""
    assert (out / "2026-11-12_13-43-12_REC0.avi").read_bytes() == rec.read_bytes()
    assert (out / "2026-11-12_13-44-12_EVT1_KEP.avi").exists()
    assert "2026-11-12 13:43:12 KST" in (out / "2026-11-12_13-43-12_REC0.srt").read_text()
    assert hashlib.sha256(rec.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in src.iterdir()) == ["EVT1_20261028_180100_KEP.avi", "REC0_20261028_180000.avi",
                                                     "REC0_20261399_000000.avi", "setup.ini"]


def test_run_refuses_output_inside_source_or_non_empty(tmp_path, vg701):
    src = tmp_path / "card"
    src.mkdir()
    avi(src / "REC0_20261028_180000.avi")
    refs = [timefix.parse_reference("2026-10-28 02:56:47=2026-11-11 22:39:59")]
    with pytest.raises(SdprepError, match="outside"):
        timefix.run(src, src / "out", vg701, refs, "offset", False, False, "")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("x")
    with pytest.raises(SdprepError, match="not empty"):
        timefix.run(src, busy, vg701, refs, "offset", False, False, "")

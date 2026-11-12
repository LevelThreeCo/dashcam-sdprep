from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from dashcam_sdprep import importer
from dashcam_sdprep.errors import SdprepError
from dashcam_sdprep.settings import Calibration
from test_timefix import avi

KST = timezone(timedelta(hours=9))
CAL = Calibration(datetime(2026, 10, 28, 13, 44, 41), datetime(2026, 11, 12, 9, 27, 52, tzinfo=KST), "screen")


@pytest.fixture
def card(tmp_path):
    root = tmp_path / "card"
    root.mkdir()
    avi(root / "REC1_20261028_133220.avi")
    avi(root / "MOT1_20261028_041841.avi")  # crosses midnight in real time
    (root / "._REC1_20261028_133220.avi").write_bytes(b"macOS metadata")
    (root / "setup.ini").write_text("[Model]\n")
    return root


def test_import_into_dated_folders_with_subtitles(tmp_path, card, vg701):
    lib = tmp_path / "Dashcam"
    seen = []
    res = importer.run(card, lib, vg701, CAL, ZoneInfo("Asia/Seoul"), lambda *a: seen.append(a),
                       now=datetime(2026, 11, 12, 10, 0, 0))
    assert (res.imported, res.skipped) == (2, 0)
    assert (lib / "2026-11-12" / "2026-11-12_00-01-52_MOT1.avi").exists()
    rec = lib / "2026-11-12" / "2026-11-12_09-15-31_REC1.avi"
    assert rec.read_bytes() == (card / "REC1_20261028_133220.avi").read_bytes()
    assert "2026-11-12 09:15:31 KST" in rec.with_suffix(".srt").read_text()
    assert not list(lib.rglob("*.part"))
    rows = list(csv.DictReader(res.report.open()))
    assert {r["status"] for r in rows} == {"imported"} and len(rows) == 2
    assert [s[3] for s in seen] == ["imported", "imported"]


def test_reimport_skips_and_never_duplicates(tmp_path, card, vg701):
    lib = tmp_path / "Dashcam"
    importer.run(card, lib, vg701, CAL, ZoneInfo("Asia/Seoul"), now=datetime(2026, 11, 12, 10, 0, 0))
    res = importer.run(card, lib, vg701, CAL, ZoneInfo("Asia/Seoul"), now=datetime(2026, 11, 12, 10, 5, 0))
    assert (res.imported, res.skipped) == (0, 2)
    assert len(list(lib.rglob("*.avi"))) == 2


def test_other_time_zone_shows_the_same_instant(tmp_path, card, vg701):
    lib = tmp_path / "Dashcam"
    importer.run(card, lib, vg701, CAL, ZoneInfo("America/Toronto"))
    # 2026-11-12 09:15:31 KST is 2026-11-11 19:15:31 in Toronto (EST, UTC-5)
    srt = lib / "2026-11-11" / "2026-11-11_19-15-31_REC1.srt"
    assert "2026-11-11 19:15:31 EST" in srt.read_text()


def test_name_collision_with_different_content_stops(tmp_path, card, vg701):
    lib = tmp_path / "Dashcam"
    (lib / "2026-11-12").mkdir(parents=True)
    (lib / "2026-11-12" / "2026-11-12_00-01-52_MOT1.avi").write_bytes(b"something else")
    with pytest.raises(SdprepError, match="different content"):
        importer.run(card, lib, vg701, CAL, ZoneInfo("Asia/Seoul"))


def test_refuses_library_on_the_card(card, vg701):
    with pytest.raises(SdprepError, match="not be on the card"):
        importer.run(card, card / "Dashcam", vg701, CAL, ZoneInfo("Asia/Seoul"))

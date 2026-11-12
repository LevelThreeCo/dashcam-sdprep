from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from dashcam_sdprep import settings
from dashcam_sdprep.errors import SdprepError

KST = timezone(timedelta(hours=9))


def cal() -> settings.Calibration:
    return settings.Calibration(datetime(2026, 10, 28, 13, 44, 41), datetime(2026, 11, 12, 9, 27, 52, tzinfo=KST),
                                "photo")


def test_defaults_when_no_file(tmp_path):
    assert settings.load(tmp_path / "none.json") == settings.Settings()


def test_round_trip_is_atomic_and_exact(tmp_path):
    path = tmp_path / "deep" / "settings.json"
    original = settings.Settings(language="ko-KR", import_dir="/x/Dashcam", timezone="Asia/Seoul", calibration=cal())
    settings.save(original, path)
    assert settings.load(path) == original
    assert not path.with_suffix(".tmp").exists()
    assert json.loads(path.read_text())["language"] == "ko-KR"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHCAM_SDPREP_CONFIG", str(tmp_path / "s.json"))
    assert settings.config_path() == tmp_path / "s.json"


def test_calibration_maps_camera_time_to_true_instant():
    c = cal()
    # Camera 2026-10-28 13:44:41 was really 2026-11-12 00:27:52 UTC (09:27:52 KST).
    assert c.offset == timedelta(days=14, hours=10, minutes=43, seconds=11)
    real = c.real_time(datetime(2026, 10, 28, 13, 32, 20))
    assert real.astimezone(KST).replace(tzinfo=None) == datetime(2026, 11, 12, 9, 15, 31)


@pytest.mark.parametrize("bad", [
    {"language": "fr-FR"},
    {"timezone": "Mars/Olympus"},
    {"import_dir": 5},
    {"calibration": {"camera": "2026-10-28T13:44:41", "real": "2026-11-12T09:27:52", "method": "photo"}},
    {"calibration": {"camera": "2026-10-28T13:44:41", "real": "2026-11-12T09:27:52+09:00", "method": "guess"}},
    {"calibration": "yesterday"},
])
def test_invalid_settings_fail_loudly(tmp_path, bad):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(SdprepError):
        settings.load(path)


def test_corrupt_file_names_the_file(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    with pytest.raises(SdprepError, match="s.json"):
        settings.load(path)

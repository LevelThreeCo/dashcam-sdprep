"""User settings, stored as JSON in the platform's per-user config folder.

macOS: ~/Library/Application Support/dashcam-sdprep/settings.json
Linux: $XDG_CONFIG_HOME/dashcam-sdprep/settings.json (default ~/.config/...)
DASHCAM_SDPREP_CONFIG overrides the path (used by tests).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Optional

from .errors import SdprepError

LANGUAGES = ("en-US", "ko-KR")
CALIBRATION_METHODS = ("screen", "photo", "manual")


def config_path() -> Path:
    override = os.environ.get("DASHCAM_SDPREP_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "dashcam-sdprep" / "settings.json"


@dataclass(frozen=True)
class Calibration:
    """What the camera showed (its own naive clock) at one true instant."""

    camera: datetime  # naive
    real: datetime    # timezone-aware
    method: str

    def __post_init__(self) -> None:
        if self.camera.tzinfo is not None:
            raise SdprepError("camera time must not carry a time zone; it is whatever the camera shows")
        if self.real.tzinfo is None or self.real.utcoffset() is None:
            raise SdprepError("real time must carry a time zone")
        if self.method not in CALIBRATION_METHODS:
            raise SdprepError(f"unknown calibration method '{self.method}'")

    @property
    def offset(self) -> timedelta:
        """real (as UTC wall time) minus camera time; constant while the camera clock keeps time."""
        return self.real.astimezone(timezone.utc).replace(tzinfo=None) - self.camera

    def real_time(self, camera: datetime) -> datetime:
        """True instant (UTC-aware) for a time read off the camera's clock."""
        return (camera + self.offset).replace(tzinfo=timezone.utc)

    def to_json(self) -> dict[str, str]:
        return {"camera": self.camera.isoformat(), "real": self.real.isoformat(), "method": self.method}

    @classmethod
    def from_json(cls, data: Any) -> Calibration:
        if not isinstance(data, dict):
            raise SdprepError("calibration must be an object")
        try:
            return cls(datetime.fromisoformat(data["camera"]), datetime.fromisoformat(data["real"]),
                       str(data["method"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SdprepError(f"calibration is malformed: {exc}") from exc


@dataclass(frozen=True)
class Settings:
    language: str = "en-US"
    import_dir: Optional[str] = None
    profile: str = "vugera-vg-701v3"
    timezone: str = ""  # IANA name; empty means this computer's zone
    calibration: Optional[Calibration] = None

    def __post_init__(self) -> None:
        if self.language not in LANGUAGES:
            raise SdprepError(f"language must be one of {LANGUAGES}")
        if self.timezone:
            zone_from_name(self.timezone)

    def with_(self, **changes: Any) -> Settings:
        return replace(self, **changes)

    def zone(self) -> tzinfo:
        return zone_from_name(self.timezone) if self.timezone else system_zone()

    def to_json(self) -> dict[str, Any]:
        return {"language": self.language, "import_dir": self.import_dir, "profile": self.profile,
                "timezone": self.timezone,
                "calibration": self.calibration.to_json() if self.calibration else None}

    @classmethod
    def from_json(cls, data: Any) -> Settings:
        if not isinstance(data, dict):
            raise SdprepError("settings file must hold a JSON object")
        cal = data.get("calibration")
        import_dir = data.get("import_dir")
        if import_dir is not None and not isinstance(import_dir, str):
            raise SdprepError("import_dir must be a path or null")
        return cls(language=str(data.get("language", "en-US")), import_dir=import_dir,
                   profile=str(data.get("profile", "vugera-vg-701v3")), timezone=str(data.get("timezone", "")),
                   calibration=Calibration.from_json(cal) if cal is not None else None)


def load(path: Optional[Path] = None) -> Settings:
    path = path or config_path()
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SdprepError(f"settings file {path} is not valid JSON ({exc}); fix or delete it") from exc
    return Settings.from_json(data)


def save(settings: Settings, path: Optional[Path] = None) -> Path:
    """Write atomically so an interrupted save never leaves a half-written file."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def zone_from_name(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError as exc:  # pragma: no cover - Python < 3.9 is unsupported anyway
        raise SdprepError("time zones need Python 3.9+") from exc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SdprepError(f"unknown time zone '{name}' (use a name like Asia/Seoul or America/Toronto)") from None


def system_zone_name() -> str:
    """IANA name of this computer's zone, or '' if it cannot be determined."""
    if os.environ.get("TZ", "").strip(":"):
        return os.environ["TZ"].strip(":")
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return ""
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else ""


def system_zone() -> tzinfo:
    name = system_zone_name()
    if name:
        try:
            return zone_from_name(name)
        except SdprepError:
            pass  # fall through to the fixed offset below
    local = datetime.now().astimezone().tzinfo
    assert local is not None
    return local

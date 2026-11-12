from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import pytest

from dashcam_sdprep import disks, tui
from dashcam_sdprep.settings import Calibration, Settings
from test_photo_time import tiff
from test_timefix import avi

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 12, 9, 27, 52, tzinfo=KST)


class Script:
    """Scripted prompter. Select answers are matched against choice labels (substring)."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.said: list[str] = []
        self.asked: list[str] = []

    def _next(self, message: str) -> Any:
        self.asked.append(message)
        assert self.answers, f"no scripted answer left for: {message}"
        return self.answers.pop(0)

    def select(self, message: str, choices: Sequence[tui.Choice], filterable: bool = False) -> Any:
        want = self._next(message)
        if want is None:
            return None
        hits = [value for label, value in choices if want in label]
        assert hits, f"'{want}' not among {[label for label, _ in choices]}"
        return hits[0]

    def text(self, message: str, default: str = "", validate: Optional[Callable[[str], Any]] = None) -> Optional[str]:
        answer = self._next(message)
        if answer is not None and validate is not None:
            assert validate(answer) is True, f"validator rejected {answer!r}"
        return answer

    def path(self, message: str, default: str = "") -> Optional[str]:
        return self._next(message)

    def confirm(self, message: str, default: bool = False) -> Optional[bool]:
        return self._next(message)

    def say(self, message: str = "") -> None:
        self.said.append(message)

    def set_language(self, language: str) -> None:
        self.language = language

    def pause(self, message: str) -> None:
        self.said.append(f"[pause] {message}")

    def clear(self) -> None:
        pass


class FakeBackend(disks.Backend):
    def __init__(self, card: Path, device: Path) -> None:
        self.card, self.device, self.released = card, device, False

    def list_disks(self) -> list[disks.Disk]:
        return [disks.Disk("disk9", str(self.device), 31914983424, "SD reader", "Secure Digital")]

    def mount_point(self, disk: disks.Disk) -> Path:
        return self.card

    def release(self, disk: disks.Disk) -> None:
        self.released = True


def make_app(script: Script, current: Settings, tmp_path: Path, backend: Any = None, commands: Any = None) -> tui.App:
    saved: list[Settings] = []
    app = tui.App(script, current, backend=(lambda: backend) if backend else None, now=lambda: NOW,
                  save=saved.append, run_command=lambda cmd: (commands.append(cmd) if commands is not None else None) or 0,
                  home=tmp_path)
    app.saved = saved  # type: ignore[attr-defined]
    return app


CAL = Calibration(datetime(2026, 10, 28, 13, 44, 41), NOW, "screen")


def test_settings_language_and_preset_folder(tmp_path):
    s = Script(["Language", "한국어", "저장 폴더", "다운로드", None])
    app = make_app(s, Settings(), tmp_path)
    app.settings_flow()
    assert app.settings.language == "ko-KR"
    assert app.settings.import_dir == str(tmp_path / "Downloads" / "Dashcam")
    assert (tmp_path / "Downloads" / "Dashcam").is_dir()
    assert app.saved[-1] == app.settings


def test_folder_browser_opens_creates_and_uses(tmp_path):
    (tmp_path / "Movies").mkdir()
    (tmp_path / ".hidden").mkdir()
    s = Script(["Custom", "Movies/", "New folder", "Dashcam", "Use this folder"])
    app = make_app(s, Settings(), tmp_path)
    assert app.folder_setting() is True
    assert app.settings.import_dir == str(tmp_path / "Movies" / "Dashcam")


def test_screen_calibration_uses_the_moment_enter_was_pressed(tmp_path):
    s = Script(["camera screen", "2026-10-28 13:44:41", True])
    app = make_app(s, Settings(), tmp_path)
    assert app.calibrate_flow() is True
    assert app.settings.calibration is not None
    assert app.settings.calibration.offset == CAL.offset
    assert any("14 d 19 h 43 m 11 s behind" in line for line in s.said)


def test_photo_calibration_reads_exif(tmp_path):
    photo = tmp_path / "IMG.jpg"
    photo.write_bytes(b"\xff\xd8" + b"Exif\x00\x00" + tiff("2026:11:12 09:27:52", "+09:00"))
    s = Script(["photo", str(photo), "2026-10-28 13:44:41", True])
    app = make_app(s, Settings(), tmp_path)
    assert app.calibrate_flow() is True
    assert app.settings.calibration.offset == CAL.offset  # type: ignore[union-attr]


def test_import_flow_end_to_end(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    avi(card / "REC1_20261028_133220.avi")
    backend = FakeBackend(card, tmp_path / "rdisk9")
    lib = tmp_path / "Dashcam"
    s = Script([True, True])  # start importing? eject?
    app = make_app(s, Settings(import_dir=str(lib), timezone="Asia/Seoul", calibration=CAL), tmp_path, backend)
    app.import_flow()
    assert (lib / "2026-11-12" / "2026-11-12_09-15-31_REC1.avi").exists()
    assert backend.released


def test_import_without_calibration_offers_it_first(tmp_path):
    s = Script([False])
    app = make_app(s, Settings(import_dir=str(tmp_path)), tmp_path)
    app.import_flow()
    assert app.settings.calibration is None and not s.answers


def test_prepare_requires_exact_disk_name(tmp_path):
    device = tmp_path / "rdisk9"
    device.write_bytes(b"")
    commands: list[list[str]] = []
    card = tmp_path / "card"
    card.mkdir()
    backend = FakeBackend(card, device)
    app = make_app(Script(["disk 9"]), Settings(), tmp_path, backend, commands)
    app.prepare_flow()
    assert commands == []
    app = make_app(Script(["disk9"]), Settings(), tmp_path, backend, commands)
    app.prepare_flow()
    assert len(commands) == 1 and commands[0][-5:] == ["format", "disk9", "--profile", "vugera-vg-701v3", "--yes"]
    assert commands[0][0] != "sudo"  # the fake device is readable, so no sudo


def test_prepare_offers_to_import_recordings_first(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    avi(card / "REC1_20261028_133220.avi")
    backend = FakeBackend(card, tmp_path / "rdisk9")
    commands: list[list[str]] = []
    s = Script([True, True, False])  # import first? -> start importing? -> eject? no
    app = make_app(s, Settings(import_dir=str(tmp_path / "lib"), calibration=CAL), tmp_path, backend, commands)
    app.prepare_flow()
    assert commands == [] and list((tmp_path / "lib").rglob("*.avi"))


def test_main_loop_quits_and_survives_errors(tmp_path):
    s = Script(["Settings", "Time zone", "Other", "Mars/Olympus", "Quit"])
    app = make_app(s, Settings(), tmp_path)
    app.run()
    assert any("unknown time zone" in line for line in s.said)


def test_real_questionary_prompter_with_keystrokes():
    pytest.importorskip("questionary")
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        ui = tui.QuestionaryPrompter(input=pipe, output=DummyOutput())
        pipe.send_text("\x1b[B\r")  # arrow down, Enter
        assert ui.select("Pick", [("first", 1), ("second", 2), ("third", 3)]) == 2
        pipe.send_text("thi\r")  # type to filter, Enter
        assert ui.select("Pick", [("first", 1), ("second", 2), ("third", 3)], filterable=True) == 3
        pipe.send_text("2026-10-28 13:44:41\r")
        assert ui.text("When?") == "2026-10-28 13:44:41"


def test_first_run_asks_language_and_errors_never_escape(tmp_path):
    s = Script(["한국어", "SD 카드 준비", None])  # first run: Korean; prepare with no card; Esc quits
    class Boom(disks.Backend):
        def list_disks(self):
            raise OSError(5, "Input/output error", "/dev/rdisk9")
    app = make_app(s, Settings(), tmp_path, Boom())
    app.run(first_run=True)
    assert app.settings.language == "ko-KR" and s.language == "ko-KR"
    assert any("Input/output error: /dev/rdisk9" in line for line in s.said)
    assert any(line.startswith("[pause]") for line in s.said)
    assert s.said[-1] == "종료합니다."


def test_unexpected_exception_is_reported_not_raised(tmp_path):
    class Weird(disks.Backend):
        def list_disks(self):
            raise ValueError("odd")
    s = Script(["Prepare", "Quit"])
    make_app(s, Settings(), tmp_path, Weird()).run()
    assert any("ValueError: odd" in line for line in s.said)


def test_escape_goes_back_in_real_prompts():
    pytest.importorskip("questionary")
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        ui = tui.QuestionaryPrompter(input=pipe, output=DummyOutput())
        pipe.send_text("\x1b")
        assert ui.select("Pick", [("first", 1), ("second", 2)]) is None
        pipe.send_text("\x1b")
        assert ui.text("When?") is None
        pipe.send_text("\x1b")
        assert ui.confirm("Sure?") is None
        pipe.send_text("\x03")  # Ctrl-C
        assert ui.select("Pick", [("first", 1)]) is None


def test_interrupted_copy_leaves_no_partial_file(tmp_path, monkeypatch):
    from dashcam_sdprep import timefix
    src = tmp_path / "a.avi"
    src.write_bytes(b"x" * 1000)

    def broken_copy(a, b):
        open(b, "wb").write(b"x" * 10)
        raise KeyboardInterrupt
    monkeypatch.setattr(timefix.shutil, "copy2", broken_copy)
    with pytest.raises(KeyboardInterrupt):
        timefix.copy_verified(src, tmp_path / "out.avi", timefix.sha256(src))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.avi"]

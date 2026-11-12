"""Interactive menu: import footage, calibrate the camera clock, prepare cards, settings.

All screen text goes through i18n. All input goes through a Prompter, so the flows can be driven
by a scripted prompter in tests; QuestionaryPrompter is the real terminal implementation.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence

from . import __version__, disks, i18n, importer, photo_time, profile, selfexec, settings as settings_mod
from .errors import SdprepError
from .settings import Calibration, Settings

CAMERA_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
COMMON_ZONES = ("Asia/Seoul", "America/Toronto", "America/New_York", "America/Los_Angeles",
                "Europe/London", "UTC")
Choice = tuple[str, Any]  # (label, value)


class Prompter(Protocol):
    """Every prompt returns None when the user goes back (Esc) or interrupts (Ctrl-C)."""

    def set_language(self, language: str) -> None: ...
    def select(self, message: str, choices: Sequence[Choice], filterable: bool = False) -> Any: ...
    def text(self, message: str, default: str = "", validate: Optional[Callable[[str], Any]] = None) -> Optional[str]: ...
    def path(self, message: str, default: str = "") -> Optional[str]: ...
    def confirm(self, message: str, default: bool = False) -> Optional[bool]: ...
    def pause(self, message: str) -> None: ...
    def clear(self) -> None: ...
    def say(self, message: str = "") -> None: ...


class QuestionaryPrompter:
    def __init__(self, **terminal: Any) -> None:
        """`terminal` may carry prompt_toolkit input/output objects (tests pass pipe input here)."""
        import questionary  # imported here so scripted commands never need it
        self.q = questionary
        self.terminal = terminal
        self.language = "en-US"

    def set_language(self, language: str) -> None:
        self.language = language

    def _ask(self, question: Any) -> Any:
        """Run a prompt with Esc bound to 'go back' and Ctrl-C returning quietly."""
        from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings

        back = KeyBindings()

        @back.add("escape", eager=True)
        def _(event: Any) -> None:
            event.app.exit(result=None)

        app = question.application
        app.key_bindings = merge_key_bindings([app.key_bindings, back]) if app.key_bindings else back
        app.ttimeoutlen = 0.05  # react to a lone Esc quickly; arrow keys are still parsed as arrows
        try:
            return question.unsafe_ask()
        except (KeyboardInterrupt, EOFError):
            return None

    def _hint(self, key: str) -> str:
        return i18n.t(self.language, key)

    def select(self, message: str, choices: Sequence[Choice], filterable: bool = False) -> Any:
        items = [self.q.Choice(title=label, value=i) for i, (label, _) in enumerate(choices)]
        picked = self._ask(self.q.select(
            message, items, use_search_filter=filterable, use_jk_keys=not filterable,
            instruction=self._hint("hint_filter" if filterable else "hint_select"), **self.terminal))
        return None if picked is None else choices[picked][1]

    def text(self, message: str, default: str = "", validate: Optional[Callable[[str], Any]] = None) -> Optional[str]:
        return self._ask(self.q.text(message, default=default, validate=validate,
                                     instruction=self._hint("hint_text"), **self.terminal))

    def path(self, message: str, default: str = "") -> Optional[str]:
        return self._ask(self.q.path(message, default=default, **self.terminal))

    def confirm(self, message: str, default: bool = False) -> Optional[bool]:
        return self._ask(self.q.confirm(message, default=default, **self.terminal))

    def pause(self, message: str) -> None:
        self._ask(self.q.press_any_key_to_continue(message, **self.terminal))

    def clear(self) -> None:
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="", flush=True)

    def say(self, message: str = "") -> None:
        print(message, flush=True)


def clean_path(raw: str) -> Path:
    """Turn what people type, paste or drag into a terminal into a path.

    Handles surrounding spaces (Tab completion and drag-and-drop add one), quotes, backslash-escaped
    characters ("My\\ Photos"), and ~.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    else:
        text = re.sub(r"\\(.)", r"\1", text)
    return Path(text).expanduser()


def describe_offset(offset: timedelta, t: Callable[..., str]) -> str:
    seconds = int(round(offset.total_seconds()))
    days, rest = divmod(abs(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    span = t("span", days=days, hours=hours, minutes=minutes, seconds=secs)
    if seconds == 0:
        return t("offset_none")
    return t("offset_behind" if seconds > 0 else "offset_ahead", span=span)


class App:
    def __init__(self, ui: Prompter, current: Settings, *,
                 backend: Optional[Callable[[], disks.Backend]] = None,
                 now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                 save: Callable[[Settings], Any] = settings_mod.save,
                 run_command: Callable[[list[str]], int] = lambda cmd: subprocess.run(cmd).returncode,
                 home: Optional[Path] = None) -> None:
        self.ui = ui
        self.settings = current
        self.backend = backend or disks.backend
        self.now = now
        self._save = save
        self.run_command = run_command
        self.home = home or Path.home()

    # ------------------------------------------------------------------ helpers

    def t(self, key: str, **values: Any) -> str:
        return i18n.t(self.settings.language, key, **values)

    def update(self, **changes: Any) -> None:
        self.settings = self.settings.with_(**changes)
        self._save(self.settings)

    def zone(self) -> tzinfo:
        return self.settings.zone()

    def wall_offset(self, cal: Calibration) -> timedelta:
        """Gap between the camera's clock and wall-clock time in the chosen zone (what people read)."""
        return cal.real.astimezone(self.zone()).replace(tzinfo=None) - cal.camera

    def zone_label(self) -> str:
        name = self.settings.timezone or settings_mod.system_zone_name() or str(self.zone())
        abbr = self.now().astimezone(self.zone()).tzname() or ""
        return f"{name} ({abbr})" if abbr and abbr != name else name

    def status(self) -> str:
        prof = profile.load(self.settings.profile)
        folder = self.settings.import_dir or self.t("not_set")
        cal = self.settings.calibration
        clock = (self.t("clock_calibrated", offset=describe_offset(self.wall_offset(cal), self.t),
                        when=cal.real.astimezone(self.zone()).strftime("%Y-%m-%d %H:%M"))
                 if cal else self.t("clock_not_calibrated"))
        return "\n".join([self.t("status_camera", name=prof.name), self.t("status_folder", folder=folder),
                          self.t("status_zone", zone=self.zone_label()), clock])

    def error(self, exc: Any) -> None:
        self.ui.say(self.t("error", message=str(exc)))

    def parse_camera_time(self, text: str) -> Optional[datetime]:
        try:
            return datetime.strptime(text.strip(), CAMERA_TIME_FORMAT)
        except ValueError:
            return None

    def validate_time(self, text: str) -> Any:
        return True if self.parse_camera_time(text) else self.t("bad_time")

    # ------------------------------------------------------------------ main loop

    def run(self, first_run: bool = False) -> None:
        self.ui.set_language(self.settings.language)
        if first_run:
            self.first_run()
        while True:
            self.ui.clear()
            self.ui.say(f"dashcam-sdprep {__version__}")
            self.ui.say()
            self.ui.say(self.status())
            self.ui.say()
            action = self.ui.select(self.t("main_title"), [
                (self.t("main_import"), self.import_flow),
                (self.t("main_calibrate"), self.calibrate_flow),
                (self.t("main_prepare"), self.prepare_flow),
                (self.t("main_settings"), self.settings_flow),
                (self.t("main_quit"), None),
            ])
            if action is None:  # Quit, Esc or Ctrl-C at the main menu
                self.ui.say(self.t("bye"))
                return
            self.ui.say()
            self.guarded(action)
            if action != self.settings_flow:
                self.ui.pause(self.t("pause"))

    def first_run(self) -> None:
        lang = self.ui.select("Language / 언어", [(name, code) for code, name in i18n.LANGUAGE_NAMES.items()])
        if lang:
            self.update(language=lang)
            self.ui.set_language(lang)

    def guarded(self, action: Callable[[], Any]) -> None:
        """Run a menu action; whatever goes wrong, explain it and come back to the menu."""
        try:
            action()
        except SdprepError as exc:
            self.error(exc)
        except KeyboardInterrupt:
            self.ui.say(self.t("interrupted"))
        except OSError as exc:
            detail = exc.strerror or str(exc)
            self.error(f"{detail}: {exc.filename}" if exc.filename else detail)
        except Exception as exc:  # last resort: never drop the user out with a traceback
            self.ui.say(self.t("unexpected", message=f"{type(exc).__name__}: {exc}"))

    # ------------------------------------------------------------------ cards

    def choose_card(self) -> Optional[tuple[disks.Backend, disks.Disk]]:
        backend = self.backend()
        found = backend.list_disks()
        if not found:
            self.ui.say(self.t("no_card"))
            return None
        if len(found) == 1:
            return backend, found[0]
        disk = self.ui.select(self.t("pick_card"), [
            (f"{d.ident}  {d.size / 1e9:.1f} GB  {d.description}", d) for d in found])
        return (backend, disk) if disk else None

    # ------------------------------------------------------------------ import

    def import_flow(self) -> None:
        if self.settings.calibration is None:
            self.ui.say(self.t("need_calibration"))
            if not self.ui.confirm(self.t("calibrate_now"), default=True) or not self.calibrate_flow():
                return
        if not self.settings.import_dir:
            self.ui.say(self.t("need_folder"))
            if not self.folder_setting():
                return
        picked = self.choose_card()
        if not picked:
            return
        backend, disk = picked
        source = backend.mounted_card(disk)
        prof = profile.load(self.settings.profile)
        cal = self.settings.calibration
        assert cal is not None
        library = Path(self.settings.import_dir or "")
        found = importer.plan(source, prof, cal, self.zone())
        fmt = "%Y-%m-%d %H:%M:%S %Z"
        self.ui.say(self.t("import_summary", count=len(found.recordings), first=found.first_real.strftime(fmt),
                           last=found.last_real.strftime(fmt), folder=library))
        if not self.ui.confirm(self.t("import_go"), default=True):
            return

        def progress(i: int, total: int, name: str, status: str) -> None:
            self.ui.say(self.t("import_progress_" + status, i=i, total=total, name=name))

        result = importer.run(source, library, prof, cal, self.zone(), progress)
        self.ui.say(self.t("import_done", imported=result.imported, skipped=result.skipped,
                           folder=library, report=result.report))
        if self.ui.confirm(self.t("eject_now"), default=True):
            backend.release(disk)
            self.ui.say(self.t("ejected"))

    # ------------------------------------------------------------------ calibration

    def calibrate_flow(self) -> bool:
        self.ui.say(self.t("calibrate_intro"))
        method = self.ui.select(self.t("calibrate_title"), [
            (self.t("cal_screen"), "screen"), (self.t("cal_photo"), "photo"),
            (self.t("cal_manual"), "manual"), (self.t("back"), None)])
        if method is None:
            return False
        cal = {"screen": self.calibrate_screen, "photo": self.calibrate_photo,
               "manual": self.calibrate_manual}[method]()
        if cal is None:
            return False
        self.ui.say(self.t("cal_result", offset=describe_offset(self.wall_offset(cal), self.t)))
        old = self.settings.calibration
        if old is not None:
            drift = int(round((cal.offset - old.offset).total_seconds()))
            self.ui.say(self.t("cal_compare", seconds=drift))
        if not self.ui.confirm(self.t("cal_save"), default=True):
            return False
        self.update(calibration=cal)
        self.ui.say(self.t("cal_saved"))
        return True

    def calibrate_screen(self) -> Optional[Calibration]:
        self.ui.say(self.t("cal_screen_help"))
        default = ""
        if self.settings.calibration is not None:
            # Suggest the camera's expected reading half a minute from now, rounded to a minute.
            soon_utc = (self.now() + timedelta(seconds=30)).astimezone(timezone.utc).replace(tzinfo=None)
            camera_est = soon_utc - self.settings.calibration.offset
            default = camera_est.replace(second=0).strftime(CAMERA_TIME_FORMAT)
        typed = self.ui.text(self.t("cal_screen_prompt"), default=default, validate=self.validate_time)
        pressed = self.now()  # the moment Enter was pressed is the true time for what was typed
        camera = self.parse_camera_time(typed) if typed else None
        return Calibration(camera, pressed, "screen") if camera else None

    def calibrate_photo(self) -> Optional[Calibration]:
        raw = self.ui.path(self.t("cal_photo_prompt"), default=str(self.home) + "/")
        if not raw:
            return None
        taken = photo_time.capture_time(clean_path(raw))
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=self.zone())
            self.ui.say(self.t("cal_photo_no_zone", zone=self.zone_label()))
        self.ui.say(self.t("cal_photo_taken", when=taken.strftime("%Y-%m-%d %H:%M:%S %z")))
        typed = self.ui.text(self.t("cal_photo_camera"), validate=self.validate_time)
        camera = self.parse_camera_time(typed) if typed else None
        return Calibration(camera, taken, "photo") if camera else None

    def calibrate_manual(self) -> Optional[Calibration]:
        typed = self.ui.text(self.t("cal_manual_camera"), validate=self.validate_time)
        camera = self.parse_camera_time(typed) if typed else None
        if camera is None:
            return None
        typed = self.ui.text(self.t("cal_manual_real", zone=self.zone_label()), validate=self.validate_time)
        real = self.parse_camera_time(typed) if typed else None
        if real is None:
            return None
        return Calibration(camera, real.replace(tzinfo=self.zone()), "manual")

    # ------------------------------------------------------------------ prepare (format)

    def prepare_flow(self) -> None:
        prof = profile.load(self.settings.profile)
        picked = self.choose_card()
        if not picked:
            return
        backend, disk = picked
        self.ui.say(self.t("prepare_warning", ident=disk.ident, size=f"{disk.size / 1e9:.1f}",
                           desc=disk.description, camera=prof.name))
        mp = backend.mount_point(disk)
        if mp is not None and self.settings.calibration is not None:
            try:
                count = len(importer.plan(mp, prof, self.settings.calibration, self.zone()).recordings)
            except SdprepError:
                count = 0
            if count and self.ui.confirm(self.t("prepare_import_first", count=count), default=True):
                self.import_flow()
                return
        typed = self.ui.text(self.t("prepare_confirm", ident=disk.ident))
        if typed is None or typed.strip() != disk.ident:
            self.ui.say(self.t("prepare_cancelled"))
            return
        cmd = selfexec.command(["format", disk.ident, "--profile", prof.id, "--yes"])
        if selfexec.needs_sudo(disk.device):
            self.ui.say(self.t("prepare_password"))
            cmd = ["sudo", *cmd]
        code = self.run_command(cmd)
        self.ui.say(self.t("prepare_done" if code == 0 else "prepare_failed"))

    # ------------------------------------------------------------------ settings

    def settings_flow(self) -> None:
        while True:
            s = self.settings
            choice = self.ui.select(self.t("settings_title"), [
                (self.t("set_folder", value=s.import_dir or self.t("not_set")), self.folder_setting),
                (self.t("set_language", value=i18n.LANGUAGE_NAMES[s.language]), self.language_setting),
                (self.t("set_zone", value=self.zone_label()), self.zone_setting),
                (self.t("set_camera", value=profile.load(s.profile).name), self.camera_setting),
                (self.t("back"), None)])
            if choice is None:
                return
            choice()

    def folder_setting(self) -> bool:
        presets = [(name, self.home / name / "Dashcam") for name in ("Desktop", "Documents", "Downloads")]
        choice = self.ui.select(self.t("folder_title"), [
            *[(self.t("folder_preset", name=self.t("dir_" + name.lower()), path=path), path) for name, path in presets],
            (self.t("folder_custom"), "custom"), (self.t("back"), None)])
        if choice is None:
            return False
        if choice == "custom":
            start = Path(self.settings.import_dir) if self.settings.import_dir else self.home
            choice = self.browse(start)
            if choice is None:
                return False
        folder = Path(choice)
        folder.mkdir(parents=True, exist_ok=True)
        self.update(import_dir=str(folder))
        self.ui.say(self.t("folder_saved", folder=folder))
        return True

    def browse(self, start: Path) -> Optional[Path]:
        """Arrow keys move, Enter opens a folder, typing filters the list."""
        current = start if start.is_dir() else self.home
        while True:
            try:
                subdirs = sorted((p for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")),
                                 key=lambda p: p.name.lower())
            except PermissionError:
                subdirs = []
                self.ui.say(self.t("browse_denied", path=current))
            choices: list[Choice] = [(self.t("browse_use", path=current), "use")]
            if current.parent != current:
                choices.append((self.t("browse_up"), "up"))
            choices += [(self.t("browse_new"), "new"), (self.t("browse_type"), "type")]
            choices += [(f"{d.name}/", d) for d in subdirs]
            choices.append((self.t("back"), None))
            pick = self.ui.select(self.t("browse_title", path=current), choices, filterable=True)
            if pick is None:
                return None
            if pick == "use":
                return current
            if pick == "up":
                current = current.parent
            elif pick == "new":
                name = self.ui.text(self.t("browse_new_name"))
                if name and name.strip() and "/" not in name:
                    current = current / name.strip()
                    current.mkdir(parents=True, exist_ok=True)
            elif pick == "type":
                raw = self.ui.path(self.t("browse_type_prompt"), default=str(current) + "/")
                if raw:
                    target = clean_path(raw)
                    if target.is_dir():
                        current = target
                    elif self.ui.confirm(self.t("browse_create", path=target), default=True):
                        target.mkdir(parents=True, exist_ok=True)
                        current = target
            else:
                current = pick

    def language_setting(self) -> None:
        lang = self.ui.select(self.t("language_title"),
                              [(name, code) for code, name in i18n.LANGUAGE_NAMES.items()])
        if lang:
            self.update(language=lang)
            self.ui.set_language(lang)

    def zone_setting(self) -> None:
        system = settings_mod.system_zone_name() or self.t("unknown")
        choices: list[Choice] = [(self.t("zone_system", zone=system), "")]
        choices += [(z, z) for z in COMMON_ZONES]
        choices += [(self.t("zone_other"), "other"), (self.t("back"), None)]
        pick = self.ui.select(self.t("zone_title"), choices)
        if pick is None:
            return
        if pick == "other":
            pick = self.ui.text(self.t("zone_prompt"))
            if not pick:
                return
            settings_mod.zone_from_name(pick.strip())
            pick = pick.strip()
        self.update(timezone=pick)

    def camera_setting(self) -> None:
        pick = self.ui.select(self.t("camera_title"),
                              [(profile.load(p).name, p) for p in profile.available()])
        if pick:
            self.update(profile=pick)


def attach_real_terminal() -> None:
    """On macOS, swap a stdin of /dev/tty for the terminal device it stands for.

    The installer starts the menu with `</dev/tty` (under `curl ... | bash` its stdin is the
    download). macOS's kqueue cannot watch /dev/tty itself, so prompt_toolkit would see end of
    input at once; the real device (e.g. /dev/ttys003) behind stdout or stderr works.
    """
    if sys.platform != "darwin":
        return
    try:
        if os.ttyname(0) != "/dev/tty":
            return
    except OSError:
        return
    for fd in (1, 2):
        try:
            real = os.ttyname(fd)
        except OSError:
            continue
        if real != "/dev/tty":
            replacement = os.open(real, os.O_RDWR)
            os.dup2(replacement, 0)
            os.close(replacement)
            return


def main() -> int:
    attach_real_terminal()
    # These prompts never need the terminal's cursor-position reply; asking for it only produces a
    # warning on terminals that do not answer (some SSH sessions, multiplexers).
    os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")
    try:
        ui = QuestionaryPrompter()
    except ImportError:
        print("The interactive menu needs the 'questionary' package.\n"
              "Reinstall with:  curl -fsSL https://raw.githubusercontent.com/LevelThreeCo/dashcam-sdprep/main/install.sh | bash\n"
              "or, in a source checkout:  python3 -m pip install questionary")
        return 1
    if not sys.stdin.isatty():
        print("The menu needs an interactive terminal. For scripts, use the commands: dashcam-sdprep --help")
        return 1
    first_run = not settings_mod.config_path().exists()
    try:
        current = settings_mod.load()
    except SdprepError as exc:
        print(f"ERROR: {exc}")
        return 1
    try:
        App(ui, current).run(first_run=first_run)
    except KeyboardInterrupt:
        print()
    return 0

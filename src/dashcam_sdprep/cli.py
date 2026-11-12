from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from . import __version__, disks, profile, selfexec, timefix
from .errors import SdprepError

LABEL_RE = re.compile(r"[A-Z0-9_]{1,11}")


def ensure_device_access(device: str, argv: list[str]) -> None:
    """Re-run under sudo when the card's raw device is root-only."""
    if selfexec.needs_sudo(device):
        os.execvp("sudo", ["sudo", *selfexec.command(argv)])


def cmd_profiles(_: argparse.Namespace) -> int:
    for pid in profile.available():
        p = profile.load(pid)
        print(f"{p.id:24} {p.name} (firmware {', '.join(p.firmware)})")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    found = disks.backend().list_disks()
    if not found:
        print("No SD or USB cards attached. Insert the card and try again.")
    for d in found:
        print(f"{d.ident:10} {d.size / 1e9:6.1f} GB  {d.bus:15} {d.description}")
    return 0


def cmd_verify(args: argparse.Namespace, argv: list[str]) -> int:
    prof = profile.load(args.profile)
    backend = disks.backend()
    disk = backend.resolve(args.disk, args.test_device)
    ensure_device_access(disk.device, argv)
    report = disks.verify(backend, disk, prof)
    print(f"PASS {disk.ident}: {prof.name} will accept this card "
          f"(partition {report.partition.index}, type 0x{report.partition.type_id:02X}, "
          f"{report.partition.sectors * 512 / 1e9:.1f} GB)")
    print("root: " + (", ".join(report.root) if report.root else "empty"))
    return 0


def cmd_format(args: argparse.Namespace, argv: list[str]) -> int:
    prof = profile.load(args.profile)
    if not LABEL_RE.fullmatch(args.label):
        raise SdprepError("label must be 1-11 characters of A-Z, 0-9 or _")
    backend = disks.backend()
    disk = backend.resolve(args.disk, args.test_device)
    disks.check_size(prof, disk.ident, disk.size)
    ensure_device_access(disk.device, argv)
    print(f"About to ERASE {disk.ident}: {disk.description or 'unnamed'}, {disk.size / 1e9:.1f} GB, {disk.bus}")
    if not args.yes and input(f"Type {disk.ident} to continue: ").strip() != disk.ident:
        raise SdprepError("confirmation did not match; nothing was changed")
    report = disks.format_card(backend, disk, prof, args.label)
    print(f"DONE {disk.ident}: formatted for {prof.name} and verified against its firmware rules. "
          f"Partition {report.partition.index} starts at sector {report.partition.start}; root is empty. "
          "The card is unmounted and safe to remove.")
    return 0


def cmd_timefix(args: argparse.Namespace) -> int:
    prof = profile.load(args.profile)
    refs = [timefix.parse_reference(r) for r in args.ref]
    report, count = timefix.run(Path(args.source), Path(args.out), prof, refs, args.mode,
                                args.copy, args.srt, args.label)
    print(f"Corrected {count} recordings. Report: {report}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dashcam-sdprep", description=__doc__ or
                                     "Prepare SD cards the way a dashcam's firmware expects, and fix recording times.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("menu", help="interactive menu (the default when no command is given)")
    sub.add_parser("profiles", help="list supported cameras")
    sub.add_parser("list", help="list attached SD/USB cards")
    for name, text in (("verify", "read-only: check a card against a camera's rules"),
                       ("format", "ERASE a card and prepare it for a camera")):
        p = sub.add_parser(name, help=text)
        p.add_argument("disk", help="whole card from `list`, e.g. disk4 (macOS) or sdb / mmcblk0 (Linux)")
        p.add_argument("--profile", required=True, help="camera profile id from `profiles`")
        p.add_argument("--test-device", action="store_true", help=argparse.SUPPRESS)
        if name == "format":
            p.add_argument("--label", default="DASHCAM", help="volume label (default DASHCAM)")
            p.add_argument("--yes", action="store_true", help="skip the typed confirmation")

    t = sub.add_parser("timefix", help="map recordings to real time using reference points (never edits originals)")
    t.add_argument("source", help="folder holding the recordings (the card or a copy of it)")
    t.add_argument("--out", required=True, help="new or empty folder for the report and any copies")
    t.add_argument("--profile", required=True, help="camera profile id from `profiles`")
    t.add_argument("--ref", action="append", required=True, metavar="'CAMERA=REAL'",
                   help="camera time and true time at the same instant, e.g. "
                        "'2026-10-28 02:56:47=2026-11-11 22:39:59'. Repeatable.")
    t.add_argument("--mode", choices=("offset", "linear"), default="offset",
                   help="offset: nearest earlier reference (default). linear: fit drift over 2+ references")
    t.add_argument("--copy", action="store_true", help="also copy recordings under their corrected names")
    t.add_argument("--srt", action="store_true", help="write a subtitle file per recording showing the true time")
    t.add_argument("--label", default="", help="text after each subtitle time, e.g. a time zone like KST")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    if args.cmd in (None, "menu"):
        from . import tui  # the menu's dependency is only loaded when the menu is used
        return tui.main()
    try:
        if args.cmd == "profiles":
            return cmd_profiles(args)
        if args.cmd == "list":
            return cmd_list(args)
        if args.cmd == "verify":
            return cmd_verify(args, argv)
        if args.cmd == "format":
            return cmd_format(args, argv)
        return cmd_timefix(args)
    except SdprepError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

"""Build a command that re-runs this tool in a fresh interpreter (optionally under sudo).

Works from a source checkout and from an installed package alike, because it points Python at
the directory that contains the dashcam_sdprep package instead of relying on PATH or PYTHONPATH,
which sudo resets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def command(args: list[str]) -> list[str]:
    package_parent = str(Path(__file__).resolve().parent.parent)
    boot = (f"import sys; sys.path.insert(0, {package_parent!r}); "
            "from dashcam_sdprep.cli import main; sys.exit(main(sys.argv[1:]))")
    return [sys.executable, "-c", boot, *args]


def needs_sudo(device: str) -> bool:
    return os.geteuid() != 0 and not os.access(device, os.R_OK | os.W_OK)

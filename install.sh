#!/usr/bin/env bash
# dashcam-sdprep installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/LevelThreeCo/dashcam-sdprep/main/install.sh | bash
#
# Installs into a private Python environment under ~/.local/share/dashcam-sdprep, puts the
# `dashcam-sdprep` command in ~/.local/bin, then opens the menu. Re-run it to update.
#   ... | bash -s -- --uninstall      remove it (your settings are kept)
#   ... | bash -s -- --no-launch      install without opening the menu
# Environment overrides: DASHCAM_SDPREP_SOURCE (pip-installable URL or path),
# DASHCAM_SDPREP_PYTHON (interpreter to use).
#
# Everything lives inside main(), called on the last line, so a partial download runs nothing.

main() {
  set -euo pipefail

  local repo="LevelThreeCo/dashcam-sdprep"
  local source="${DASHCAM_SDPREP_SOURCE:-https://github.com/${repo}/archive/refs/heads/main.tar.gz}"
  local data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/dashcam-sdprep"
  local venv="$data_dir/venv"
  local bin_dir="$HOME/.local/bin"
  local link="$bin_dir/dashcam-sdprep"
  local launch=1

  if [ -t 1 ]; then BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
  else BOLD=""; RED=""; GREEN=""; RESET=""; fi
  say()  { printf '%s\n' "$*"; }
  step() { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
  fail() { printf '%sError:%s %s\n' "$RED" "$RESET" "$1" >&2; [ -n "${2:-}" ] && printf '\n%s\n' "$2" >&2; exit 1; }

  for arg in "$@"; do
    case "$arg" in
      --uninstall) uninstall "$data_dir" "$link"; return ;;
      --no-launch) launch=0 ;;
      -h|--help) say "Usage: install.sh [--uninstall] [--no-launch]"; say "Installs or updates dashcam-sdprep, then opens its menu."; return ;;
      *) fail "unknown option '$arg'" ;;
    esac
  done

  local os; os="$(uname -s)"
  case "$os" in
    Darwin|Linux) ;;
    *) fail "this installer supports macOS and Linux only (found $os)." ;;
  esac

  step "Checking for Python 3.9 or newer"
  local python; python="$(find_python "$os")"
  say "    using $python ($("$python" -c 'import sys; print(sys.version.split()[0])'))"

  if ! "$python" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    fail "Python's 'venv' module is missing." "$(venv_hint "$os")"
  fi

  if [ -x "$venv/bin/python" ] && "$venv/bin/python" -c 'import sys' >/dev/null 2>&1; then
    step "Updating the existing installation"
  else
    step "Creating a private environment in $venv"
    rm -rf "$venv"
    mkdir -p "$data_dir"
    "$python" -m venv "$venv" || fail "could not create the environment." "$(venv_hint "$os")"
  fi

  step "Downloading and installing dashcam-sdprep"
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
  if ! "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$source"; then
    fail "the download or install failed (see the message above)." "Check your internet connection and run the installer again."
  fi
  "$venv/bin/dashcam-sdprep" --version >/dev/null || fail "the installed command does not run."

  mkdir -p "$bin_dir"
  ln -sf "$venv/bin/dashcam-sdprep" "$link"
  local path_note=""
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) path_note="$(add_to_path "$bin_dir")" ;;
  esac

  if [ "$os" = "Linux" ] && ! command -v mkfs.fat >/dev/null 2>&1; then
    say ""
    say "Note: formatting SD cards also needs dosfstools. Install it with:"
    say "    $(linux_install_cmd dosfstools)"
  fi

  say ""
  say "${GREEN}Installed $("$link" --version).${RESET} Run it any time with:  ${BOLD}dashcam-sdprep${RESET}"
  [ -n "$path_note" ] && say "$path_note"

  if [ "$launch" = 1 ]; then
    if { : </dev/tty; } 2>/dev/null; then
      say ""
      # stdin is the downloaded script when run as `curl ... | bash`; give the menu the keyboard.
      exec "$link" </dev/tty
    fi
    say "No terminal available to open the menu; run 'dashcam-sdprep' to start."
  fi
}

find_python() {
  local os="$1" candidate
  if [ -n "${DASHCAM_SDPREP_PYTHON:-}" ]; then
    python_ok "$DASHCAM_SDPREP_PYTHON" && { echo "$DASHCAM_SDPREP_PYTHON"; return; }
    fail "DASHCAM_SDPREP_PYTHON=$DASHCAM_SDPREP_PYTHON is not Python 3.9 or newer."
  fi
  for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    local found; found="$(command -v "$candidate" 2>/dev/null)" || continue
    # On a Mac without Command Line Tools, /usr/bin/python3 is a stub that opens an install dialog.
    if [ "$os" = "Darwin" ] && [ "$found" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
      continue
    fi
    python_ok "$found" && { echo "$found"; return; }
  done
  if [ "$os" = "Darwin" ]; then
    fail "Python 3.9 or newer was not found." \
"Install Apple's Command Line Tools (includes Python 3), then run this installer again:
    xcode-select --install
Or, with Homebrew:  brew install python"
  fi
  fail "Python 3.9 or newer was not found." "Install it, then run this installer again:
    $(linux_install_cmd python3)"
}

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

linux_install_cmd() {
  local pkg="$1"
  if command -v apt-get >/dev/null 2>&1; then
    case "$pkg" in python3-venv) echo "sudo apt-get install -y python3-venv" ;; *) echo "sudo apt-get install -y $pkg" ;; esac
  elif command -v dnf >/dev/null 2>&1; then echo "sudo dnf install -y $pkg"
  elif command -v pacman >/dev/null 2>&1; then
    case "$pkg" in python3*) echo "sudo pacman -S python" ;; *) echo "sudo pacman -S $pkg" ;; esac
  elif command -v zypper >/dev/null 2>&1; then echo "sudo zypper install -y $pkg"
  else echo "(use your distribution's package manager to install $pkg)"; fi
}

venv_hint() {
  if [ "$1" = "Linux" ]; then
    printf 'Install it, then run this installer again:\n    %s' "$(linux_install_cmd python3-venv)"
  else
    printf 'Reinstall Python (xcode-select --install, or brew install python), then run this installer again.'
  fi
}

add_to_path() {
  local bin_dir="$1" rc line
  case "${SHELL:-}" in
    */zsh)  rc="$HOME/.zshrc";  line="export PATH=\"$bin_dir:\$PATH\"" ;;
    */fish) rc="$HOME/.config/fish/config.fish"; line="fish_add_path $bin_dir" ;;
    */bash) if [ "$(uname -s)" = "Darwin" ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi
            line="export PATH=\"$bin_dir:\$PATH\"" ;;
    *) echo "Add $bin_dir to your PATH to run 'dashcam-sdprep' from anywhere."; return ;;
  esac
  mkdir -p "$(dirname "$rc")"
  if grep -qs "# dashcam-sdprep PATH" "$rc"; then
    echo "Open a new terminal window to run 'dashcam-sdprep' from anywhere."
    return
  fi
  printf '\n%s  # dashcam-sdprep PATH\n' "$line" >> "$rc"
  echo "Added $bin_dir to your PATH in $rc (takes effect in new terminal windows)."
}

uninstall() {
  local data_dir="$1" link="$2" rc
  rm -f "$link"
  rm -rf "$data_dir"
  for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.config/fish/config.fish"; do
    if [ -f "$rc" ] && grep -qs "# dashcam-sdprep PATH" "$rc"; then
      { grep -v "# dashcam-sdprep PATH" "$rc" || true; } > "$rc.dashcam-sdprep.tmp"
      mv "$rc.dashcam-sdprep.tmp" "$rc"
    fi
  done
  echo "dashcam-sdprep is uninstalled. Your settings (import folder, calibration) were kept;"
  echo "delete them with:  rm -rf \"$HOME/Library/Application Support/dashcam-sdprep\" \"$HOME/.config/dashcam-sdprep\""
}

main "$@"

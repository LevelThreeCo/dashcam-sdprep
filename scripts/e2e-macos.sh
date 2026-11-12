#!/bin/bash
# End-to-end test of the macOS backend on a 32 GB sparse disk image. No sudo needed: the image is yours.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
PROFILE=vugera-vg-701v3
DIR=$(mktemp -d)
# ASIF images need macOS 26+; older systems (and CI runners) use a classic sparse image.
if [ -z "${E2E_FORCE_SPARSE:-}" ] && diskutil image create blank --format ASIF --size 31914983424 --fs None "$DIR/card.asif" >/dev/null 2>&1; then
  IMG="$DIR/card.asif"; attach() { diskutil image attach --noMount "$IMG" >/dev/null; }
else
  hdiutil create -size 30g -type SPARSE -layout NONE "$DIR/card" >/dev/null
  IMG="$DIR/card.sparseimage"; attach() { hdiutil attach -nomount "$IMG" >/dev/null; }
fi
# Find the device for OUR image only, by its path. Never touch other attached images.
mine() { hdiutil info | awk -v p="$IMG" '$1=="image-path"{cur=$3} /^\/dev\/disk[0-9]+\t/ && cur==p && !found {sub("/dev/","",$1); print $1; found=1}'; }  # no early exit: pipefail
cleanup() { d=$(mine); [ -n "$d" ] && diskutil eject "$d" >/dev/null; rm -rf "$DIR"; }
trap cleanup EXIT
c() { "$REPO/dashcam-sdprep" "$@"; }
expect_fail() { if "$@"; then echo "UNEXPECTED PASS: $*"; exit 1; fi; }

attach; D=$(mine)

echo "== disk images are refused without the test flag; the internal disk always is"
expect_fail c verify "$D" --profile "$PROFILE"
expect_fail c format disk0 --profile "$PROFILE" --yes

echo "== Disk Utility's default FAT32 is rejected"
diskutil partitionDisk "$D" 1 MBR "MS-DOS FAT32" DASHCAM R >/dev/null
expect_fail c verify "$D" --profile "$PROFILE" --test-device

echo "== format ejects without mounting; re-attach and verify"
c format "$D" --profile "$PROFILE" --test-device --yes
for _ in $(seq 50); do [ -z "$(mine)" ] && break; sleep 0.1; done; [ -z "$(mine)" ]  # eject is asynchronous
attach; D=$(mine)
out=$(c verify "$D" --profile "$PROFILE" --test-device); echo "$out"; grep -q "root: empty" <<<"$out"
fsck_msdos -n "/dev/r${D}s1"
echo "E2E MACOS: PASS"

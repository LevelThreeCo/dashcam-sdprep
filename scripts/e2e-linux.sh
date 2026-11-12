#!/bin/bash
# End-to-end test of the Linux backend on a 32 GB sparse loop device. Needs root.
# Natively:      sudo scripts/e2e-linux.sh            (needs python3, dosfstools, fdisk/util-linux)
# Under Docker:  scripts/e2e-linux-docker.sh          (from macOS or Linux)
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
PROFILE=vugera-vg-701v3
IMG=$(mktemp /tmp/dashcam-sdprep-e2e.XXXXXX)
truncate -s 31914983424 "$IMG"
LOOP=$(losetup -fP --show "$IMG"); D=${LOOP#/dev/}
cleanup() { kill "$NODES" 2>/dev/null || true; umount /tmp/dashcam-sdprep-mnt 2>/dev/null || true
            losetup -d "$LOOP"; rm -f "$IMG"; }
# Containers have no udev. Keep partition nodes in step with sysfs the way udev would; harmless where udev runs.
( while sleep 0.2; do for p in /sys/block/$D/${D}p*; do [ -e "$p/dev" ] || continue
    n=/dev/$(basename "$p"); want=$(cat "$p/dev")
    have=$( { [ -e "$n" ] && printf '%d:%d' 0x"$(stat -c %t "$n")" 0x"$(stat -c %T "$n")"; } || true)
    [ "$want" = "$have" ] || { rm -f "$n"; mknod "$n" b "${want%:*}" "${want#*:}"; }
  done; done ) & NODES=$!
trap cleanup EXIT
c() { "$REPO/dashcam-sdprep" "$@"; }
expect_fail() { if "$@"; then echo "UNEXPECTED PASS: $*"; exit 1; fi; }

echo "== loop devices are never listed or accepted without the test flag"
out=$(c list); ! grep -qw "$D" <<<"$out"
expect_fail c verify "$D" --profile "$PROFILE"

echo "== a default mkfs.fat card (small clusters) is rejected"
printf 'label: dos\nstart=8192, type=c\n' | sfdisk -q "$LOOP"; sleep 1
mkfs.fat -F 32 -n DASHCAM "${LOOP}p1" >/dev/null
expect_fail c verify "$D" --profile "$PROFILE" --test-device

echo "== format, then verify"
c format "$D" --profile "$PROFILE" --test-device --yes
out=$(c verify "$D" --profile "$PROFILE" --test-device); echo "$out"; grep -q "root: empty" <<<"$out"
fsck.fat -n "${LOOP}p1"

echo "== files written by the camera are listed"
mkdir -p /tmp/dashcam-sdprep-mnt && mount "${LOOP}p1" /tmp/dashcam-sdprep-mnt
touch /tmp/dashcam-sdprep-mnt/REC0_20261111_210000.avi && mkdir /tmp/dashcam-sdprep-mnt/.driving_text
mp=$(python3 -c "import sys; sys.path.insert(0, '$REPO/src'); from dashcam_sdprep import disks; b = disks.LinuxBackend(); print(b.mount_point(b.resolve('$D', True)))")
[ "$mp" = /tmp/dashcam-sdprep-mnt ] || { echo "mount_point returned $mp"; exit 1; }
umount /tmp/dashcam-sdprep-mnt
out=$(c verify "$D" --profile "$PROFILE" --test-device); echo "$out"; grep -q "REC0_20261111_210000.avi, .driving_text" <<<"$out"
echo "E2E LINUX: PASS"

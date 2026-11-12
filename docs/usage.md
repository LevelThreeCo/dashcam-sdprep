# dashcam-sdprep: command reference

The menu (`dashcam-sdprep` with no arguments) covers everyday use. This page documents the plain
commands behind it, for scripting and for people who prefer typing. They need no extra packages.

[Back to the main page](../README.md)

## Commands

Everything the menu does is also available as plain commands, which need no extra packages:
`list`, `verify`, `format`, `timefix` and `profiles`. Run `dashcam-sdprep <command> --help`.
The sections below show them in use, run from a download (`./dashcam-sdprep`); with pipx, drop
the `./`.

## Prepare a card on macOS

1. **Insert the card** and copy off anything you want to keep. Formatting erases everything.
2. **Find its disk name.** Cards in the built-in SD slot show up too:
   ```sh
   ./dashcam-sdprep list
   ```
   ```
   disk4        31.9 GB  Secure Digital  Built In SDXC Reader DASHCAM
   ```
3. **Optional: check the card as it is now** (read-only):
   ```sh
   ./dashcam-sdprep verify disk4 --profile vugera-vg-701v3
   ```
   If the camera would reject the card, this prints the reasons.
4. **Format it.** You will be asked for your Mac password (raw disk access needs it), then to type
   the disk name to confirm:
   ```sh
   ./dashcam-sdprep format disk4 --profile vugera-vg-701v3
   ```
   ```
   About to ERASE disk4: Built In SDXC Reader, 31.9 GB, Secure Digital
   Type disk4 to continue: disk4
   DONE disk4: formatted for Vugera VG-701V3 and verified against its firmware rules. ...
   ```
5. **Put the card straight into the camera.** The tool ejects the card without mounting it, so
   macOS never writes its hidden `.fseventsd` / `.Spotlight-V100` folders onto it. Re-inserting
   the card into the Mac before the camera brings those back. They are harmless to the cameras
   profiled so far, but a card the camera sees first is cleaner.

If `format` says no disk is attached, the card's number changed when it was re-inserted. Run
`list` again.

## Prepare a card on Linux

1. **Insert the card** and copy off anything you want to keep.
2. **Find its disk name.** USB readers show up as `sdb`, `sdc` and so on. Built-in readers show up
   as `mmcblk0`. Only removable SD/USB disks are listed, and the disk holding your system never is:
   ```sh
   ./dashcam-sdprep list
   ```
   ```
   sdb          31.9 GB  usb             Card Reader
   ```
3. **Optional: check the card as it is now** (read-only):
   ```sh
   sudo ./dashcam-sdprep verify sdb --profile vugera-vg-701v3
   ```
4. **Format it.** The tool unmounts the card's partitions itself. If you leave out `sudo` it
   re-runs itself with `sudo` when it needs raw access:
   ```sh
   sudo ./dashcam-sdprep format sdb --profile vugera-vg-701v3
   ```
5. **Remove the card** once it says `DONE`, and put it in the camera. Some desktops automount a
   freshly formatted card. If yours does, eject it from the file manager before removing it.

## Fix recording timestamps (one-off, without saved settings)

If the camera's clock is wrong, the file names and the time burned into the video are wrong too.
`timefix` works out the real time of every recording from one or more **reference points**. It
**never modifies the originals**. It writes a report (and optionally renamed copies and
subtitles) to a separate folder.

**Take a reference point:** photograph the camera's screen with your phone while it shows the
time. The camera time is what the screen shows. The real time is the photo's own timestamp (in
your phone's photo info). Write them as `CAMERA=REAL`:

```
2025-03-01 08:12:05=2025-03-15 17:30:41
```

**Clock is set wrong but keeps time** (one photo is enough):

```sh
./dashcam-sdprep timefix /Volumes/DASHCAM --profile vugera-vg-701v3 --out ~/dashcam-fixed \
  --ref "2025-03-01 08:12:05=2025-03-15 17:30:41" --copy --srt --label "UTC+1"
```

**Clock stops whenever the car is off** (the camera shows the same time each time you start
the car): every drive needs its own photo. Each recording uses the closest earlier reference:

```sh
./dashcam-sdprep timefix /Volumes/DASHCAM --profile vugera-vg-701v3 --out ~/dashcam-fixed \
  --ref "2025-03-01 08:12:05=2025-03-15 17:30:41" \
  --ref "2025-03-01 09:02:33=2025-03-16 07:45:10"
```

**Clock runs fast or slow** (the offset grows over days): take two photos days apart and fit the
drift with `--mode linear`.

You get, in the `--out` folder:
- `timefix.csv`: every recording's original name, camera time, real time, correction, duration and
  SHA-256 hash (so you can show the originals were not altered).
- With `--copy`: copies renamed to the real time, e.g. `2025-03-16_10-05-00_REC0.avi`. Each copy
  is hash-checked against its original.
- With `--srt`: a subtitle file per recording showing the real time, second by second. Players
  such as VLC show it when the `.srt` sits next to the video with the same name (use it together
  with `--copy`).

To tell which clock problem you have, take two photos at different times and compare the gap
between camera time and real time. If the gap is the same, the clock keeps time. If the camera
time barely moved between drives, the clock stops when the car is off (usually a dead backup
battery or capacitor inside the camera).

## How it works

- `verify` reads the card's partition table and FAT boot sector directly from the device and runs
  the profile's checks: partition type, file system, bytes per sector, sectors per cluster for
  the card's size, and the FAT32 layout fields the firmware inspects. It also lists the files in
  the card's root folder, without mounting it.
- `format` writes a new MBR with one FAT partition (macOS: `diskutil`; Linux: `sfdisk`, starting at
  4 MiB), builds the file system with the cluster size the profile demands (macOS: `newfs_msdos`;
  Linux: `mkfs.fat`), re-runs `verify`, confirms the root folder is empty, and leaves the card
  unmounted.
- Safety: only whole removable disks on an SD or USB bus are accepted. The system disk, internal
  drives, partitions, disk images and loop devices are refused, and you must type the disk name
  before anything is erased.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest                    # unit tests, no hardware needed
scripts/e2e-macos.sh                # macOS: formats a 32 GB sparse disk image end to end
scripts/e2e-linux-docker.sh         # Linux: same test on a loop device in a privileged container
```


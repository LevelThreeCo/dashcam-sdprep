# Vugera VG-701V3: evidence for the profile

Firmware analysed: **1.0.4** (Korean release, update file `VG-701V3_1.0.4_KOR_Knl_App_uBoot.rom`,
build date 2020-02-19). The device is Allwinner-based: the update file carries an `eGON.BT0` boot
header and an XZ-compressed squashfs application partition. That partition holds the camera
application (`cdr`) and two small SD card utilities (`fat`, `fsck`). Addresses below are virtual
addresses in those 32-bit ARM binaries.

**Tested on a device:** a camera running firmware 1.0.3 (it reports its version in `setup.ini`) rejected a 32 GB card formatted by SD Card Formatter and accepted the same card formatted by this tool, then recorded normally. The rules were analysed in 1.0.4.

No firmware or binaries are distributed with this project. The notes describe behaviour so the
profile can be checked against the original firmware.

## What happens when a card is inserted

1. `cdr` runs `./fsck mmc0 -a` and reads its result. `fsck` is a modified `dosfsck` with an extra
   check stage (it prints "mBrain's FAT Checker").
2. `cdr` (`mountSDCard`, `0x534e0`) runs `./fat mmc0 -c`. A nonzero exit means the card is not
   mounted.
3. Only then does it run `mount -t vfat /dev/mmcblk0p1 /media/mmcblk0p1`.
4. On failure the UI receives `CDR_QCMD_UI_SD_FORMAT_NEED` and shows the "format the card?" popup,
   which must be answered on the touchscreen.

## The rules, and where they come from

| Rule | Where it is checked |
|---|---|
| MBR partition table; first partition of type 0x01, 0x04, 0x06, 0x0B, 0x0C or 0x0E is used | `fat` partition scan, `0x94f0` onwards. Extended partitions (0x05, 0x0F) are followed |
| The camera mounts partition 1 as `vfat` | `cdr` mount command string |
| 512 bytes per sector | `fat` `mkCheck_format` (`0x9d80`) |
| Boot sector ends in 0x55AA | `fat` `mkCheck_format` |
| Sectors per cluster must equal a value derived from the file system's total sector count | `fat` `mkCheck_format` calls a lookup at `0x91a4` |
| File system type field (offset 0x52) starts with `FAT32` | `fsck`, `0xa7d8` |
| FAT32 layout: 16-bit FAT size = 0, 32-bit FAT size != 0, 16-bit sector count = 0, 32-bit sector count != 0, root entry count = 0 | `fsck`, `0xa814` to `0xa8a4` |
| Boot sector bytes 0x34 to 0x3F (BPB reserved) are zero | `fsck`, `0xa8b4` loop |
| bytes per sector x sectors per cluster = 65536 | `fsck`, `0xa8f0` |

The lookup at `0x91a4` (total sectors, then required sectors per cluster):

| Total sectors below | Sectors per cluster |
|---|---|
| 0x20000 (64 MiB) | 1 |
| 0x40000 (128 MiB) | 2 |
| 0x80000 (256 MiB) | 4 |
| 0x100000 (512 MiB) | 8 |
| 0x200000 (1 GiB) | 8 |
| 0x400000 (2 GiB) | 32 |
| 0x800000 (4 GiB) | 64 |
| anything larger | 128 |

Below 4 GiB this table and the 64 KiB rule in `fsck` contradict each other, so the profile only
covers cards of 4 GiB (0x800000 sectors) and up. Every current high-endurance card is far above
that.

The camera's own format routine agrees with these rules: it creates one primary partition of type
0x0C with `fdisk`, then runs `mkdosfs` with 128 sectors per cluster.

## Why common formatters fail

- SD Card Formatter and macOS Disk Utility on a 32 GB card: FAT32 with 32 KiB clusters
  (64 sectors per cluster). Fails the cluster rule.
- Any of them on 64 GB and larger cards: exFAT (partition type 0x07). Fails the partition type
  and file system rules.

## Card layout written by the camera

- Recordings go straight into the root folder. The file scanner is called on
  `/media/mmcblk0p1/`, and no sub-folder strings exist for recordings.
- Name format: `<KIND><channel>_<YYYYMMDD>_<HHMMSS>.avi`, where KIND is `REC`, `EVT`, `MAN`,
  `IMP` or `MOT`. Locked files get `_KEP` before the extension.
- The camera creates a hidden `.driving_text` folder itself (it checks first, so an existing one
  is fine).
- The camera reads and writes `setup.ini` in the root (see below).
- In practice the first channel's files are numbered `1` (e.g. `REC1_...`). `MOT` and `IMP` files are written in parking mode.

## The clock

- At boot the camera reads the hardware RTC. If the year is 2019 or earlier it resets the clock
  to 2020-01-01. It does not save or restore the time anywhere else.
- `setup.ini` in the card's root has a `[TIME]` section (`set`, `year`, `mon`, `day`, `hour`,
  `min`, `sec`, `localset`, `localtime`). The `[Model]` entry must be `VG-701V3`. With `set = 1`
  the new time is **not** applied silently: the camera raises the same "format the card?" popup,
  and only after **YES** is pressed does it set the clock (and then format the card). Cameras
  without a working touchscreen cannot use this route.
- A GPS fix sets the clock automatically (RMC sentence), adjusted by the configured time zone.
- The firmware contains a function that would set the clock from the newest recording's file
  name, but nothing calls it, so naming a file with the right date has no effect.

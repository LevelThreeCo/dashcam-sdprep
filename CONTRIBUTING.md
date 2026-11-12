# Contributing

This project is provided as is. There is no support and no promise that issues or pull requests
will be answered. Contributions are still welcome, especially **profiles for more cameras**.

## Adding a camera profile

A profile is a JSON file in `src/dashcam_sdprep/profiles/` plus an evidence write-up in
`docs/profiles/`. Copy `vugera-vg-701v3.json` and change what differs.

| Field | Meaning |
|---|---|
| `id` | File name without `.json`, lowercase, e.g. `brand-model` |
| `firmware` | Firmware versions the rules were confirmed on |
| `evidence` | Path to the write-up in `docs/profiles/` |
| `partition.index` | Which MBR partition the camera mounts (almost always 1) |
| `partition.types` | MBR partition type ids the camera accepts (FAT32 is 11 or 12) |
| `filesystem.type` | `FAT32` (the only one supported so far) |
| `filesystem.bytes_per_sector` | Usually 512 |
| `filesystem.cluster_table` | `[limit, sectors_per_cluster]` rows: below `limit` total sectors, require that cluster size |
| `filesystem.cluster_default` | Sectors per cluster when the size is above every limit |
| `filesystem.cluster_bytes` | Exact cluster size in bytes if the camera insists on one, else omit |
| `filesystem.min_card_bytes` / `max_card_bytes` | Card sizes the profile is valid for |
| `recordings.pattern` | Regex for recording file names with named groups `date` (YYYYMMDD) and `time` (HHMMSS), optionally `kind`, `channel`, `locked` |

Every rule needs evidence. Good evidence is, in order of strength:
1. Firmware analysis: where the check lives and what it compares.
2. A test: the camera accepts a card formatted one way and rejects one formatted another way.
3. Vendor documentation.

Write the evidence **in your own words**. Do **not** commit firmware images, extracted binaries,
or large disassembly listings. Short references (function names, addresses, the values compared)
are enough to let others check your work.

## Before opening a pull request

```sh
.venv/bin/pytest
scripts/e2e-macos.sh          # if you changed macOS code
scripts/e2e-linux-docker.sh   # if you changed Linux code
```

Never test `format` against a real disk you care about. The end-to-end scripts use a sparse image
or loop device for that reason.

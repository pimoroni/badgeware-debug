# Firmware debug bundle

A firmware debug bundle carries what a host needs to show and debug a badge's Python: the firmware's filesystem and the source of every frozen module. Board CI builds one alongside each firmware and publishes it as `<board>-<version>-micropython-simulator.zip`.

```sh
tools/bundle.py <board> <repo> [ref] --output DIR
tools/bundle.py tufty ../tufty2350 HEAD --output build/tufty-bundle
```

`<repo>` is read at `ref` (default `HEAD`) with `git archive`. The runtime in `device/python/` comes from the badgeware-debug checkout the tool runs from, so CI runs the pinned checkout's tool.

The tool replaces only the entries below in `--output`.

## Contents

| Path | Contents |
|---|---|
| `filesystem/` | The firmware filesystem in the badge's flat layout |
| `filesystem.json` | `{"files": {"/<path>": size}}` for every file in `filesystem/` |
| `fonts-manifest.json` | `{"vector": [...], "pixel": [...]}`: `.af` files in `filesystem/fonts`, `.ppf` files in `filesystem/rom/fonts` |
| `frozen/` | The source of every frozen module, by frozen path |
| `frozen.json` | `{"files": [...]}`: frozen paths, board modules first, then the badgeware-debug runtime |
| `bundle.json` | Provenance: `board`, `commit` and `version` of the board repo, and `debug.commit` and `debug.version` of badgeware-debug |

### Filesystem layout

| Board repo | Bundle |
|---|---|
| `firmware/apps` | `filesystem/apps` |
| `firmware/fonts` | `filesystem/fonts` |
| `firmware/assets` | `filesystem/assets` |
| `modules/common/badgeware` | `filesystem/badgeware` |
| `romfs` | `filesystem/rom` |

In `.py` files copied to `filesystem/`, `.frozen/badgeware/` becomes `badgeware/`, so the `badgeware` package imports from the filesystem.

The bundle has no simulator stand-ins (`powman`, `rtc` and so on). A simulator adds those on top: the Badgeware IDE imports a bundle with badgeware-wasm's `simulator/overlay/`.

### Frozen paths

Frozen paths are relative to the frozen roots: the board's `modules/python` and `modules/common`, then `device/python`. They match the paths the firmware's frozen modules report, minus the `.frozen/` prefix.

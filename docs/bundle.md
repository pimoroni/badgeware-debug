# Firmware debug bundle

A firmware debug bundle carries what a host needs to show and debug a badge's Python: the firmware's filesystem, the source of every frozen module, and the local variable names that frozen bytecode lacks. Board CI builds one alongside each firmware and publishes it as `<board>-<version>-micropython-simulator.zip`.

```sh
tools/bundle.py <board> <repo> [ref] --output DIR --mpy-cross PATH
tools/bundle.py tufty ../tufty2350 HEAD --output build/tufty-bundle --mpy-cross ../micropython/mpy-cross/build/mpy-cross
```

`<repo>` is read at `ref` (default `HEAD`) with `git archive`. The runtime in `device/python/` comes from the badgeware-debug checkout the tool runs from, so CI runs the pinned checkout's tool. `mpy-cross` must support `--locals` (pimoroni/micropython `bw-1.29.0-ide`).

The tool replaces only the entries below in `--output`.

## Contents

| Path | Contents |
|---|---|
| `filesystem/` | The firmware filesystem in the badge's flat layout |
| `filesystem.json` | `{"files": {"/<path>": size}}` for every file in `filesystem/` |
| `fonts-manifest.json` | `{"vector": [...], "pixel": [...]}`: `.af` files in `filesystem/fonts`, `.ppf` files in `filesystem/rom/fonts` |
| `frozen/` | The source of every frozen module, by frozen path |
| `frozen.json` | `{"files": [...]}`: frozen paths, board modules first, then the badgeware-debug runtime |
| `locals.json` | `{"<frozen path>": [function, ...]}` for every module with functions |
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

### Locals tables

Each function entry is the JSON `mpy-cross --locals` writes:

```json
{"name": "draw", "line": 12, "n_state": 9, "locals": ["self", "x", "y"]}
```

`line` is the function's first line, `n_state` its state size and `locals` its local names in slot order. A debugger maps physical state slot `s` to local `n_state - 1 - s`, and hides slots below the locals (value stack temporaries).

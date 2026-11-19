# Device runtime

`device/python/` is frozen into every board's firmware. It owns everything the IDE and debugger need on the badge, and holds no board-specific drawing.

| Module | Role |
|---|---|
| `_boot.py` | Mounts LittleFS at `/` through the guarded `cdcaux.BlockDevice`, formatting it if the mount fails |
| `ide.py` | Facade the IDE and firmware call: `import ide; ide.info()` etc. |
| `ide_files.py` | Raw REPL file helpers: `info`, `ls`, `hashes`, `get`, `put`, `rm`, `mkdir`, `rename` |
| `ide_rom.py` | ROM filesystem helpers: `rom_info`, `rom_put`, `rom_remove` |
| `ide_session.py` | IDE sessions: `serve`, `execute`, `safe_mode`, the hold screen loop, `IDEStop` |
| `ide_debug.py` | The debugger, driven over the aux channel by `ide.execute(path, debug=True)` |

## The `ide` facade

The IDE calls these over the raw REPL on firmware without the aux channel roles, and the firmware calls `serve` and `safe_mode` from `main.py`. The names are stable.

| Function | Use |
|---|---|
| `info()` | Prints JSON: MicroPython version, machine, disk usage, free heap |
| `ls(path="/")` | Prints JSON `[[path, size], ...]`, recursive, size `-1` for directories |
| `hashes(paths)` | Prints JSON `{path: sha256 or null}` |
| `get(path)` | Prints the file as base64 lines |
| `put(path, size)` | Receives `size` raw bytes on stdin, ACK (`0x06`) per 4 KB chunk |
| `rm(path)`, `mkdir(path)`, `rename(source, destination)` | File operations, creating parent directories as needed |
| `rom_info()`, `rom_put(path, size)`, `rom_remove(path)` | ROM filesystem usage and edits, rewriting only changed blocks |
| `execute(path, debug=False)` | Runs a file or app directory in IDE mode, optionally under the debugger |
| `serve()` | Called at the top of `main.py`: shows the hold screen while the IDE holds the badge, then runs a launch requested by `app.run` |
| `safe_mode()` | Shows the board's safe mode screen |

## Badgeware dependencies

The runtime runs after the board's `boot.py` has imported `badgeware`, and uses these builtins from it: `badge` (`poll`, `pressed`, `held`, `disk_free`), `launch`, `run`, `reset`, `fatal_error` and `ide_mode`.

## Board hooks

Each board provides a frozen `ide_display` module with three functions. `ide_session` imports it and calls nothing else board-specific.

```python
def hold():
    ...


def safe_mode():
    ...


def error(title, message):
    ...
```

- `hold()` shows the hold screen when the IDE has locked the badge. It returns `None` for a screen drawn once, or a `draw()` callable that `ide_session` calls every 20 ms while the hold lasts.
- `safe_mode()` shows the safe mode screen. It returns `None` for a screen drawn once, after which `main.py` returns to the REPL, or a `draw()` callable that `ide_session` calls every 20 ms forever.
- `error(title, message)` shows that an IDE run failed. The board's `fatal_error` calls `ide_session.error(title, message)` in IDE mode, after printing (and drawing, if it does) the error; `ide_session` calls the hook and raises `IDEStop`, which ends the run. `message` is the formatted exception.

Draw-once screens suit e-ink, where a redraw per loop would refresh the panel. A `draw()` callable suits Blinky's scrolling text.

| Board | `hold()` | `safe_mode()` | `error()` |
|---|---|---|---|
| Tufty 2350 | Draws a message box once in HIRES | Same, safe mode text | Nothing: `fatal_error` has already drawn the error |
| Badger 2350 | Draws a message box once | Same, safe mode text | Waits for the e-ink update to finish |
| Blinky 2350 | Prints the message and returns a scroller | Same, safe mode text | Shows `ERR` |

## Badgeware integration

The board's `badgeware` package calls into the runtime only in IDE mode, and imports it there, so the simulator, which has no `ide_session`, never loads it:

```python
def quit_to_ide(_pin):
    import ide_session
    micropython.schedule(ide_session.stop, None)
```

```python
if ide_mode:
    import ide_session
    ide_session.error(title, error)
```

`launch(path)` still calls `cdcaux.app(path)` to register the running app with the aux channel.

## Board integration

- CMake: pass `-DBADGEWARE_DEBUG_DIR=<checkout>`. `mpconfigboard.cmake` forwards it to the frozen manifest as `MICROPY_MANIFEST_BADGEWARE_DEBUG_DIR`, and `usermodules.cmake` includes `${BADGEWARE_DEBUG_DIR}/device/cdcaux/micropython.cmake` and sets `BADGEWARE_MODEL` and `BADGEWARE_VERSION` on `usermod_cdcaux` for the ident.
- Manifest: `freeze("$(BADGEWARE_DEBUG_DIR)/device/python")`.
- `mpconfigboard.h`: `MICROPY_BOARD_END_SOFT_RESET()` calls `cdcaux_soft_reset()`, and `MICROPY_ENABLE_VM_ABORT` and `MICROPY_PYEXEC_ENABLE_VM_ABORT` are set.
- `main.py`: `import ide`, `ide.serve()` first, then `ide.safe_mode()` when the board decides to enter safe mode.
- `modules/common/ide_display.py`: the hooks above.
- CI: `ci/micropython.sh` pins `BADGEWARE_DEBUG_VERSION` and clones this repository into `$CI_BUILD_ROOT/badgeware-debug`, unless `BADGEWARE_DEBUG_DIR` is already set, which local builds use to point at a working checkout.

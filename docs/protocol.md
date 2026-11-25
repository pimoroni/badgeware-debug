# cdcaux protocol

`cdcaux` drives the second USB CDC interface ("Tufty Debug", "Badger Debug", "Blinky Debug"), used by the Badgeware IDE. It carries the debugger, file access to the badge's LittleFS, and launch control, while the first interface stays a normal REPL.

The module lives in `device/cdcaux/` and is the same on Tufty 2350, Badger 2350 and Blinky 2350. Boards include it by path; see [board integration](runtime.md#board-integration).

## Layout

| File | Role |
|---|---|
| `device/cdcaux/cdcaux.c` | Arbiter: framing, routing, scheduling, the guarded block device, the Python API |
| `device/cdcaux/cdcaux_fs.c` | `fs` role: file commands, change and state events |
| `device/cdcaux/cdcaux_app.c` | `app` role: lock, unlock, run, stop, the held state and pending launch |
| `device/cdcaux/cdcaux.h` | Shared declarations and the `cdcaux_role_t` interface |

A role is a `cdcaux_role_t` (command prefix, dispatch, service, disconnected, soft reset). The arbiter loops over `cdcaux_roles[]` for every one of those, so adding a role is a new file plus one entry in that list. Dispatch tries each role in order until one claims the command; the prefix only decides whether the arbiter handles a line at all (`fs.lock` is matched by the `fs.` prefix and claimed by the `app` role).

## Protocol

### Framing (owned by the arbiter)

- A message is one JSON object on one line, terminated by `\n`. Lines are at most 1024 bytes.
- A message may carry a binary payload: the header includes `"bytes": N` and exactly N raw bytes follow the newline. The debugger's `screen` event uses the same convention.
- Requests have `"cmd"` and `"id"`. The arbiter handles any `cmd` matching a role prefix (`fs.`, `app.`), whether or not Python has claimed the channel. Other lines go to Python while it holds `cdcaux.claim(True)` (the debugger) and are dropped otherwise.
- Byte `0x03` outside a payload returns the ident line. Its `features` include `"fs"` and `"app"`.
- Responses echo `"id"`. Success: `{"event": "<cmd>", "id": ...}` plus fields. Failure: `{"event": "error", "id": ..., "errno": "ENOENT", "message": "..."}`.
- Requests are handled in order and may be pipelined. The badge applies back-pressure by not draining the TinyUSB RX FIFO.
- Role commands wait while `/` isn't mounted and while a requested soft reset is pending.

### Paths

- Reads (`fs.stat`, `fs.list`, `fs.read`, `fs.hash`) work on any mount, including `/rom`. `fs.list /` includes mount points.
- Writes (`fs.open`, `fs.mkdir`, `fs.remove`, `fs.rename`) address the LittleFS volume at `/` only. A path on another mount returns `EROFS`.
- Absolute, `/`-separated, UTF-8, no trailing `/`, at most 255 bytes. Empty, `.` and `..` components return `EINVAL`.
- `mtime` is integer seconds since 1970, from MicroPython's `t` attribute, stamped by the badge when a file is closed.

### States and write policy

| State | Meaning | Host writes |
|---|---|---|
| `idle` | no app registered (safe mode, REPL) | allowed |
| `running` | `launch()` registered an app, including the launcher | allowed, except inside the app's path or any ancestor of it (`EBUSY`) |
| `held` | after `fs.lock` or `app.stop` | allowed; no app runs |

Reads are always allowed and see the last committed version of a file.

### fs commands

| Command | Fields | Reply |
|---|---|---|
| `fs.info` | | `version`, `block_size`, `block_count`, `blocks_used`, `name_max`, `chunk`, `handles`, `state`, `app` |
| `fs.stat` | `path` | `type` (`file`/`dir`), `size`, `mtime` |
| `fs.list` | `path`, `offset` (0), `limit` (64, max 256) | `entries` of `{name, type, size, mtime}`, `more` |
| `fs.read` | `path`, `offset`, `length` (max `chunk`) | `eof`, then `bytes` raw bytes |
| `fs.hash` | `path` | `sha256`, `size` |
| `fs.open` | `path`, `size`, `exclusive` | `handle` |
| `fs.write` | `handle`, `offset` (must equal bytes written so far), payload (max `chunk`) | `written` |
| `fs.close` | `handle` | `size` |
| `fs.abort` | `handle` | |
| `fs.mkdir` | `path`, `parents` | |
| `fs.remove` | `path`, `recursive` | |
| `fs.rename` | `from`, `to` | |
| `fs.watch` | `enable` | |

- Writes go to `/.fs-tmp-<handle>` and are renamed over the target on close, so power loss or a disconnect leaves the old file intact. Disconnect aborts open handles; a soft reset forgets them; leftover temp files are removed by the first fs command after a reset.
- With `fs.watch` on, the badge sends `{"event": "fs.changed"}` 100 ms after Python first changes the filesystem (no paths; the host re-lists), and `{"event": "fs.state", "state": ..., "app": ...}` whenever the state or registered app changes, including after every soft reset. The watch setting survives soft resets and is cleared on disconnect.

### app commands

| Command | Fields | Reply |
|---|---|---|
| `fs.lock` | | `state` (`held`), sent after the soft reset once `/` is mounted |
| `app.stop` | | same as `fs.lock` |
| `fs.unlock` | `resume` | `state` |
| `app.run` | `path`, `debug` | sent before the soft reset |

- Lock and stop set the held flag and soft reset. `main.py` calls `ide.serve()`, which shows the board's hold screen (`ide_display.hold()`). With no app and nothing executing (safe mode, idle REPL) the reply comes at once without a reset.
- The hold ends on `fs.unlock`, `app.run`, disconnect, a button press, or 10 s without any request.
- `app.run` stores the launch in C state and soft resets; `ide.serve()` calls `ide.execute(path, debug)`. When the app ends, or HOME raises `ide_session.IDEStop`, the badge soft resets into the launcher.

### errno values

`ENOENT`, `EEXIST`, `ENOTDIR`, `EISDIR`, `ENOTEMPTY`, `ENOSPC`, `ENAMETOOLONG`, `EINVAL`, `EBADF`, `EIO`, `ECORRUPT`, `ENOMEM`, `EROFS`, `EPERM`, `EBUSY`. Anything else is `EIO`.

## Design rationale

### Why the receive callback only schedules

`tud_cdc_rx_cb` runs inside `tud_task` with `in_usbd_task` set. Doing file work there would stall every USB interface during a flash erase, and a reply that fills the TX FIFO calls `mp_usbd_task()`, which raises "TinyUSB callback can't recurse". So the callback only schedules the arbiter's static `mp_sched_node_t`; the arbiter reads the FIFO and dispatches from the MicroPython scheduler, at most 8 commands per run so a busy host can't starve the running app.

### Why the guard exists

Scheduled callbacks run at VM pending checks, in event waits, and in any C code that calls `mp_event_handle_nowait()`. `rp2.Flash` calls that after every block read and write, so a scheduled arbiter can run in the middle of a Python LittleFS operation, and LittleFS is not reentrant.

The shared `_boot.py` mounts through `cdcaux.BlockDevice(rp2.Flash())`. Every block device call raises a guard count; while it is non-zero the arbiter defers, and the last guard exit reschedules it. Between block device calls LittleFS is pure C with no scheduler entry points, so "inside a block device call" covers every point where the arbiter could interrupt LittleFS. This needs no MicroPython patch. `cdcaux.write` from Python takes the same guard, so arbiter replies never interleave with a debugger message, and the arbiter takes it while it runs, so it is never re-entered.

The scheduled callback must never raise: an exception escaping a C scheduler node leaves the scheduler locked. Each command runs under `nlr_push`, errors become `error` replies, and a caught `KeyboardInterrupt` is re-scheduled so it still reaches the app.

### Why readiness walks the mount table

`mp_vfs_lookup_path("/")` always returns the virtual root, even when LittleFS is mounted at `/`, so asking it "is `/` mounted" never succeeds. `cdcaux_fs_ready()` walks the mount table for a mount of length 1 instead. Getting this wrong deferred every role command forever.

### Why the receive state resets on disconnect

A command that waits (for the mount, or for a pending reset) holds the line buffer, and everything behind it in the FIFO waits too, including the `0x03` ident byte. If the host gives up and reconnects, a stale half-command would block the new session's probe. On disconnect the arbiter drops its line, payload and inbox state and flushes the FIFO.

### Soft resets

- Lock, stop and run soft reset with `mp_sched_vm_abort()`, which apps can't catch (unlike `machine.soft_reset()`, a `SystemExit` that a bare `except:` swallows). `MICROPY_ENABLE_VM_ABORT` and `MICROPY_PYEXEC_ENABLE_VM_ABORT` are set in each board's `mpconfigboard.h`; with exit-code handling off, `PYEXEC_ABORT` equals `PYEXEC_FORCED_EXIT`, so the abort soft resets.
- `mp_sched_vm_abort()` only sets a flag, so the arbiter also schedules itself to make the VM call `mp_handle_pending()`.
- At an idle REPL there is no abort target, so the arbiter queues Ctrl-D on stdin instead. Either way it switches to the friendly REPL first so `main.py` runs.
- USB stays enumerated through a soft reset (unless an app activated `machine.USBDevice`), so the host keeps its ports open.
- `MICROPY_BOARD_END_SOFT_RESET()` calls `cdcaux_soft_reset()`, which clears the claim, buffers, handles and guard, and keeps the held flag, pending launch, pending reply and watch setting in C statics. Static scheduler nodes and static soft timers survive a soft reset by design, so the arbiter's node is never cleared by hand.

### Memory

- Line, payload and inbox buffers (9 KB) live on the GC heap behind a root pointer and are dropped at soft reset.
- Statics that survive a reset come to about 1 KB of SRAM.

### Costs to know about

- Flash erase and program use multicore lockout, and picovector rasterises on core1, so host writes while an app runs stall rendering per erased block.
- Requests are serviced at VM pending checks, so an app stuck in a long native call delays replies.

## Debugger support

These live in `device/python/ide_debug.py`, the MicroPython fork's `mpy-cross` and `py/profile.c`, and the IDE, but exist for the same channel.

- **Stepping into library code.** `ide_debug` normally traces only user files (absolute paths not under `/.frozen`). While the debugger is stepping in, it also traces frozen modules, so step into enters `badgeware` code such as the closure returned by `text.scroll`. Plain runs, Continue and breakpoints stay user-only and cost nothing extra.
- **Frame identity.** Each stack frame reports `first` (`co_firstlineno`) alongside `file`, `line` and `name`, so a function can be identified exactly, even among closures that share a name.
- **Local names for frozen code.** Frozen modules carry no local names, so `py/profile.c` reports unnamed slots as `local_NN`, numbered by physical state slot, with value-stack temporaries below the locals. The fork's `mpy-cross --locals <file>` writes, for every function, its name, first line, `n_state` and local names in slot order, using the same compiler and byte-identical bytecode. The IDE maps slot `s` to local `n_state - 1 - s` and hides slots below the locals. The fork's `profile.c` also unwraps closure cells in unnamed locals so captured values show.
- **Release asset.** Board CI runs `tools/bundle.py` from the pinned badgeware-debug checkout and publishes `<board>-<version>-micropython-simulator.zip`, a [firmware debug bundle](bundle.md): the firmware filesystem, every frozen source under `frozen/`, `locals.json`, and `bundle.json` recording the board and badgeware-debug commits. These come from the same sources and mpy-cross as the firmware, so source and tables match the badge.

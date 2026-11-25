# badgeware-debug

Device-side runtime and tooling for debugging Pimoroni Badgeware badges (Tufty 2350, Badger 2350, Blinky 2350) from the [Badgeware IDE](https://github.com/pimoroni/badgeware-ide).

## Layout

| Path | Contents |
|---|---|
| `device/cdcaux/` | `cdcaux` C module: the second USB CDC interface, file and app roles, guarded block device |
| `device/python/` | Shared Python runtime frozen into each board: `ide` facade, file and ROM helpers, IDE sessions, debugger, `_boot.py` |
| `tools/bundle.py` | Builds a firmware debug bundle from a board's firmware repo |
| `tools/fsclient.py` | Command line client and hardware test suite for the aux channel's `fs` and `app` roles |
| `docs/` | [Protocol and design rationale](docs/protocol.md), [device runtime and board hooks](docs/runtime.md), [bundle format](docs/bundle.md) |
| `tests/` | Off-hardware tests |
| `ci/` | Lint configuration and CI helpers |

A VS Code extension may live here later.

## Using it from a board

Boards consume this repository by path, like picovector:

```sh
cmake ... -DBADGEWARE_DEBUG_DIR=/path/to/badgeware-debug
```

See [board integration](docs/runtime.md#board-integration). Board CI clones a pinned version; set `BADGEWARE_DEBUG_DIR` before sourcing `ci/micropython.sh` to build against a local checkout.

The MicroPython side (the aux CDC interface, and settrace local names for source and frozen code) is in the pimoroni/micropython fork, branch `feature/v4.0.0`.

## Tools

```sh
tools/bundle.py tufty ../tufty2350 --output build/tufty-bundle
pip install -r tools/requirements.txt
python3 tools/fsclient.py info
python3 tools/fsclient.py test
```

`fsclient.py test` writes to `/.fstest` on a connected badge and removes it afterwards.

## Tests

```sh
source ci/python.sh
qa_device_check && qa_tools_check && qa_tests_check
qa_tests_run
```

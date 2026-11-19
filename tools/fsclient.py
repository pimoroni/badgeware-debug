import argparse
import hashlib
import json
import os
import sys
import time

import serial
from serial.tools import list_ports

VID = 0x2E8A
PID = 0x1101
CHUNK = 4096


class DeviceError(Exception):
    def __init__(self, message):
        super().__init__(f"{message.get('errno')}: {message.get('message')}")
        self.errno = message.get("errno")


class Badge:
    def __init__(self, port, timeout=15):
        self.serial = serial.Serial(port, timeout=timeout)
        self.next_id = 1
        self.events = []

    def close(self):
        self.serial.close()

    def read_message(self):
        line = self.serial.readline()
        if not line:
            raise TimeoutError("no response from badge")
        message = json.loads(line)
        if "bytes" in message and message.get("event") == "fs.read":
            message["data"] = self.serial.read(message["bytes"])
        return message

    def send(self, cmd, payload=None, **fields):
        request_id = self.next_id
        self.next_id += 1
        header = {"cmd": cmd, "id": request_id, **fields}
        if payload is not None:
            header["bytes"] = len(payload)
        self.serial.write(json.dumps(header).encode() + b"\n")
        if payload:
            self.serial.write(payload)
        return request_id

    def wait(self, request_id):
        while True:
            message = self.read_message()
            if message.get("id") != request_id:
                self.events.append(message)
                continue
            if message.get("event") == "error":
                raise DeviceError(message)
            return message

    def request(self, cmd, payload=None, **fields):
        return self.wait(self.send(cmd, payload, **fields))

    def wait_event(self, name, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, event in enumerate(self.events):
                if event.get("event") == name:
                    return self.events.pop(index)
            try:
                self.events.append(self.read_message())
            except TimeoutError:
                pass
        raise TimeoutError(f"no {name} event")

    def get(self, path):
        data = b""
        while True:
            reply = self.request("fs.read", path=path, offset=len(data), length=CHUNK)
            data += reply["data"]
            if reply["eof"]:
                return data

    def put(self, path, data):
        handle = self.request("fs.open", path=path, size=len(data))["handle"]
        try:
            for offset in range(0, len(data), CHUNK):
                self.request("fs.write", data[offset:offset + CHUNK], handle=handle, offset=offset)
        except Exception:
            self.request("fs.abort", handle=handle)
            raise
        return self.request("fs.close", handle=handle)


def find_port():
    for port in list_ports.comports():
        if port.vid != VID or port.pid != PID:
            continue
        try:
            with serial.Serial(port.device, timeout=1) as probe:
                probe.reset_input_buffer()
                probe.write(b"\x03")
                line = probe.readline()
            if b"badgeware-ide" in line:
                return port.device
        except (OSError, serial.SerialException):
            continue
    raise SystemExit("no badge debug port found")


def expect_error(errno, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except DeviceError as e:
        if e.errno != errno:
            raise AssertionError(f"expected {errno}, got {e.errno}") from e
        return
    raise AssertionError(f"expected {errno}, got success")


def run_tests(badge):
    root = "/.fstest"
    results = []

    def check(name, fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, repr(e)))

    try:
        badge.request("fs.remove", path=root, recursive=True)
    except DeviceError:
        pass

    info = badge.request("fs.info")
    print(f"info: {info}")

    def mkdir():
        badge.request("fs.mkdir", path=f"{root}/a/b", parents=True)
        assert badge.request("fs.stat", path=f"{root}/a/b")["type"] == "dir"
        expect_error("EEXIST", badge.request, "fs.mkdir", path=f"{root}/a")

    def roundtrip():
        for size in (0, 1, CHUNK - 1, CHUNK, CHUNK * 3 + 17):
            data = os.urandom(size)
            path = f"{root}/a/file-{size}.bin"
            assert badge.put(path, data)["size"] == size
            assert badge.get(path) == data
            assert badge.request("fs.stat", path=path)["size"] == size
            assert badge.request("fs.hash", path=path)["sha256"] == hashlib.sha256(data).hexdigest()

    def overwrite_is_atomic():
        path = f"{root}/a/replace.txt"
        badge.put(path, b"old")
        handle = badge.request("fs.open", path=path)["handle"]
        badge.request("fs.write", b"new contents", handle=handle, offset=0)
        assert badge.get(path) == b"old"
        badge.request("fs.close", handle=handle)
        assert badge.get(path) == b"new contents"
        handle = badge.request("fs.open", path=path)["handle"]
        badge.request("fs.write", b"discarded", handle=handle, offset=0)
        badge.request("fs.abort", handle=handle)
        assert badge.get(path) == b"new contents"

    def write_errors():
        handle = badge.request("fs.open", path=f"{root}/a/offset.bin")["handle"]
        expect_error("EINVAL", badge.request, "fs.write", b"x", handle=handle, offset=5)
        badge.request("fs.abort", handle=handle)
        expect_error("EBADF", badge.request, "fs.write", b"x", handle=handle, offset=0)
        expect_error("EINVAL", badge.request, "fs.write", b"x" * (CHUNK + 1), handle=1, offset=0)
        expect_error("ENOENT", badge.request, "fs.open", path=f"{root}/missing/file")
        expect_error("EEXIST", badge.request, "fs.open", path=f"{root}/a/replace.txt", exclusive=True)
        expect_error("EISDIR", badge.request, "fs.open", path=f"{root}/a")

    def paths():
        for bad in ("relative", f"{root}/../x", f"{root}/./x", f"{root}//x", f"{root}/"):
            expect_error("EINVAL", badge.request, "fs.stat", path=bad)
        expect_error("ENOENT", badge.request, "fs.stat", path=f"{root}/nope")
        expect_error("ENOENT", badge.request, "fs.stat", path="/rom/nope")

    def listing():
        badge.request("fs.mkdir", path=f"{root}/list")
        names = {f"n{index:02d}" for index in range(10)}
        for name in names:
            badge.put(f"{root}/list/{name}", name.encode())
        seen = set()
        offset = 0
        while True:
            reply = badge.request("fs.list", path=f"{root}/list", offset=offset, limit=3)
            seen.update(entry["name"] for entry in reply["entries"])
            offset += len(reply["entries"])
            if not reply["more"]:
                break
        assert seen == names, seen

    def rename_remove():
        badge.request("fs.rename", **{"from": f"{root}/a/replace.txt", "to": f"{root}/moved.txt"})
        assert badge.get(f"{root}/moved.txt") == b"new contents"
        expect_error("ENOTEMPTY", badge.request, "fs.remove", path=f"{root}/a")
        badge.request("fs.remove", path=f"{root}/a", recursive=True)
        expect_error("ENOENT", badge.request, "fs.stat", path=f"{root}/a")

    def rom_read_only():
        assert badge.request("fs.stat", path="/rom")["type"] == "dir"
        files = []

        def walk(path):
            offset = 0
            while True:
                reply = badge.request("fs.list", path=path, offset=offset)
                for entry in reply["entries"]:
                    full = f"{path}/{entry['name']}"
                    if entry["type"] == "dir":
                        walk(full)
                    else:
                        files.append((full, entry["size"]))
                offset += len(reply["entries"])
                if not reply["more"]:
                    break

        walk("/rom")
        if files:
            path, size = files[0]
            data = badge.get(path)
            assert len(data) == size
            assert badge.request("fs.hash", path=path)["sha256"] == hashlib.sha256(data).hexdigest()
        expect_error("EROFS", badge.request, "fs.open", path="/rom/x.bin")
        expect_error("EROFS", badge.request, "fs.mkdir", path="/rom/x", parents=True)
        expect_error("EROFS", badge.request, "fs.remove", path="/rom", recursive=True)
        expect_error("EROFS", badge.request, "fs.rename", **{"from": "/rom/x", "to": "/rom/y"})
        expect_error("EROFS", badge.request, "fs.rename", **{"from": f"{root}/moved.txt", "to": "/rom/y"})

    def running_app_protected():
        state = badge.request("fs.info")
        if state["state"] != "running":
            return
        expect_error("EBUSY", badge.request, "fs.mkdir", path=f"{state['app']}/fstest")
        expect_error("EBUSY", badge.request, "fs.remove", path=state["app"], recursive=True)

    def lock_unlock():
        reply = badge.request("fs.lock")
        assert reply["state"] == "held", reply
        assert badge.request("fs.info")["state"] == "held"
        badge.put(f"{root}/held.txt", b"held")
        assert badge.request("fs.unlock")["state"] in ("idle", "running")

    def watch():
        badge.request("fs.watch", enable=True)
        badge.request("fs.lock")
        badge.wait_event("fs.state")
        badge.request("fs.unlock")
        badge.wait_event("fs.state")
        badge.request("fs.watch", enable=False)

    check("mkdir", mkdir)
    check("roundtrip", roundtrip)
    check("overwrite is atomic", overwrite_is_atomic)
    check("write errors", write_errors)
    check("paths", paths)
    check("listing", listing)
    check("rename/remove", rename_remove)
    check("rom read-only", rom_read_only)
    check("running app protected", running_app_protected)
    check("lock/unlock", lock_unlock)
    check("watch", watch)

    try:
        badge.request("fs.remove", path=root, recursive=True)
    except DeviceError:
        pass

    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    return all(ok for _, ok, _ in results)


def main():
    parser = argparse.ArgumentParser(description="Badgeware cdcaux fs/app client")
    parser.add_argument("--port")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info")
    sub.add_parser("test")
    for name in ("ls", "stat", "hash", "cat"):
        sub.add_parser(name).add_argument("path")
    get = sub.add_parser("get")
    get.add_argument("remote")
    get.add_argument("local")
    put = sub.add_parser("put")
    put.add_argument("local")
    put.add_argument("remote")
    rm = sub.add_parser("rm")
    rm.add_argument("path")
    rm.add_argument("-r", action="store_true")
    mkdir = sub.add_parser("mkdir")
    mkdir.add_argument("path")
    mkdir.add_argument("-p", action="store_true")
    mv = sub.add_parser("mv")
    mv.add_argument("source")
    mv.add_argument("dest")
    sub.add_parser("lock")
    unlock = sub.add_parser("unlock")
    unlock.add_argument("--resume", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("path")
    run.add_argument("--debug", action="store_true")
    sub.add_parser("stop")
    sub.add_parser("watch")
    args = parser.parse_args()

    badge = Badge(args.port or find_port())
    try:
        if args.command == "info":
            print(json.dumps(badge.request("fs.info"), indent=2))
        elif args.command == "test":
            sys.exit(0 if run_tests(badge) else 1)
        elif args.command == "ls":
            offset = 0
            while True:
                reply = badge.request("fs.list", path=args.path, offset=offset)
                for entry in reply["entries"]:
                    suffix = "/" if entry["type"] == "dir" else ""
                    print(f"{entry['size']:>10} {entry['mtime']:>12} {entry['name']}{suffix}")
                offset += len(reply["entries"])
                if not reply["more"]:
                    break
        elif args.command == "stat":
            print(json.dumps(badge.request("fs.stat", path=args.path)))
        elif args.command == "hash":
            print(badge.request("fs.hash", path=args.path)["sha256"])
        elif args.command == "cat":
            sys.stdout.buffer.write(badge.get(args.path))
        elif args.command == "get":
            with open(args.local, "wb") as f:
                f.write(badge.get(args.remote))
        elif args.command == "put":
            with open(args.local, "rb") as f:
                print(badge.put(args.remote, f.read()))
        elif args.command == "rm":
            badge.request("fs.remove", path=args.path, recursive=args.r)
        elif args.command == "mkdir":
            badge.request("fs.mkdir", path=args.path, parents=args.p)
        elif args.command == "mv":
            badge.request("fs.rename", **{"from": args.source, "to": args.dest})
        elif args.command == "lock":
            print(badge.request("fs.lock"))
        elif args.command == "unlock":
            print(badge.request("fs.unlock", resume=args.resume))
        elif args.command == "run":
            print(badge.request("app.run", path=args.path, debug=args.debug))
        elif args.command == "stop":
            print(badge.request("app.stop"))
        elif args.command == "watch":
            badge.request("fs.watch", enable=True)
            badge.serial.timeout = None
            while True:
                print(badge.read_message())
    except DeviceError as e:
        sys.exit(str(e))
    finally:
        badge.close()


if __name__ == "__main__":
    main()

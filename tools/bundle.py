#!/usr/bin/env python3
import argparse
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "device" / "python"

LAYOUT = [
    ("firmware/apps", "apps"),
    ("firmware/fonts", "fonts"),
    ("firmware/assets", "assets"),
    ("modules/common/badgeware", "badgeware"),
    ("romfs", "rom"),
]

REWRITES = [
    (".frozen/badgeware/", "badgeware/"),
]

BOARD_FROZEN_ROOTS = ["modules/python", "modules/common"]

OUTPUTS = ["filesystem", "frozen", "filesystem.json", "fonts-manifest.json", "frozen.json", "locals.json", "bundle.json"]


def source_paths():
    return sorted({source.split("/")[0] for source, _ in LAYOUT})


def destination(relative):
    for prefix, target in LAYOUT:
        if relative == prefix or relative.startswith(prefix + "/"):
            return f"{target}{relative[len(prefix):]}"
    return None


def rewrite(text):
    for old, new in REWRITES:
        text = text.replace(old, new)
    return text


def extract(repo, ref, target):
    archive = subprocess.run(["git", "-C", str(repo), "archive", ref, *source_paths()], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")


def assemble(source, output):
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        mapped = destination(path.relative_to(source).as_posix())
        if mapped is None:
            continue
        target = output / mapped
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".py":
            target.write_text(rewrite(path.read_text()))
        else:
            shutil.copyfile(path, target)


def collect_fonts(directory, extension):
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if path.suffix.lower() == extension)


def write_manifests(bundle_dir):
    filesystem = bundle_dir / "filesystem"
    fonts = {
        "vector": collect_fonts(filesystem / "fonts", ".af"),
        "pixel": collect_fonts(filesystem / "rom" / "fonts", ".ppf"),
    }
    (bundle_dir / "fonts-manifest.json").write_text(json.dumps(fonts, indent=2) + "\n")
    files = {
        "/" + path.relative_to(filesystem).as_posix(): path.stat().st_size
        for path in sorted(filesystem.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.name != ".DS_Store"
    }
    (bundle_dir / "filesystem.json").write_text(json.dumps({"files": dict(sorted(files.items()))}, indent=4) + "\n")
    return len(files)


def frozen_sources(source):
    for base in [source / root for root in BOARD_FROZEN_ROOTS] + [RUNTIME]:
        for path in sorted(base.rglob("*.py")):
            yield path, path.relative_to(base).as_posix()


def write_locals(sources, mpy_cross, output):
    tables = {}
    with tempfile.TemporaryDirectory() as temp:
        compiled = pathlib.Path(temp) / "module.mpy"
        names = pathlib.Path(temp) / "locals.json"
        for path, frozen in sources:
            subprocess.run([mpy_cross, "-s", frozen, "-o", str(compiled), "--locals", str(names), str(path)], check=True)
            functions = json.loads(names.read_text())["functions"]
            if functions:
                tables[frozen] = functions
    (output / "locals.json").write_text(json.dumps(tables, indent=1, sort_keys=True) + "\n")
    return len(tables)


def copy_frozen(sources, output):
    files = []
    for path, frozen in sources:
        target = output / "frozen" / frozen
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        files.append(frozen)
    (output / "frozen.json").write_text(json.dumps({"files": files}, indent=1) + "\n")


def git_output(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def write_provenance(repo, ref, board, output):
    provenance = {
        "board": board,
        "commit": git_output(repo, "rev-parse", ref),
        "version": git_output(repo, "describe", "--tags", "--always", ref),
        "debug": {
            "commit": git_output(ROOT, "rev-parse", "HEAD"),
            "version": git_output(ROOT, "describe", "--tags", "--always", "--dirty"),
        },
    }
    (output / "bundle.json").write_text(json.dumps(provenance, indent=1) + "\n")
    return provenance


def clear(output):
    for name in OUTPUTS:
        path = output / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def bundle(board, repo, ref, output, mpy_cross):
    output.mkdir(parents=True, exist_ok=True)
    clear(output)
    with tempfile.TemporaryDirectory() as temp:
        source = pathlib.Path(temp)
        extract(repo, ref, source)
        assemble(source, output / "filesystem")
        files = write_manifests(output)
        sources = list(frozen_sources(source))
        copy_frozen(sources, output)
        tables = write_locals(sources, mpy_cross, output)
    provenance = write_provenance(repo, ref, board, output)
    return provenance, files, tables


def main():
    parser = argparse.ArgumentParser(description="Build a firmware debug bundle from a board's firmware repo and this badgeware-debug checkout.")
    parser.add_argument("board", help="board id, eg: badger")
    parser.add_argument("repo", type=pathlib.Path, help="firmware git repository, eg: ../badger2350")
    parser.add_argument("ref", nargs="?", default="HEAD", help="git ref to bundle")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="bundle directory to write")
    parser.add_argument("--mpy-cross", required=True, help="mpy-cross with --locals support")
    args = parser.parse_args()

    provenance, files, tables = bundle(args.board, args.repo, args.ref, args.output, args.mpy_cross)
    print(f"{args.board}: bundled {args.repo} {args.ref} ({provenance['version']}) with badgeware-debug {provenance['debug']['version']} into {args.output}, {files} files, {tables} locals tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())

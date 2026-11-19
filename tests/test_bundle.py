import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import bundle  # noqa: E402

FAKE_MPY_CROSS = """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
frozen = args[args.index("-s") + 1]
names = args[args.index("--locals") + 1]
functions = [] if frozen.endswith("empty.py") else [{"name": "<module>", "line": 1, "n_state": 2, "locals": ["value"]}]
with open(names, "w") as f:
    json.dump({"functions": functions}, f)
"""

BOARD_FILES = {
    "firmware/apps/menu/__init__.py": "import badgeware\n",
    "firmware/fonts/a.af": "font",
    "firmware/assets/x.png": "png",
    "modules/common/badgeware/__init__.py": '__import__(".frozen/badgeware/badge")\n',
    "modules/common/ide_display.py": "def hold():\n    pass\n",
    "modules/common/empty.py": "",
    "modules/python/hardware_test.py": "value = 1\n",
    "romfs/fonts/sins.ppf": "ppf",
    "board/pins.csv": "pins",
}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class LayoutTest(unittest.TestCase):
    def test_destination(self):
        paths = ["firmware/apps/menu/__init__.py", "firmware/fonts/a.af", "firmware/assets/x.png", "modules/common/badgeware/badge.py", "romfs/fonts/sins.ppf", "board/pins.csv"]
        self.assertEqual([bundle.destination(path) for path in paths], ["apps/menu/__init__.py", "fonts/a.af", "assets/x.png", "badgeware/badge.py", "rom/fonts/sins.ppf", None])

    def test_rewrite(self):
        self.assertEqual(bundle.rewrite('__import__(".frozen/badgeware/badge")'), '__import__("badgeware/badge")')


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.temp.name)
        self.repo = base / "board"
        self.output = base / "bundle"
        self.mpy_cross = base / "mpy-cross"
        self.mpy_cross.write_text(FAKE_MPY_CROSS)
        self.mpy_cross.chmod(0o755)
        for name, text in BOARD_FILES.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        git(self.repo, "init", "-q")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "Test.")

    def tearDown(self):
        self.temp.cleanup()

    def test_bundle(self):
        provenance, files, tables = bundle.bundle("tufty", self.repo, "HEAD", self.output, str(self.mpy_cross))
        filesystem = json.loads((self.output / "filesystem.json").read_text())["files"]
        self.assertEqual(sorted(filesystem), ["/apps/menu/__init__.py", "/assets/x.png", "/badgeware/__init__.py", "/fonts/a.af", "/rom/fonts/sins.ppf"])
        self.assertEqual(files, 5)
        self.assertEqual((self.output / "filesystem" / "badgeware" / "__init__.py").read_text(), '__import__("badgeware/badge")\n')
        self.assertEqual(json.loads((self.output / "fonts-manifest.json").read_text()), {"vector": ["a.af"], "pixel": ["sins.ppf"]})

        frozen = json.loads((self.output / "frozen.json").read_text())["files"]
        self.assertEqual(frozen[:4], ["hardware_test.py", "badgeware/__init__.py", "empty.py", "ide_display.py"])
        for name in ["_boot.py", "ide.py", "ide_debug.py", "ide_files.py", "ide_rom.py", "ide_session.py"]:
            self.assertIn(name, frozen)
            self.assertTrue((self.output / "frozen" / name).is_file())
        self.assertEqual((self.output / "frozen" / "badgeware" / "__init__.py").read_text(), BOARD_FILES["modules/common/badgeware/__init__.py"])

        locals_tables = json.loads((self.output / "locals.json").read_text())
        self.assertNotIn("empty.py", locals_tables)
        self.assertEqual(locals_tables["hardware_test.py"], [{"name": "<module>", "line": 1, "n_state": 2, "locals": ["value"]}])
        self.assertEqual(tables, len(frozen) - 1)

        self.assertEqual(provenance["board"], "tufty")
        self.assertEqual(json.loads((self.output / "bundle.json").read_text()), provenance)
        self.assertEqual(set(provenance["debug"]), {"commit", "version"})

    def test_bundle_replaces_previous_output(self):
        stale = self.output / "filesystem" / "stale.py"
        stale.parent.mkdir(parents=True)
        stale.write_text("")
        keep = self.output / "notes.txt"
        keep.write_text("")
        bundle.bundle("tufty", self.repo, "HEAD", self.output, str(self.mpy_cross))
        self.assertFalse(stale.exists())
        self.assertTrue(keep.exists())


if __name__ == "__main__":
    unittest.main()

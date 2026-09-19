"""Tests for isolated wheel construction and artifact validation."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import check_wheel_artifact


class WheelArtifactTest(unittest.TestCase):
    @staticmethod
    def _write_wheel(path: Path, *extra_paths: str) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for name in check_wheel_artifact.REQUIRED_WHEEL_PATHS:
                archive.writestr(name, "")
            for name in extra_paths:
                archive.writestr(name, "generated")

    def test_rejects_generated_python_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            wheel = Path(temporary_dir) / "basaltwater.whl"
            self._write_wheel(
                wheel,
                "lib/__pycache__/config.cpython-313.pyc",
            )

            with self.assertRaisesRegex(RuntimeError, "generated Python artifacts"):
                check_wheel_artifact._check_wheel_contents(wheel)

    def test_build_uses_clean_temporary_source_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir) / "checkout"
            work = Path(temporary_dir) / "work"
            package = root / "common"
            package.mkdir(parents=True)
            (package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "stale.pyc").write_bytes(b"stale")
            cache = package / "__pycache__"
            cache.mkdir()
            (cache / "module.pyc").write_bytes(b"cache")
            work.mkdir()
            observed_source: Path | None = None

            def build(command: list[str], **kwargs: object) -> None:
                nonlocal observed_source
                observed_source = Path(command[-1])
                self.assertEqual(Path(str(kwargs["cwd"])), observed_source)
                output_dir = Path(command[command.index("--outdir") + 1])
                output_dir.mkdir(parents=True)
                self._write_wheel(output_dir / "basaltwater.whl")

            with (
                patch.object(check_wheel_artifact, "ROOT", root),
                patch.object(check_wheel_artifact.subprocess, "run", side_effect=build),
            ):
                wheel = check_wheel_artifact._build_wheel(work / "dist")

            self.assertEqual(wheel.name, "basaltwater.whl")
            if observed_source is None:
                self.fail("Build command did not receive the temporary source path")
            self.assertTrue((observed_source / "common" / "module.py").is_file())
            self.assertFalse((observed_source / "build").exists())
            self.assertFalse((observed_source / "common" / "__pycache__").exists())
            self.assertNotEqual(os.path.realpath(observed_source), os.path.realpath(root))


if __name__ == "__main__":
    unittest.main()

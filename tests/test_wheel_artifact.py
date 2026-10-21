"""Tests for isolated wheel construction and artifact validation."""

from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import subprocess
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

    def test_rejects_missing_workflow_resources(self) -> None:
        resources = (
            "common/agent_skills/basaltwater-project-development/SKILL.md",
            "common/agent_skills/basaltwater-t3code/references/operations.md",
            "common/agent_skills/basaltwater-project-adoption/SKILL.md",
            "common/agent_skills/basaltwater-frontend-design/SKILL.md",
            "common/agent_skills/basaltwater-cachyos-desktop/SKILL.md",
            "common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md",
            "lib/agent_models.py",
            "lib/agent_skill_catalog.py",
            "common/agent_skills/basaltwater-subagents/SKILL.md",
            "common/agent_skills/basaltwater-subagents/references/lifecycle.md",
            "common/agent_skills/basaltwater-subagents/references/model-selection.md",
            "common/agent_skills/basaltwater-subagents/references/session-context.md",
            "common/agent_skills/basaltwater-subagents/references/t3-orchestration.md",
            "common/agent_skills/basaltwater-subagents/references/t3-scheduling.md",
            "common/agent_skills/basaltwater-wsl-workspace/SKILL.md",
            "common/agent_skills/basaltwater-subagents/assets/agent-models.json",
        )
        for missing in resources:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                wheel = Path(directory) / "basaltwater.whl"
                with zipfile.ZipFile(wheel, "w") as archive:
                    for name in check_wheel_artifact.REQUIRED_WHEEL_PATHS:
                        if name != missing:
                            archive.writestr(name, "")
                with self.assertRaisesRegex(RuntimeError, "missing required runtime files") as error:
                    check_wheel_artifact._check_wheel_contents(wheel)
                self.assertIn(missing, str(error.exception))

    def test_build_output_is_quiet_on_success_and_replayed_on_failure(self) -> None:
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                captured_out, captured_err = io.StringIO(), io.StringIO()

                def run(command, **kwargs):
                    self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
                    kwargs["stdout"].write("compiler evidence\n")
                    if code:
                        raise subprocess.CalledProcessError(code, command)

                with (
                    patch.object(check_wheel_artifact.subprocess, "run", side_effect=run),
                    redirect_stdout(captured_out), redirect_stderr(captured_err),
                ):
                    if code:
                        with self.assertRaises(subprocess.CalledProcessError) as error:
                            check_wheel_artifact._run_quietly(["mock-build"], cwd=Path(directory))
                        self.assertEqual(error.exception.returncode, code)
                    else:
                        check_wheel_artifact._run_quietly(["mock-build"], cwd=Path(directory))
                self.assertEqual(captured_out.getvalue(), "")
                self.assertEqual(captured_err.getvalue(), "compiler evidence\n" if code else "")

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

#!/usr/bin/env python3
"""Build or inspect a wheel and smoke-test it outside the source tree."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_WHEEL_PATHS = (
    "basaltwater.py",
    "remote_setup.py",
    "lib/config.py",
    "plugins/common.py",
    "common/agent_steps.py",
    "common/cachyos_steps.py",
    "common/agent_skills/basaltwater-cachyos-workstation/SKILL.md",
    "common/agent_skills/basaltwater-cachyos-workspace/SKILL.md",
    "common/agent_skills/basaltwater-cachyos-t3code/SKILL.md",
    "deploy/deploy_steps.py",
    "desktop/config/xrdp.ini.template",
    "security/security_steps.py",
    "smb/samba_steps.py",
    "sync/service_tools/scrub_par2.py",
    "web/config/nginx.conf.template",
    "web/service_tools/webhook_manager.py",
)
SOURCE_COPY_IGNORE = shutil.ignore_patterns(
    ".cache",
    ".codex",
    ".env",
    ".env.*",
    ".git",
    ".basaltwater",
    ".nox",
    ".tox",
    ".venv",
    "__pycache__",
    "*.egg-info",
    "*.py[cod]",
    "build",
    "dist",
)


def _build_wheel(output_dir: Path) -> Path:
    source_dir = output_dir.parent / "source"
    shutil.copytree(ROOT, source_dir, ignore=SOURCE_COPY_IGNORE, symlinks=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(output_dir),
            str(source_dir),
        ],
        check=True,
        cwd=source_dir,
    )
    wheels = list(output_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one wheel in {output_dir}, found {len(wheels)}")
    return wheels[0]


def _check_wheel_contents(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    missing = [path for path in REQUIRED_WHEEL_PATHS if path not in names]
    if missing:
        raise RuntimeError(
            "Wheel is missing required runtime files: " + ", ".join(missing)
        )
    if "infra_tools.py" in names or any("common/agent_skills/infra-tools-" in name for name in names):
        raise RuntimeError("Wheel includes retired entry points or skill IDs")
    generated = sorted(
        name
        for name in names
        if "__pycache__" in PurePosixPath(name).parts
        or name.endswith((".pyc", ".pyo"))
    )
    if generated:
        raise RuntimeError(
            "Wheel contains generated Python artifacts: " + ", ".join(generated[:5])
        )


def _venv_command(venv_dir: Path, command: str) -> Path:
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    return venv_dir / bin_dir / command


def _smoke_installed_wheel(
    wheel: Path, work_dir: Path,
) -> None:
    venv_dir = work_dir / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    python = _venv_command(venv_dir, "python")
    pip = _venv_command(venv_dir, "pip")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)



    subprocess.run(
        [str(pip), "install", "--no-deps", str(wheel)],
        check=True,
        cwd=work_dir,
        env=environment,
    )
    for retired in ("infra-tools", "infra_tools", "basaltwater"):
        if _venv_command(venv_dir, retired).exists():
            raise RuntimeError(f"Wheel installed an unexpected launcher: {retired}")
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "import common, deploy, desktop, game, basaltwater, lib, plugins, "
                "remote_setup, security, smb, sync, web; "
                "import sync.service_tools.scrub_par2; "
                "import web.service_tools.webhook_manager"
            ),
        ],
        check=True,
        cwd=work_dir,
        env=environment,
    )
    subprocess.run(
        [str(_venv_command(venv_dir, "basaltw")), "--help"],
        check=True,
        cwd=work_dir,
        env=environment,
        stdout=subprocess.DEVNULL,
    )
    installed_version = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "from importlib.metadata import version; print(version('basaltwater'))",
        ],
        check=True,
        cwd=work_dir,
        env=environment,
        capture_output=True,
        text=True,
    ).stdout.strip()
    version_result = subprocess.run(
        [str(_venv_command(venv_dir, "basaltw")), "--version"],
        check=True,
        cwd=work_dir,
        env=environment,
        capture_output=True,
        text=True,
    )
    if version_result.stdout.strip() != f"basaltw {installed_version}":
        raise RuntimeError("Installed basaltw launcher returned the wrong version")
    subprocess.run(
        [str(_venv_command(venv_dir, "webhook_manager")), "--help"],
        check=True,
        cwd=work_dir,
        env=environment,
        stdout=subprocess.DEVNULL,
    )



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "wheel",
        nargs="?",
        type=Path,
        help="Existing wheel to inspect; omit to build one in a temporary directory",
    )

    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="basaltw-wheel-") as temp_dir:
        work_dir = Path(temp_dir)
        wheel = args.wheel.resolve() if args.wheel else _build_wheel(work_dir / "dist")
        if not wheel.is_file():
            parser.error(f"wheel does not exist: {wheel}")
        _check_wheel_contents(wheel)
        _smoke_installed_wheel(wheel, work_dir)

    print(f"Wheel artifact check passed: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

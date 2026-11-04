#!/usr/bin/env python3
"""Build or inspect a wheel and smoke-test it outside the source tree."""

from __future__ import annotations

import argparse
import json
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
    "lib/agent_skill_bundles.py",
    "lib/agent_skill_catalog.py",
    "lib/agent_models.py",
    "lib/t3code_native_probe.cjs",
    "plugins/common.py",
    "common/agent_steps.py",
    "lib/admin_actions.py",
    "common/service_tools/admin_job.py",
    "common/service_tools/web_panel_admin.py",
    "common/service_tools/web_panel_agent_tools.py",
    "common/agent_skills/basaltwater-project-development/SKILL.md",
    "common/agent_skills/basaltwater-project-development/references/t3-workflows.md",
    "common/agent_skills/basaltwater-project-adoption/SKILL.md",
    "common/agent_skills/basaltwater-frontend-design/SKILL.md",
    "common/agent_skills/basaltwater-agent-operations/references/unattended-tasks.md",
    "common/agent_skills/basaltwater-subagents/SKILL.md",
    "common/agent_skills/basaltwater-subagents/references/lifecycle.md",
    "common/agent_skills/basaltwater-subagents/references/model-selection.md",
    "common/agent_skills/basaltwater-subagents/references/session-context.md",
    "common/agent_skills/basaltwater-subagents/references/t3-orchestration.md",
    "common/agent_skills/basaltwater-subagents/references/t3-scheduling.md",
    "common/agent_skills/basaltwater-wsl-workspace/SKILL.md",
    "common/wsl_steps.py",
    "plugins/wsl.py",
    "common/agent_skills/basaltwater-subagents/assets/agent-models.json",
    "common/agent_skills/basaltwater-t3code/references/updates.md",
    "common/agent_skills/basaltwater-t3code/references/operations.md",
    "common/agent_skills/basaltwater-t3code/references/thread-migration.md",
    "common/cachyos_steps.py",
    "common/agent_skills/basaltwater-cachyos-desktop/SKILL.md",
    "common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md",
    "common/agent_skills/basaltwater-cachyos-workstation/SKILL.md",
    "common/agent_skills/basaltwater-cachyos-workspace/SKILL.md",
    "common/agent_skills/basaltwater-cachyos-t3code/SKILL.md",
    "deploy/deploy_steps.py",
    "desktop/client.py",
    "desktop/coordinates.py",
    "desktop/evidence.py",
    "desktop/observations.py",
    "desktop/preflight.py",
    "desktop/qa_cli.py",
    "desktop/receipts.py",
    "desktop/run_checkpoint.py",
    "desktop/learning_records.py",
    "desktop/record_bundles.py",
    "desktop/config/xrdp.ini.template",
    "security/security_steps.py",
    "smb/samba_steps.py",
    "sync/service_tools/scrub_par2.py",
    "sync/service_tools/scrub_runner.py",
    "web/config/nginx.conf.template",
    "common/service_tools/build_webhooks.py",
    "lib/build_webhook_service.py",
    "lib/webhook_builds.py",
    "lib/build_coordinator.py",
    "lib/build_commands.py",
    "lib/nginx_validation.py",
    "web/webhook_build_steps.py",
)
SOURCE_COPY_IGNORE = shutil.ignore_patterns(
    ".cache",
    ".codex",
    ".coverage",
    ".coverage.*",
    ".coverage-*",
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


def _run_quietly(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    """Keep successful build/install chatter out of check output; replay failures."""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as output:
        try:
            subprocess.run(command, check=True, cwd=cwd, env=env,
                           stdout=output, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            output.seek(0)
            shutil.copyfileobj(output, sys.stderr)
            raise


def _build_wheel(output_dir: Path) -> Path:
    source_dir = output_dir.parent / "source"
    shutil.copytree(ROOT, source_dir, ignore=SOURCE_COPY_IGNORE, symlinks=True)
    _run_quietly(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(output_dir),
            str(source_dir),
        ],
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


def _smoke_desktop_cli(launcher: Path, work_dir: Path, environment: dict[str, str]) -> None:
    """Exercise installed offline contracts without a desktop connection or input."""
    folder = work_dir / "desktop-smoke"
    folder.mkdir(mode=0o700)

    def request(*arguments: str, expected: int = 0) -> dict:
        result = subprocess.run([str(launcher), *arguments], cwd=work_dir, env=environment,
                                capture_output=True, text=True, timeout=20)
        if result.returncode != expected:
            raise RuntimeError(f"Installed desktop CLI failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise RuntimeError("Installed desktop CLI did not return a JSON object")
        return data

    source = folder / "records-input.json"
    source.write_bytes((ROOT / "docs/examples/learning-records/accepted.json").read_bytes())
    records = request("desktop", "evidence", "records", "create", str(source),
                      "--output", str(folder / "records"), "--json")
    checked = request("desktop", "evidence", "records", "validate", records["artifact"],
                      "--current-state", "state-2", "--at", "2026-01-01T00:00:20+00:00", "--json")
    if checked["advice"][0]["validation"] != "stale" or checked["input_authority"] is not False:
        raise RuntimeError("Installed record validator lost freshness/authority boundaries")
    request("desktop", "evidence", "records", "create", str(source),
            "--output", str(folder / "records"), "--json", expected=1)

    instructions = folder / "instructions.md"
    text = "Bounded instructions α\n" * 300
    instructions.write_text(text, encoding="utf-8")
    first = request("desktop", "instructions", str(instructions), "--json")
    continuation = ["basaltw", "desktop", "instructions", str(instructions), "--start", "4000", "--sha256", first["sha256"]]
    if first["range"] != [0, 4000] or first["next_argv"] != continuation:
        raise RuntimeError("Installed instruction continuation did not retain its bounded read-only command")
    second = request(*continuation[1:], "--json")
    if first["text"] + second["text"] != text or first["model_comprehension"] != "unknown":
        raise RuntimeError("Installed instruction paging did not preserve bounded Unicode text/limits")
    coordinates = request("desktop", "--native", "map-point", "--geometry", "2400", "1600",
                          "--view-geometry", "400", "300", "--crop", "100", "200", "800", "600",
                          "--x", "200", "--y", "150", "--to-x", "399", "--to-y", "299", "--json")
    if coordinates["input_sent"] is not False or coordinates["input_coordinates"] != {
            "x": 500, "y": 500, "to_x": 898, "to_y": 798}:
        raise RuntimeError("Installed offline coordinate mapping failed")
    started = request("desktop", "evidence", "run", "start", "--run-id", "wheel-smoke", "--match-id", "same-match",
                      "--phase", "preparation", "--review-seconds", "30", "--hard-seconds", "600",
                      "--output", str(folder / "run"), "--json")
    artifact = started["artifact"]
    for event, arguments in (("suspend", ["--note", "Explicit fixture owner wait"]),
                             ("context", ["--context", "continuation"]), ("resume", []),
                             ("policy", ["--hard-seconds", "1200", "--note", "Fixture owner extension"]),
                             ("policy", ["--clear-hard-limit", "--note", "Fixture owner authorized open-ended same-match completion"]),
                             ("policy", ["--review-seconds", "60", "--note", "Fixture owner changed review cadence only"]),
                             ("policy", ["--hard-seconds", "1500", "--note", "Fixture owner restored a finite cap"]),
                             ("checkpoint", ["--phase", "closeout", "--artifact", str(instructions)]), ("finish", [])):
        updated = request("desktop", "evidence", "run", event, artifact, *arguments, "--json")
        if updated["started"] != started["started"] or updated["match_id"] != "same-match":
            raise RuntimeError("Installed run continuation reset original identity/clocks")
        if ("--clear-hard-limit" in arguments or "--review-seconds" in arguments) and updated["hard_limit_seconds"] is not None:
            raise RuntimeError("Installed run ledger did not retain the explicitly cleared hard limit")
    shown = request("desktop", "evidence", "run", "show", artifact, "--json")
    if not shown["finished"] or shown["events"] != 10 or shown["hard_limit_seconds"] != 1500:
        raise RuntimeError("Installed run ledger lost checkpoint/policy history")
    before = Path(artifact).read_bytes()
    refused = request("desktop", "evidence", "run", "checkpoint", artifact, "--json", expected=1)
    if "finished" not in refused.get("error", "") or Path(artifact).read_bytes() != before:
        raise RuntimeError("Installed run ledger did not preserve its finished record on refusal")


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
    _run_quietly(
        [str(pip), "install", "--no-deps", str(wheel)],
        cwd=work_dir,
        env=environment,
    )
    for retired in ("infra-tools", "infra_tools", "basaltwater", "webhook_manager"):
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
                "import sync.service_tools.storage_ops; "
                "import lib.build_webhook_service, lib.webhook_builds, lib.build_coordinator"
                "; import lib.build_commands, lib.nginx_validation, web.webhook_build_steps"
                "; import common.service_tools.admin_job"
                "; import common.service_tools.web_panel_admin"
                "; import common.service_tools.web_panel_agent_tools"
                "; from lib.agent_models import _TEMPLATE, validate_model_policy"
                "; from lib.atomic_io import read_json_file"
                "; validate_model_policy(read_json_file(str(_TEMPLATE)))"
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
    if sys.platform == "linux":
        _smoke_desktop_cli(_venv_command(venv_dir, "basaltw"), work_dir, environment)
    entrypoint = subprocess.run(
        [str(python), "-I", "-c",
         "import runpy; runpy.run_module('common.service_tools.build_webhooks', run_name='__main__')"],
        cwd=work_dir, env=environment, capture_output=True, text=True,
    )
    if entrypoint.returncode != 1 or "Use the managed webhook services" not in entrypoint.stderr:
        raise RuntimeError("Managed webhook entrypoint did not enforce its service arguments")



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

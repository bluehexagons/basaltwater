"""Declared vendor-channel policy and provenance for downloaded installers."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import pwd
import shlex
import signal
import stat
import sys
import tempfile
import time
import urllib.parse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.atomic_io import write_json_atomic
from lib.remote_utils import run
from lib.validation import validate_filesystem_path


# Selecting these tools accepts their vendor-managed channel, including payloads
# fetched by the installer. Observed hashes are provenance, not pinned integrity.
POLICIES = {
    "codex": ("https://chatgpt.com/codex/install.sh", "sh", "vendor-rolling"),
    "claude": ("https://claude.ai/install.sh", "bash", "vendor-rolling"),
    "opencode": ("https://opencode.ai/install", "bash", "vendor-rolling"),
    "uv": ("https://astral.sh/uv/install.sh", "sh", "vendor-rolling"),
    "nvm": ("https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh", "bash", "vendor-tagged-installer-rolling-node-lts"),
}
MAX_INSTALLER_BYTES = 4 * 1024 * 1024


def _policy(tool: str) -> tuple[str, str, str]:
    try:
        return POLICIES[tool]
    except KeyError as exc:
        raise ValueError("Installer has no accepted channel policy") from exc


def installer_command(tool: str) -> str:
    _policy(tool)
    return shlex.join(["/usr/bin/python3", os.path.abspath(__file__), tool, "--accept-vendor-channel"])


def _state_directory() -> Path:
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    root = Path("/var/lib/infra_tools/installer-provenance") if os.geteuid() == 0 else home / ".local/state/infra-tools/installers"
    validate_filesystem_path(str(root), must_exist=False)
    for path in reversed((root, *root.parents)):
        if path.is_symlink():
            raise ValueError(f"Unsafe installer state directory: {path}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError("Installer provenance directory must be private and owned by the executing user")
    return root


def record_installer(tool: str, path: str, *, effective_url: str | None = None) -> tuple[str, dict]:
    """Persist the latest attempt per tool before executing any downloaded bytes."""
    source, _, policy = _policy(tool)
    resolved = effective_url or source
    parsed = urllib.parse.urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Installer download must remain on HTTPS without URL credentials")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Installer must be a regular file")
        content = stream.read(MAX_INSTALLER_BYTES + 1)
    if not content or len(content) > MAX_INSTALLER_BYTES:
        raise ValueError("Installer is empty or exceeds 4 MiB")
    record = dict(schema_version=1, tool=tool, policy=policy, source=source,
                  effective_url=resolved, observed_sha256=hashlib.sha256(content).hexdigest(),
                  bytes=len(content), uid=os.geteuid(), recorded_at=time.time(), status="downloaded")
    state_path = str(_state_directory() / f"{tool}.json")
    write_json_atomic(state_path, record)
    print(f"  {tool}: accepted {policy}; SHA-256 {record['observed_sha256']}; provenance {state_path}", file=sys.stderr)
    return state_path, record


def download_installer(tool: str, directory: str) -> tuple[str, str, dict]:
    source = _policy(tool)[0]
    validate_filesystem_path(directory, must_exist=True)
    fd, path = tempfile.mkstemp(prefix=f".{tool}-installer-", suffix=".sh", dir=directory)
    os.close(fd)
    try:
        curl = [
            "curl", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https",
            "--connect-timeout", "15", "--max-time", "120", "--max-filesize", str(MAX_INSTALLER_BYTES),
            "--output", path, "--write-out", "%{url_effective}", source,
        ]
        downloaded = run(
            ["/usr/bin/prlimit", f"--fsize={MAX_INSTALLER_BYTES}", "--", *curl],
            capture_output=True,
            timeout=130,
        )
        state_path, record = record_installer(tool, path, effective_url=downloaded.stdout.strip())
        return path, state_path, record
    except BaseException:
        os.unlink(path)
        raise


def install(
    tool: str,
    *,
    accept_vendor_channel: bool = False,
    non_interactive: bool = False,
) -> int:
    if not accept_vendor_channel or tool not in POLICIES:
        raise ValueError("Explicit vendor-channel acceptance is required")
    source, shell, policy = _policy(tool)
    print(f"  Installing {tool} under {policy} policy from {source}")
    with tempfile.TemporaryDirectory(prefix="installer-", dir=_state_directory()) as directory:
        path, state_path, record = download_installer(tool, directory)
        environment = dict(os.environ)
        environment["CODEX_NON_INTERACTIVE"] = "1"
        if non_interactive:
            environment.update({
                "CI": "1",
                "NONINTERACTIVE": "1",
                "NON_INTERACTIVE": "1",
                "npm_config_yes": "true",
                "NPM_CONFIG_YES": "true",
            })
        try:
            run_kwargs = {
                "check": False,
                "env": environment,
                "timeout": 3600,
            }
            if non_interactive:
                # Keep an installer from blocking the setup terminal on a read.
                run_kwargs["input_data"] = ""
            result = run([f"/bin/{shell}", path], **run_kwargs)
            record.update(status="succeeded" if result.returncode == 0 else "failed", returncode=result.returncode)
            return result.returncode
        except BaseException:
            record.update(status="interrupted")
            raise
        finally:
            write_json_atomic(state_path, record)


def main() -> int:
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Installer interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=POLICIES)
    parser.add_argument("--accept-vendor-channel", action="store_true")
    args = parser.parse_args()
    return install(args.tool, accept_vendor_channel=args.accept_vendor_channel)


if __name__ == "__main__":
    raise SystemExit(main())

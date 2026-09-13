"""Utility functions for remote setup."""

from __future__ import annotations

import math
import os
import pwd
import re
import secrets
import shlex
import signal
import string
import subprocess
import sys
from typing import Callable, IO, Optional, Sequence

from lib.validation import validate_package_name


_dry_run = False


VERBOSE_ENV_VAR = "INFRA_TOOLS_VERBOSE"


DEFAULT_COMMAND_TIMEOUT_SECONDS = 60 * 60
_TIMEOUT_TERMINATION_GRACE_SECONDS = 5.0


_SECRET_ASSIGNMENT_PREFIX_RE = re.compile(
    r"(?i)(?P<key>\b[A-Za-z_][A-Za-z0-9_-]*)(?P<separator>\s*=\s*)"
)
_SECRET_OPTION_PREFIX_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(--?(?:password|passwd|secret|token|api[-_]?key|private[-_]?key|"
    r"credentials?)(?:=|\s+))"
)
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "api-key",
    "private_key",
    "private-key",
    "credential",
)


def _shell_word_end(value: str, start: int) -> int:
    """Return the end of one shell-style word without evaluating it."""

    index = start
    quote: Optional[str] = None
    while index < len(value):
        character = value[index]
        if quote is not None:
            if character == quote:
                quote = None
            elif character == "\\" and quote == '"' and index + 1 < len(value):
                index += 1
        elif character in {"'", '"'}:
            quote = character
        elif character == "\\" and index + 1 < len(value):
            index += 1
        elif character.isspace() or character in ";&|":
            break
        index += 1
    return index


def _secret_value_spans(value: str) -> list[tuple[int, int]]:
    """Locate shell-style values belonging to recognized secret keys/options."""

    starts: list[int] = []
    for match in _SECRET_ASSIGNMENT_PREFIX_RE.finditer(value):
        key = match.group("key").lower()
        if any(marker in key for marker in _SECRET_KEY_MARKERS):
            starts.append(match.end())
    starts.extend(match.end() for match in _SECRET_OPTION_PREFIX_RE.finditer(value))

    spans: list[tuple[int, int]] = []
    for start in sorted(set(starts)):
        end = _shell_word_end(value, start)
        if end > start:
            spans.append((start, end))
    return spans


def _redact_command(value: str) -> str:
    """Redact complete shell-style secret values without evaluating the text."""

    spans = _secret_value_spans(value)
    if not spans:
        return value

    parts: list[str] = []
    previous_end = 0
    for start, end in spans:
        if start < previous_end:
            continue
        parts.append(value[previous_end:start])
        parts.append("<redacted>")
        previous_end = end
    parts.append(value[previous_end:])
    return "".join(parts)


Command = str | Sequence[str]


def _command_text(cmd: Command) -> str:
    """Render a command for diagnostics without invoking a shell."""

    if isinstance(cmd, str):
        return cmd
    return shlex.join(list(cmd))


class CommandExecutionError(RuntimeError):
    """Raised when a required command exits unsuccessfully."""

    def __init__(
        self,
        command: str,
        returncode: int,
        stderr: Optional[str] = None,
        *,
        result: Optional[subprocess.CompletedProcess[str]] = None,
    ) -> None:
        self.command = _redact_command(command)
        self.returncode = returncode
        self.stderr = _redact_command(stderr) if stderr else stderr
        self.result = result
        message = f"Command failed with exit code {returncode}: {self.command}"
        if self.stderr:
            message += f"\n{self.stderr[:500]}"
        super().__init__(message)


class CommandTimeoutError(TimeoutError):
    """Raised when a command exceeds its caller-visible execution bound."""

    def __init__(
        self,
        command: str,
        timeout: float,
        stderr: Optional[str] = None,
    ) -> None:
        self.command = _redact_command(command)
        self.timeout = timeout
        self.stderr = _redact_command(stderr) if stderr else stderr
        message = f"Command timed out after {timeout:g} seconds: {self.command}"
        if self.stderr:
            message += f"\n{self.stderr[:500]}"
        super().__init__(message)


def set_dry_run(enabled: bool) -> None:
    """Set dry-run mode globally."""
    global _dry_run
    _dry_run = enabled


def is_dry_run() -> bool:
    """Check if dry-run mode is enabled."""
    return _dry_run


def _verbose_commands_enabled() -> bool:
    """Return whether individual shell commands should be echoed."""

    return os.environ.get(VERBOSE_ENV_VAR, "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _validate_timeout(timeout: Optional[float]) -> Optional[float]:
    """Validate a command timeout while retaining an explicit unbounded option."""

    if timeout is None:
        return None
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("Command timeout must be a positive number or None")
    return float(timeout)


def _terminate_timed_out_process(
    process: subprocess.Popen[str],
) -> None:
    """Terminate a timed-out command's group, including surviving children."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=_TIMEOUT_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(
    cmd: Command,
    check: bool = True,
    cwd: Optional[str] = None,
    capture_output: bool = False,
    text: bool = True,
    display_cmd: Optional[str] = None,
    input_data: Optional[str] = None,
    timeout: Optional[float] = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    *,
    stdout: IO[str] | int | None = None,
    stderr: IO[str] | int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if capture_output and (stdout is not None or stderr is not None):
        raise ValueError("stdout/stderr and capture_output cannot be combined")
    validated_timeout = _validate_timeout(timeout)
    diagnostic_output = sys.stderr if stdout is not None else sys.stdout
    log_cmd = _redact_command(
        display_cmd if display_cmd is not None else _command_text(cmd)
    )
    if _verbose_commands_enabled() or is_dry_run():
        print(f"  Running: {log_cmd[:80]}..." if len(log_cmd) > 80 else f"  Running: {log_cmd}", file=diagnostic_output)
        diagnostic_output.flush()
    
    if is_dry_run():
        print("  [DRY-RUN] Command not executed", file=diagnostic_output)
        dry_run_args = [cmd] if isinstance(cmd, str) else list(cmd)
        return subprocess.CompletedProcess(
            args=dry_run_args,
            returncode=0,
            stdout="",
            stderr="",
        )

    requires_shell = isinstance(cmd, str) and _requires_shell(cmd)
    command = (
        ["/bin/bash", "-lc", cmd]
        if requires_shell
        else list(cmd) if not isinstance(cmd, str) else shlex.split(cmd)
    )
    if not command:
        raise ValueError("Command must not be empty")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_data is not None else None,
        stdout=subprocess.PIPE if capture_output else stdout,
        stderr=subprocess.PIPE if capture_output else stderr,
        text=text,
        cwd=cwd,
        start_new_session=True,
        **({"env": env} if env is not None else {}),
    )
    try:
        stdout, stderr = process.communicate(input=input_data, timeout=validated_timeout)
    except subprocess.TimeoutExpired as exc:
        assert validated_timeout is not None
        _terminate_timed_out_process(process)
        try:
            stdout, stderr = process.communicate(timeout=_TIMEOUT_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired as cleanup_exc:
            # A child can escape the group with setsid() and retain a pipe.
            # Never turn timeout handling into another unbounded wait.
            stderr = cleanup_exc.stderr
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            process.poll()
        diagnostic = stderr or exc.stderr
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode("utf-8", errors="replace")
        raise CommandTimeoutError(
            log_cmd,
            validated_timeout,
            diagnostic if isinstance(diagnostic, str) else None,
        ) from exc
    except KeyboardInterrupt:
        # Isolated groups do not receive the caller's terminal SIGINT.
        # Do not leave setup or deployment children running after cancellation.
        _terminate_timed_out_process(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        process.poll()
        raise

    result = subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if check and result.returncode != 0:
        if getattr(result, 'stderr', None):
            warning = _redact_command(result.stderr) if isinstance(result.stderr, str) else result.stderr
            print(f"    Warning: {warning[:200]}", file=diagnostic_output)
            diagnostic_output.flush()
        raise CommandExecutionError(
            log_cmd,
            result.returncode,
            result.stderr if isinstance(result.stderr, str) else None,
            result=result,
        )
    return result


def _requires_shell(cmd: str) -> bool:
    """Return True when a command string depends on shell parsing."""

    stripped = cmd.strip()
    if not stripped:
        return False

    if stripped.startswith(("export ", ".", "source ")):
        return True

    first_token = stripped.split(None, 1)[0]
    if "=" in first_token and not first_token.startswith(("/", "./")):
        return True

    shell_metacharacters = ("|", "&&", "||", ";", ">", "<", "$", "`", "$(", "\n")
    return any(token in stripped for token in shell_metacharacters)


SUPPORTED_OS_IDS = {"debian", "ubuntu", "linuxmint"}


def read_os_release(path: str = "/etc/os-release") -> dict[str, str]:
    """Read the small KEY=VALUE format used by Linux ``os-release`` files."""
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as file_obj:
        for line in file_obj:
            key, separator, value = line.partition("=")
            if not separator or not key or key.strip() != key:
                continue
            values[key] = value.strip().strip('"').strip("'")
    return values


def get_os_id(path: str = "/etc/os-release") -> str:
    """Return a normalized distribution ID, or an empty string if unknown."""

    try:
        release = read_os_release(path)
    except OSError:
        return ""
    return release.get("ID", "").lower()


def confirm_unsupported_environment(
    operation: str,
    *,
    input_fn: Optional[Callable[[str], str]] = None,
) -> bool:
    """Ask before a local operation runs on an unsupported distribution."""

    distro_id = get_os_id()
    if distro_id in SUPPORTED_OS_IDS:
        return True

    display_id = distro_id or "unknown"
    print(
        f"Warning: {operation} is running on unsupported host distribution "
        f"'{display_id}'."
    )
    print(
        "Debian is officially supported; Ubuntu and Linux Mint are "
        "best-effort. Other distributions may lack compatible package tools."
    )
    prompt = f"Continue with {operation} anyway? [y/N] "
    try:
        response = (input_fn or input)(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return False

    if response.strip().lower() in {"y", "yes"}:
        return True

    print("Cancelled.")
    return False


def detect_os() -> str:
    try:
        release = read_os_release()
    except FileNotFoundError:
        print("Error: Cannot detect OS - /etc/os-release not found")
        sys.exit(1)

    distro_id = release.get("ID", "").lower()
    if distro_id not in SUPPORTED_OS_IDS:
        print(
            "Error: Unsupported OS (Debian is officially supported; "
            "Ubuntu and Linux Mint are best-effort)"
        )
        sys.exit(1)

    return {
        "debian": "Debian",
        "ubuntu": "Ubuntu (Debian-compatible)",
        "linuxmint": "Linux Mint (Ubuntu/Debian-compatible)",
    }[distro_id]


class ProbeError(RuntimeError):
    """A required system probe could not determine the requested state."""


def _probe(command: list[str]) -> subprocess.CompletedProcess[str]:
    # Probes still execute in dry-run mode, but never authorize mutation when
    # the operating system cannot answer. These fixed system tools do not run
    # repository-authored scripts.
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(
            f"System state is unknown: {_command_text(command)} could not complete "
            "within the probe contract. Repair the probe before retrying setup."
        ) from exc
    expected_codes = {"dpkg-query": {0, 1}, "systemctl": {0, 3, 4}, "id": {0, 1}}
    if result.returncode not in expected_codes[command[0]]:
        raise ProbeError(
            f"System state is unknown: {_command_text(command)} exited "
            f"with unexpected status {result.returncode}. Repair the probe before retrying setup."
        )
    return result


def is_package_installed(package: str) -> bool:
    safe_package = validate_package_name(package)
    result = _probe(
        ["dpkg-query", "-W", "-f=${Status}", safe_package],
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def install_with_verify(
    name: str,
    install_cmd: Command,
    verify_fn: Callable[[], bool],
) -> bool:
    """Run install command and verify using provided verification function.
    
    Args:
        name: Display name for messages
        install_cmd: Command to run
        verify_fn: Callable that returns True if installed

    Returns:
        True if verification passed
    """
    if verify_fn():
        print(f"  ✓ {name} already installed")
        return True

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    print(f"  Installing {name}...")
    run(install_cmd, check=False)
    
    if verify_fn():
        print(f"  ✓ {name} installed")
        return True
    
    print(f"  ⚠ Failed to install {name}")
    return False


def install_package(name: str, package: str, install_cmd: Command) -> bool:
    """Install an apt package and verify success.
    
    Args:
        name: Display name for messages
        package: Package name to check (for verification)
        install_cmd: Command to run for installation

    Returns:
        True if installed, False otherwise
    """
    return install_with_verify(
        name,
        install_cmd,
        lambda pkg=package: is_package_installed(pkg),
    )


def is_service_active(service: str) -> bool:
    result = _probe(
        ["systemctl", "is-active", service],
    )
    return result.returncode == 0


def user_exists(username: str) -> bool:
    result = _probe(
        ["id", username],
    )
    return result.returncode == 0


def get_user_home(username: str) -> str:
    """Return the home directory recorded for an existing Unix account."""
    try:
        return pwd.getpwnam(username).pw_dir
    except KeyError as exc:
        raise RuntimeError(f"Target user does not exist: {username}") from exc


def file_contains(filepath: str, content: str) -> bool:
    try:
        with open(filepath, 'r') as f:
            return content in f.read()
    except (FileNotFoundError, PermissionError):
        return False

"""Target-side setup for origin-scoped HTTPS Git credentials."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import stat
from subprocess import CompletedProcess
import sys
from urllib.parse import quote, urlsplit, urlunsplit

from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.credentials import get_runtime_credential
from lib.git_credentials import (
    decode_git_ca_pem,
    git_ca_filename,
    normalize_git_https_origin,
)
from lib.remote_utils import is_dry_run, run
from lib.setup_directories import directory_fd
from lib.validators import validate_username
from web.service_storage import reject_nested_mounts


_MANAGED_RELATIVE_DIR = os.path.join(".config", "basaltwater", "git")
_MANAGED_INCLUDE_NAME = "config"
_MANAGED_CREDENTIAL_NAME = "credentials"


def _git_config_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ensure_user_owned_directory(
    path: str,
    account: pwd.struct_passwd,
    *,
    mode: int,
) -> None:
    """Create one private directory chain as the target account."""
    home = os.path.abspath(account.pw_dir)
    target = os.path.abspath(path)
    if os.path.commonpath((home, target)) != home:
        raise RuntimeError(f"Managed Git credential path is outside user home: {path}")
    with directory_fd(home) as descriptor:
        if os.fstat(descriptor).st_uid != account.pw_uid:
            raise RuntimeError(f"Refusing managed Git home owned by another user: {home}")

    current = home
    relative = os.path.relpath(target, home)
    for component in relative.split(os.path.sep):
        current = os.path.join(current, component)
        try:
            with directory_fd(current, create=True, mode=mode) as descriptor:
                if os.fstat(descriptor).st_uid != account.pw_uid:
                    raise RuntimeError(
                        f"Refusing managed Git credential directory owned by another user: {current}"
                    )
                if current == target:
                    os.fchmod(descriptor, mode)
        except ValueError as exc:
            raise RuntimeError(f"Refusing unsafe managed Git credential directory: {current}") from exc


def _write_user_file(
    path: str,
    content: str,
    account: pwd.struct_passwd,
    *,
    mode: int,
) -> None:
    with directory_fd(os.path.dirname(path)) as descriptor:
        target = Path(f"/proc/self/fd/{descriptor}") / os.path.basename(path)
        try:
            info = target.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError(f"Refusing unsafe managed Git credential file: {path}")
            if info.st_uid != account.pw_uid:
                raise RuntimeError(f"Refusing managed Git credential file owned by another user: {path}")
        write_text_atomic(str(target), content, mode=mode)


def _run_git(account: pwd.struct_passwd, *arguments: str) -> CompletedProcess[str]:
    return run(
        ["/usr/bin/git", "config", *arguments],
        cwd=account.pw_dir,
        env={"HOME": account.pw_dir, "USER": account.pw_name, "PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        # Retain the worker's process group so setup can cancel both processes.
        preserve_terminal=True,
    )


def _remove_managed_include(
    account: pwd.struct_passwd,
    include_path: str,
) -> None:
    result = _run_git(account, "--global", "--fixed-value", "--unset-all", "include.path", include_path)
    if result.returncode not in {0, 1, 5}:
        detail = (result.stderr or result.stdout or "git config failed").strip()
        raise RuntimeError(f"Could not reconcile managed Git config include: {detail}")


def _install_managed_include(
    account: pwd.struct_passwd,
    include_path: str,
) -> None:
    _remove_managed_include(account, include_path)
    result = _run_git(account, "--global", "--add", "include.path", include_path)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git config failed").strip()
        raise RuntimeError(f"Could not install managed Git config include: {detail}")


def _credential_store_line(origin: str, username: str, password: str) -> str:
    parsed = urlsplit(origin)
    escaped_username = quote(username, safe="")
    escaped_password = quote(password, safe="")
    authenticated_netloc = (
        f"{escaped_username}:{escaped_password}@{parsed.netloc}"
    )
    return urlunsplit((parsed.scheme, authenticated_netloc, "", "", ""))


@contextmanager
def _managed_removal_directory(
    account: pwd.struct_passwd,
) -> Iterator[tuple[int, int] | None]:
    """Validate ownership and retain nofollow anchors before changing Git config."""
    with ExitStack() as stack:
        try:
            descriptor = stack.enter_context(directory_fd(account.pw_dir))
            if os.fstat(descriptor).st_uid != account.pw_uid:
                raise RuntimeError("Refusing managed Git home owned by another user")
            for component in _MANAGED_RELATIVE_DIR.split(os.sep):
                parent = descriptor
                descriptor = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent,
                )
                stack.callback(os.close, descriptor)
                if os.fstat(descriptor).st_uid != account.pw_uid:
                    raise RuntimeError(
                        "Refusing managed Git credential directory owned by another user"
                    )
        except FileNotFoundError:
            yield None
            return
        except (OSError, ValueError) as exc:
            raise RuntimeError("Refusing unsafe managed Git credential ancestry") from exc
        yield parent, descriptor


def _clear_opened_managed_directory(parent: int, descriptor: int) -> None:
    """Clear the opened tree while retaining its replaceable directory entry."""
    for name in os.listdir(descriptor):
        entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(entry.st_mode):
            shutil.rmtree(name, dir_fd=descriptor)
        else:
            os.unlink(name, dir_fd=descriptor)
    try:
        current = os.stat("git", dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    opened = os.fstat(descriptor)
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise RuntimeError("Managed Git credential directory changed during removal")
    # The user can replace this entry after the identity check. Retaining the
    # empty directory avoids a path-based rmdir deleting that replacement.


def _managed_git_config(
    credential_specs: list[list[str]],
    ca_paths: dict[str, str],
    credential_path: str,
) -> str:
    lines = ["# Managed by basaltwater; do not edit"]
    usernames = {origin: username for origin, username in credential_specs}
    for origin in sorted(set(usernames) | set(ca_paths)):
        if origin in usernames:
            lines.extend(
                (
                    f"[credential {_git_config_quote(origin)}]",
                    f"\tusername = {_git_config_quote(usernames[origin])}",
                    "\thelper =",
                    "\thelper = "
                    + _git_config_quote(
                        f"store --file={shlex.quote(credential_path)}"
                    ),
                )
            )
        if origin in ca_paths:
            lines.extend(
                (
                    f"[http {_git_config_quote(origin)}]",
                    f"\tsslCAInfo = {_git_config_quote(ca_paths[origin])}",
                )
            )
    return "\n".join(lines) + "\n"


def configure_git_https_credentials(config: SetupConfig) -> None:
    """Reconcile managed Git HTTPS/LFS credentials for the target user."""
    if is_dry_run():
        action = "remove" if config.clear_git_credentials else "configure"
        print(f"  [DRY-RUN] Would {action} managed Git HTTPS credentials")
        return

    credential_specs: list[list[str]] = []
    credential_lines: list[str] = []
    for origin, username in config.git_credentials or []:
        normalized_origin = normalize_git_https_origin(origin)
        password = get_runtime_credential(config, username, service="git", origin=normalized_origin)
        if password is None:
            raise RuntimeError(
                f"Missing staged Git credential for {username} at {normalized_origin}"
            )
        credential_specs.append([normalized_origin, username])
        credential_lines.append(
            _credential_store_line(normalized_origin, username, password)
        )

    ca_content: dict[str, str] = {}
    for origin, encoded_pem in config.git_ca_pems or []:
        normalized_origin = normalize_git_https_origin(origin)
        ca_content[normalized_origin] = decode_git_ca_pem(encoded_pem)

    if not validate_username(config.username):
        raise ValueError("Invalid target user for Git credentials")
    source = str(Path(__file__).resolve().parents[1])
    run(
        ["/usr/sbin/runuser", "-u", config.username, "--", "/usr/bin/python3", "-I", "-B", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "from common.git_credential_steps import _main; _main(sys.argv[2])",
         source, config.username],
        input_data=json.dumps([config.clear_git_credentials, credential_specs, credential_lines, ca_content]),
        cwd="/", display_cmd=f"Reconcile managed Git credentials as {config.username}",
    )


def _main(username: str) -> None:
    account = pwd.getpwnam(username)
    if os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid:
        raise RuntimeError("Git credential setup must run as the selected account")
    os.umask(0o077)
    clear, credential_specs, credential_lines, ca_content = json.load(sys.stdin)
    _apply_git_configuration(account, clear, credential_specs, credential_lines, ca_content)


def _apply_git_configuration(
    account: pwd.struct_passwd,
    clear: bool,
    credential_specs: list[list[str]],
    credential_lines: list[str],
    ca_content: dict[str, str],
) -> None:
    managed_dir = os.path.join(account.pw_dir, _MANAGED_RELATIVE_DIR)
    include_path = os.path.join(managed_dir, _MANAGED_INCLUDE_NAME)
    credential_path = os.path.join(managed_dir, _MANAGED_CREDENTIAL_NAME)
    reject_nested_mounts(Path(managed_dir), include_root=True)
    if clear:
        with _managed_removal_directory(account) as opened:
            _remove_managed_include(account, include_path)
            if opened is not None:
                _clear_opened_managed_directory(*opened)
        print("  ✓ Removed managed Git HTTPS credentials")
        return

    _ensure_user_owned_directory(managed_dir, account, mode=0o700)
    ca_dir = os.path.join(managed_dir, "ca")
    ca_paths: dict[str, str] = {}
    if ca_content:
        _ensure_user_owned_directory(ca_dir, account, mode=0o700)
        desired_names: set[str] = set()
        for origin, content in ca_content.items():
            filename = git_ca_filename(origin)
            desired_names.add(filename)
            destination = os.path.join(ca_dir, filename)
            _write_user_file(destination, content, account, mode=0o644)
            ca_paths[origin] = destination
        for filename in os.listdir(ca_dir):
            stale_path = os.path.join(ca_dir, filename)
            if filename in desired_names:
                continue
            if os.path.islink(stale_path) or not os.path.isfile(stale_path):
                raise RuntimeError(f"Refusing unsafe stale Git CA path: {stale_path}")
            os.unlink(stale_path)
    elif os.path.lexists(ca_dir):
        if os.path.islink(ca_dir) or not os.path.isdir(ca_dir):
            raise RuntimeError(f"Refusing unsafe managed Git CA path: {ca_dir}")
        shutil.rmtree(ca_dir)

    if credential_lines:
        _write_user_file(
            credential_path,
            "\n".join(credential_lines) + "\n",
            account,
            mode=0o600,
        )
    elif os.path.lexists(credential_path):
        if os.path.islink(credential_path) or not os.path.isfile(credential_path):
            raise RuntimeError(
                f"Refusing unsafe managed Git credential file: {credential_path}"
            )
        os.unlink(credential_path)

    include_content = _managed_git_config(
        credential_specs,
        ca_paths,
        credential_path,
    )
    _write_user_file(include_path, include_content, account, mode=0o600)
    _install_managed_include(account, include_path)

    for origin, username in credential_specs:
        verification = _run_git(account, "--includes", "--get", f"credential.{origin}.username")
        if (
            verification.returncode != 0
            or (verification.stdout or "").strip() != username
        ):
            raise RuntimeError(
                f"Managed Git credential configuration could not be verified: {origin}"
            )

    for origin, ca_path in ca_paths.items():
        verification = _run_git(account, "--includes", "--get", f"http.{origin}.sslCAInfo")
        if (
            verification.returncode != 0
            or (verification.stdout or "").strip() != ca_path
        ):
            raise RuntimeError(
                f"Managed Git CA configuration could not be verified: {origin}"
            )

    origins = sorted(set(origin for origin, _username in credential_specs) | set(ca_paths))
    print(
        "  ✓ Managed Git HTTPS credentials configured for: "
        + ", ".join(origins)
    )

"""Manage public Git identity and private GitHub tokens for one local account."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading

from lib.atomic_io import write_text_atomic
from lib.validation import (
    validate_filesystem_path, validate_git_author_email, validate_git_author_name,
    validate_github_token,
)
from lib.validators import validate_username


_MAX_BYTES = 1024 * 1024
_HOST_KEY = re.compile(r'''^(?:github\.com|'github\.com'|"github\.com"):''')
_HOST_HEADER = re.compile(r'''^(?:github\.com|'github\.com'|"github\.com"):[ \t]*(?:#.*)?$''')


def _github_range(text: str) -> tuple[list[str], int | None, int]:
    lines = text.splitlines(keepends=True)
    starts = []
    for i, line in enumerate(lines):
        if _HOST_KEY.match(line):
            if not _HOST_HEADER.fullmatch(line.rstrip("\r\n")):
                raise ValueError("GitHub configuration requires a block host entry")
            starts.append(i)
    if len(starts) > 1:
        raise ValueError("GitHub configuration has duplicate host entries")
    start = starts[0] if starts else None
    end = len(lines)
    if start is not None:
        for i in range(start + 1, len(lines)):
            line = lines[i]
            if line.strip() and not line[0].isspace() and not line.startswith("#"):
                end = i
                break
    return lines, start, end


class AgentGitSettings:
    """Operate only as the configured non-root user in that user's owned home."""

    def __init__(self, home: str, username: str) -> None:
        self.home = Path(os.path.abspath(home))
        self.username = username
        self.git_file = self.home / ".gitconfig"
        self.github_file = self.home / ".config/gh/hosts.yml"
        self._lock = threading.RLock()

    def available(self) -> bool:
        try:
            if not validate_username(self.username) or os.geteuid() == 0:
                return False
            account = pwd.getpwnam(self.username)
            return (
                account.pw_uid == os.geteuid()
                and Path(os.path.abspath(account.pw_dir)) == self.home
                and not self.home.is_symlink() and self.home.is_dir()
                and self.home.stat().st_uid == os.geteuid()
            )
        except (KeyError, OSError):
            return False

    def _path(self, path: Path, *, create_parent: bool = False) -> None:
        if not self.available():
            raise RuntimeError("Account settings require the panel's non-root setup user")
        validate_filesystem_path(str(path))
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise RuntimeError("Account settings contain a symlinked path")
            if not current.exists():
                if create_parent and current != path:
                    current.mkdir(mode=0o700)
                else:
                    continue
            info = current.stat()
            if current == self.home or self.home in current.parents:
                if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                    raise RuntimeError("Account settings must be owned by the user and not writable by others")
            if current == path:
                if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
                    raise RuntimeError("Account settings must be a bounded regular file")
            elif not stat.S_ISDIR(info.st_mode):
                raise RuntimeError("Account settings have an invalid parent directory")

    def _read(self, path: Path, *, private: bool = False) -> str:
        self._path(path)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return ""
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & (0o077 if private else 0o022):
                raise RuntimeError("Account settings have unsafe ownership or permissions")
            content = stream.read(_MAX_BYTES + 1)
        if len(content) > _MAX_BYTES:
            raise RuntimeError("Account settings exceed the size limit")
        return content.decode("utf-8")

    def _environment(self) -> dict[str, str]:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"), GIT_CONFIG_NOSYSTEM="1")
        return environment

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        executable = shutil.which("git")
        if not executable:
            raise RuntimeError("Git is not installed")
        try:
            return subprocess.run([executable, "config", *args], capture_output=True, text=True, check=False,
                                  timeout=10, cwd=self.home, env=self._environment())
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Git configuration could not be read or updated") from exc

    @contextmanager
    def _file_lock(self, path: Path):
        lock = Path(str(path) + ".lock")
        self._path(lock, create_parent=True)
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            yield
        finally:
            os.close(descriptor)
            lock.unlink()

    def _edit_git(self, edits: list[tuple[str, list[str]]]) -> None:
        self._path(self.git_file)
        with self._file_lock(self.git_file), tempfile.TemporaryDirectory(prefix=".basaltwater-git-", dir=self.home) as directory:
            original = self._read(self.git_file)
            candidate = Path(directory) / "config"
            candidate.write_text(original, encoding="utf-8")
            candidate.chmod(0o600)
            for key, values in edits:
                for index, value in enumerate(values):
                    result = self._git(["--file", str(candidate), "--replace-all" if index == 0 else "--add", key, value])
                    if result.returncode != 0:
                        raise RuntimeError("Git configuration could not be updated; existing settings were retained")
            self._path(self.git_file)
            if self._read(self.git_file) != original:
                raise RuntimeError("Git configuration changed during the update; reload and try again")
            content = candidate.read_text(encoding="utf-8")
            if len(content.encode("utf-8")) > _MAX_BYTES:
                raise ValueError("Git configuration exceeds the size limit")
            write_text_atomic(str(self.git_file), content, mode=0o600)

    def snapshot(self) -> dict[str, object]:
        """Read local public settings; never contact a provider or return tokens."""
        result: dict[str, object] = {"available": self.available(), "name": "", "email": "", "git_error": "", "github_error": "", "github_present": False}
        if not result["available"]:
            return result
        with self._lock:
            try:
                # Explicit files exclude repository-local and conditional includes.
                for path in (self.home / ".config/git/config", self.git_file):
                    if not self._read(path):
                        continue
                    for field, key, validator in (("name", "user.name", validate_git_author_name), ("email", "user.email", validate_git_author_email)):
                        value = self._git(["--file", str(path), "--get", key])
                        if value.returncode == 0:
                            result[field] = validator(value.stdout.rstrip("\r\n"))
                        elif value.returncode != 1:
                            raise RuntimeError("Git configuration could not be read")
            except (OSError, RuntimeError, ValueError):
                result["git_error"] = "Git identity could not be read. Check the account's Git config and permissions."
            try:
                lines, start, end = _github_range(self._read(self.github_file, private=True))
                if start is not None:
                    result["github_present"] = any(
                        line.strip().startswith("oauth_token:") and line.partition(":")[2].strip() not in {"", "null", "~", "''", '\"\"'}
                        for line in lines[start:end]
                    )
            except (OSError, RuntimeError, ValueError):
                result["github_error"] = "GitHub credential storage could not be read. Check ownership, mode 0600, and symlinks."
        return result

    def set_identity(self, name: str, email: str) -> None:
        edits = [("user.name", [validate_git_author_name(name)]), ("user.email", [validate_git_author_email(email)])]
        with self._lock:
            self._edit_git(edits)

    def set_github_token(self, token: str) -> None:
        token = validate_github_token(token)
        gh = shutil.which("gh", path=os.pathsep.join((str(self.home / ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin")))
        if not gh:
            raise RuntimeError("Install GitHub CLI before saving a GitHub token")
        with self._lock:
            self._path(self.github_file, create_parent=True)
            with self._file_lock(self.github_file):
                original = self._read(self.github_file, private=True)
                lines, start, end = _github_range(original)
                entry = f"github.com:\n    oauth_token: {json.dumps(token)}\n    git_protocol: https\n"
                merged = "".join(lines[:start]) + entry + "".join(lines[end:]) if start is not None else original.rstrip("\n") + ("\n" if original else "") + entry
                if len(merged.encode("utf-8")) > _MAX_BYTES:
                    raise ValueError("GitHub configuration exceeds the size limit")
                # Fixed origin helpers support fine-grained tokens without a login
                # command that requires classic-token scopes or exposes the token.
                self._edit_git([(f"credential.https://{host}.helper", ["", f"!{shlex.quote(gh)} auth git-credential"])
                                for host in ("github.com", "gist.github.com")])
                self._path(self.github_file)
                if self._read(self.github_file, private=True) != original:
                    raise RuntimeError("GitHub configuration changed during the update; reload and try again")
                write_text_atomic(str(self.github_file), merged, mode=0o600)

    def remove_github_token(self) -> None:
        with self._lock:
            self._path(self.github_file, create_parent=True)
            with self._file_lock(self.github_file):
                original = self._read(self.github_file, private=True)
                lines, start, end = _github_range(original)
                if start is not None:
                    self._path(self.github_file)
                    if self._read(self.github_file, private=True) != original:
                        raise RuntimeError("GitHub configuration changed during the update; reload and try again")
                    write_text_atomic(str(self.github_file), "".join(lines[:start] + lines[end:]), mode=0o600)

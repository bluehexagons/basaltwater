"""Select native Node package commands without executing project code."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat

from lib.validation import validate_filesystem_path, validate_no_control_characters


_DECLARATION = re.compile(r"(npm|pnpm|yarn)@([1-9][0-9]*)\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
_LOCKFILES = {
    "npm": ("package-lock.json", "npm-shrinkwrap.json"),
    "pnpm": ("pnpm-lock.yaml",),
    "yarn": ("yarn.lock",),
}


@dataclass(frozen=True)
class NodePackageManager:
    name: str
    locked: bool
    modern_yarn: bool = False

    def _command(self, corepack: bool) -> list[str]:
        return ["corepack", self.name] if corepack and self.name != "npm" else [self.name]

    def install_command(self, *, corepack: bool = False) -> list[str]:
        command = self._command(corepack)
        if self.name == "npm":
            return [*command, "ci" if self.locked else "install"]
        if self.name == "pnpm":
            flag = "--frozen-lockfile" if self.locked else "--no-frozen-lockfile"
        elif self.modern_yarn:
            flag = "--immutable" if self.locked else "--no-immutable"
        else:
            return [*command, "install", *(["--frozen-lockfile"] if self.locked else [])]
        return [*command, "install", flag]

    def script_command(self, package: dict, script: str, *, arguments: tuple[str, ...] = (),
                       corepack: bool = False) -> list[str]:
        if not isinstance(script, str) or not 1 <= len(script) <= 128 or script.startswith("-"):
            raise ValueError("Package script must be a literal name of 1–128 characters")
        validate_no_control_characters(script, "package script")
        scripts = package.get("scripts", {})
        value = scripts.get(script) if isinstance(scripts, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"package.json has no runnable {script!r} script")
        separator = ["--"] if arguments and self.name == "npm" else []
        return [*self._command(corepack), "run", script, *separator, *arguments]


def select_package_manager(directory: str | Path, package: dict) -> NodePackageManager:
    """Refuse conflicting managers and unsafe locks instead of guessing."""
    validate_filesystem_path(str(directory), must_exist=True)
    directory = Path(directory)
    declared = package.get("packageManager", "")
    if not isinstance(declared, str):
        raise ValueError("packageManager must be a string")
    declaration = _DECLARATION.fullmatch(declared) if declared else None
    if declared and declaration is None:
        raise ValueError("packageManager must name npm, pnpm or yarn with an exact version")
    name = declaration[1] if declaration else None
    locks = []
    for manager, filenames in _LOCKFILES.items():
        present = False
        for filename in filenames:
            path = directory / filename
            if not os.path.lexists(path):
                continue
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError(f"Package lock must be a regular file without symlinks: {filename}")
            present = True
        if present:
            locks.append(manager)
    if len(locks) > 1 or (name and locks and name not in locks):
        raise ValueError("Conflicting package-manager declarations or lockfiles; choose one before running")
    name = name or (locks[0] if locks else "npm")
    modern_yarn = False
    if name == "yarn":
        if declaration:
            modern_yarn = int(declaration[2]) >= 2
        elif locks:
            descriptor = os.open(directory / "yarn.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("Yarn lock must be a regular file")
                header = os.read(descriptor, 4096)
            finally:
                os.close(descriptor)
            if re.search(rb"(?m)^__metadata:[ \t]*\r?$", header):
                modern_yarn = True
            elif b"# yarn lockfile v1" not in header:
                raise ValueError("Unknown Yarn lock format; declare the exact Yarn version in packageManager")
    return NodePackageManager(name, bool(locks), modern_yarn)

"""Debian APT source detection and repair helpers."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from lib.remote_utils import read_os_release
from lib.validation import validate_debian_codename, validate_filesystem_path


OFFICIAL_DEBIAN_MIRROR = "https://deb.debian.org/debian"
OFFICIAL_DEBIAN_SECURITY_MIRROR = "https://security.debian.org/debian-security"
DEBIAN_ARCHIVE_KEYRING = "/usr/share/keyrings/debian-archive-keyring.gpg"
DEBIAN_ARCHIVE_KEYRING_PGP = "/usr/share/keyrings/debian-archive-keyring.pgp"
MANAGED_SOURCE_FILENAME = "basaltwater-debian.sources"
MANAGED_SOURCE_MARKER = "# Managed by basaltwater"
XRDP_SID_SOURCE = """Types: deb
URIs: https://deb.debian.org/debian
Suites: sid
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
"""
XRDP_SID_PREFERENCE = """Package: *
Pin: release n=sid
Pin-Priority: 50
"""
_DEFAULT_DEBIAN_ARCHIVE_KEYRING = DEBIAN_ARCHIVE_KEYRING
_DEBIAN_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.-]*$")


@dataclass(frozen=True)
class AptSourceEntry:
    """One active binary Debian source parsed from an APT source file."""

    path: str
    uri: str
    suite: str
    components: tuple[str, ...]


@dataclass(frozen=True)
class AptSourceStatus:
    """The Debian source properties required before package installation."""

    codename: str
    entries: tuple[AptSourceEntry, ...]
    cdrom_sources: tuple[str, ...]
    has_official_base: bool
    has_official_security: bool


def _find_debian_archive_keyring() -> Optional[str]:
    """Return the installed Debian archive keyring path.

    Debian 13 keeps the historical ``.gpg`` name as a compatibility symlink
    to the canonical ``.pgp`` keyring. Prefer the canonical path while
    retaining the old path for older Debian releases and tests that provide a
    temporary keyring.
    """

    candidates = []
    if DEBIAN_ARCHIVE_KEYRING != _DEFAULT_DEBIAN_ARCHIVE_KEYRING:
        candidates.append(DEBIAN_ARCHIVE_KEYRING)
    candidates.extend((DEBIAN_ARCHIVE_KEYRING_PGP, DEBIAN_ARCHIVE_KEYRING))
    for path in dict.fromkeys(candidates):
        if os.path.isfile(path):
            return path
    return None


def get_debian_codename(os_release_path: str = "/etc/os-release") -> str:
    """Return and validate the installed Debian release codename."""

    release = read_os_release(os_release_path)
    if release.get("ID", "").lower() != "debian":
        raise RuntimeError("Debian APT source repair requires a Debian target")

    codename = release.get("VERSION_CODENAME", "").lower()
    if not codename:
        raise RuntimeError(
            "Debian release codename is unavailable in /etc/os-release; "
            "repair APT sources manually before continuing"
        )
    try:
        return validate_debian_codename(codename)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _source_files(apt_dir: str) -> list[str]:
    """Return source files APT reads, excluding disabled backup suffixes."""

    files: list[str] = []
    main_list = os.path.join(apt_dir, "sources.list")
    if os.path.isfile(main_list):
        files.append(main_list)

    source_dir = os.path.join(apt_dir, "sources.list.d")
    if os.path.isdir(source_dir):
        files.extend(
            os.path.join(source_dir, name)
            for name in sorted(os.listdir(source_dir))
            if name.endswith((".list", ".sources"))
            and os.path.isfile(os.path.join(source_dir, name))
        )
    return files


def _parse_one_line_source(line: str, path: str) -> Optional[AptSourceEntry]:
    stripped = line.strip()
    if not stripped.startswith("deb ") and not stripped.startswith("deb\t"):
        return None
    try:
        tokens = shlex.split(stripped, comments=True)
    except ValueError:
        return None
    if not tokens or tokens[0] != "deb":
        return None

    uri_index = 1
    if uri_index < len(tokens) and tokens[uri_index].startswith("["):
        while uri_index < len(tokens) and not tokens[uri_index].endswith("]"):
            uri_index += 1
        uri_index += 1
    if len(tokens) <= uri_index + 1:
        return None
    return AptSourceEntry(
        path=path,
        uri=tokens[uri_index],
        suite=tokens[uri_index + 1],
        components=tuple(tokens[uri_index + 2:]),
    )


def _deb822_stanzas(content: str) -> list[list[str]]:
    stanzas: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines(keepends=True):
        if not line.strip():
            if current:
                stanzas.append(current)
                current = []
        else:
            current.append(line)
    if current:
        stanzas.append(current)
    return stanzas


def _deb822_fields(stanza: list[str]) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    current_key: Optional[str] = None
    for line in stanza:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[0].isspace() and current_key:
            fields[current_key].extend(stripped.split())
            continue
        key, separator, value = line.partition(":")
        if not separator:
            current_key = None
            continue
        current_key = key.strip().lower()
        fields[current_key] = value.strip().split()
    return fields


def _parse_deb822_stanza(stanza: list[str], path: str) -> list[AptSourceEntry]:
    fields = _deb822_fields(stanza)
    if fields.get("enabled", ["yes"])[0].lower() == "no":
        return []
    if "deb" not in {source_type.lower() for source_type in fields.get("types", [])}:
        return []

    entries: list[AptSourceEntry] = []
    for uri in fields.get("uris", []):
        for suite in fields.get("suites", []):
            entries.append(
                AptSourceEntry(
                    path=path,
                    uri=uri,
                    suite=suite,
                    components=tuple(fields.get("components", [])),
                )
            )
    return entries


def parse_apt_sources(apt_dir: str = "/etc/apt") -> tuple[AptSourceEntry, ...]:
    """Parse active binary sources from classic and deb822 APT files."""

    validate_filesystem_path(apt_dir, must_exist=True)
    entries: list[AptSourceEntry] = []
    for path in _source_files(apt_dir):
        try:
            with open(path, encoding="utf-8") as file_obj:
                content = file_obj.read()
        except OSError:
            continue

        if path.endswith(".sources"):
            for stanza in _deb822_stanzas(content):
                entries.extend(_parse_deb822_stanza(stanza, path))
        else:
            for line in content.splitlines():
                entry = _parse_one_line_source(line, path)
                if entry is not None:
                    entries.append(entry)
    return tuple(entries)


def _is_cdrom_uri(uri: str) -> bool:
    return uri.lower().startswith("cdrom:")


def _line_contains_active_cdrom(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    try:
        return any(_is_cdrom_uri(token) for token in shlex.split(stripped, comments=True))
    except ValueError:
        return False


def _official_host_and_path(uri: str) -> tuple[str, str] | None:
    parsed = urlparse(uri)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname.lower(), parsed.path.rstrip("/")


def _is_official_base_uri(uri: str) -> bool:
    host_and_path = _official_host_and_path(uri)
    return host_and_path in {
        ("deb.debian.org", "/debian"),
    }


def _is_official_security_uri(uri: str) -> bool:
    host_and_path = _official_host_and_path(uri)
    return host_and_path in {
        ("security.debian.org", ""),
        ("security.debian.org", "/debian-security"),
        ("deb.debian.org", "/debian-security"),
    }


def inspect_apt_sources(
    codename: str,
    apt_dir: str = "/etc/apt",
) -> AptSourceStatus:
    """Inspect the active APT sources for a specific Debian codename."""

    codename = validate_debian_codename(codename)
    entries = parse_apt_sources(apt_dir)
    cdrom_sources = tuple(
        sorted({entry.path for entry in entries if _is_cdrom_uri(entry.uri)})
    )
    has_main = any(
        _is_official_base_uri(entry.uri)
        and entry.suite == codename
        and "main" in {component.lower() for component in entry.components}
        for entry in entries
    )
    has_security = any(
        _is_official_security_uri(entry.uri)
        and entry.suite == f"{codename}-security"
        and "main" in {component.lower() for component in entry.components}
        for entry in entries
    )
    return AptSourceStatus(
        codename=codename,
        entries=entries,
        cdrom_sources=cdrom_sources,
        has_official_base=has_main,
        has_official_security=has_security,
    )


def _is_stale_official_entry(entry: AptSourceEntry, codename: str) -> bool:
    """Return whether an official Debian entry targets another release."""

    if _is_official_base_uri(entry.uri):
        allowed_suites = {
            codename,
            f"{codename}-backports",
            f"{codename}-proposed-updates",
            f"{codename}-updates",
        }
        return entry.suite not in allowed_suites
    if _is_official_security_uri(entry.uri):
        return entry.suite != f"{codename}-security"
    return False


def _components_for_managed_sources(entries: tuple[AptSourceEntry, ...]) -> tuple[str, ...]:
    """Preserve useful Debian components when creating a managed source."""

    components: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not (
            _is_official_base_uri(entry.uri)
            or _is_official_security_uri(entry.uri)
            or _is_cdrom_uri(entry.uri)
        ):
            continue
        for component in entry.components:
            if not _DEBIAN_COMPONENT_PATTERN.fullmatch(component):
                continue
            normalized = component.lower()
            if normalized not in seen:
                seen.add(normalized)
                components.append(component)
    if "main" not in seen:
        components.insert(0, "main")
    return tuple(components)


def _disable_stale_official_sources(apt_dir: str, codename: str) -> list[str]:
    """Comment active official Debian entries for older or alias suites."""

    changed_paths: list[str] = []
    for path in _source_files(apt_dir):
        with open(path, encoding="utf-8") as file_obj:
            content = file_obj.read()

        # Desktop setup deliberately uses this exact, low-priority source.
        # Do not exempt arbitrary Sid entries, or a matching filename without
        # its pin. Also repair files commented by our older generic cleanup.
        pin_path = os.path.join(apt_dir, "preferences.d", "basaltwater-sid.pref")
        if path == os.path.join(apt_dir, "sources.list.d", "basaltwater-sid.sources"):
            try:
                with open(pin_path, encoding="utf-8") as pin_file:
                    pinned = pin_file.read() == XRDP_SID_PREFERENCE
            except FileNotFoundError:
                pinned = False
            disabled = "".join(_comment_lines(XRDP_SID_SOURCE.splitlines(keepends=True)))
            if pinned and content.rstrip() in {XRDP_SID_SOURCE.rstrip(), disabled.rstrip()}:
                if content.rstrip() != XRDP_SID_SOURCE.rstrip():
                    _backup_file(path)
                    _write_text_atomically(path, XRDP_SID_SOURCE)
                continue

        if path.endswith(".sources"):
            output: list[str] = []
            changed = False
            for stanza in _deb822_stanzas(content):
                entries = _parse_deb822_stanza(stanza, path)
                if any(_is_stale_official_entry(entry, codename) for entry in entries):
                    output.extend(_comment_lines(stanza))
                    changed = True
                else:
                    output.extend(stanza)
                output.append("\n")
            new_content = "".join(output)
        else:
            changed = False
            output = []
            for line in content.splitlines(keepends=True):
                entry = _parse_one_line_source(line, path)
                if entry is not None and _is_stale_official_entry(entry, codename):
                    output.append(f"# Disabled by basaltwater: {line}")
                    changed = True
                else:
                    output.append(line)
            new_content = "".join(output)

        if changed and new_content != content:
            _backup_file(path)
            _write_text_atomically(path, new_content)
            changed_paths.append(path)
    return changed_paths


def _backup_file(path: str) -> None:
    backup_path = f"{path}.basaltwater.bak"
    if not os.path.exists(backup_path):
        shutil.copy2(path, backup_path)


def _write_text_atomically(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=".basaltwater_apt_",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _comment_lines(lines: list[str]) -> list[str]:
    return [
        line if line.lstrip().startswith("#") else f"# Disabled by basaltwater: {line}"
        for line in lines
    ]


def _disable_cdrom_sources(apt_dir: str) -> list[str]:
    """Comment active CD-ROM source entries and return changed file paths."""

    changed_paths: list[str] = []
    for path in _source_files(apt_dir):
        with open(path, encoding="utf-8") as file_obj:
            content = file_obj.read()

        if path.endswith(".sources"):
            output: list[str] = []
            changed = False
            for stanza in _deb822_stanzas(content):
                entries = _parse_deb822_stanza(stanza, path)
                fields = _deb822_fields(stanza)
                has_active_cdrom = (
                    fields.get("enabled", ["yes"])[0].lower() != "no"
                    and "deb" in {source_type.lower() for source_type in fields.get("types", [])}
                    and any(_is_cdrom_uri(uri) for uri in fields.get("uris", []))
                )
                if has_active_cdrom or (
                    entries and any(_is_cdrom_uri(entry.uri) for entry in entries)
                ):
                    output.extend(_comment_lines(stanza))
                    changed = True
                else:
                    output.extend(stanza)
                output.append("\n")
            new_content = "".join(output)
        else:
            changed = False
            output = []
            for line in content.splitlines(keepends=True):
                if _line_contains_active_cdrom(line):
                    output.append(f"# Disabled by basaltwater: {line}")
                    changed = True
                else:
                    output.append(line)
            new_content = "".join(output)

        if changed and new_content != content:
            _backup_file(path)
            _write_text_atomically(path, new_content)
            changed_paths.append(path)
    return changed_paths


def _managed_source_path(apt_dir: str) -> str:
    return os.path.join(apt_dir, "sources.list.d", MANAGED_SOURCE_FILENAME)


def _ensure_managed_sources(
    apt_dir: str,
    codename: str,
    components: tuple[str, ...],
    *,
    include_base: bool = True,
    include_security: bool = True,
) -> bool:
    source_dir = os.path.join(apt_dir, "sources.list.d")
    os.makedirs(source_dir, mode=0o755, exist_ok=True)
    path = _managed_source_path(apt_dir)
    keyring = _find_debian_archive_keyring()
    if keyring is None:
        raise RuntimeError("Debian archive keyring is unavailable")
    component_text = " ".join(components)
    stanzas = [
        f"{MANAGED_SOURCE_MARKER}. Do not edit; rerun basaltw after a Debian release change."
    ]
    if include_base:
        stanzas.append(
            f"""Types: deb
URIs: {OFFICIAL_DEBIAN_MIRROR}
Suites: {codename} {codename}-updates
Components: {component_text}
Signed-By: {keyring}"""
        )
    if include_security:
        stanzas.append(
            f"""Types: deb
URIs: {OFFICIAL_DEBIAN_SECURITY_MIRROR}
Suites: {codename}-security
Components: {component_text}
Signed-By: {keyring}"""
        )
    content = "\n\n".join(stanzas) + "\n"

    if os.path.exists(path):
        with open(path, encoding="utf-8") as file_obj:
            existing = file_obj.read()
        if MANAGED_SOURCE_MARKER not in existing:
            raise RuntimeError(
                f"APT source file already exists and is not managed by basaltwater: {path}"
            )
        if existing == content:
            return False
        _backup_file(path)
    _write_text_atomically(path, content)
    return True


def _remove_redundant_managed_source(
    apt_dir: str,
    status: AptSourceStatus,
) -> bool:
    """Remove our source file when other official sources already cover both suites."""

    path = _managed_source_path(apt_dir)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as file_obj:
            managed_content = file_obj.read()
    except OSError:
        return False
    if MANAGED_SOURCE_MARKER not in managed_content:
        return False

    other_entries = tuple(entry for entry in status.entries if entry.path != path)
    has_base = any(
        _is_official_base_uri(entry.uri)
        and entry.suite == status.codename
        and "main" in {component.lower() for component in entry.components}
        for entry in other_entries
    )
    has_security = any(
        _is_official_security_uri(entry.uri)
        and entry.suite == f"{status.codename}-security"
        and "main" in {component.lower() for component in entry.components}
        for entry in other_entries
    )
    if not (has_base and has_security):
        return False

    _backup_file(path)
    os.unlink(path)
    return True


def ensure_debian_package_sources(
    apt_dir: str = "/etc/apt",
    os_release_path: str = "/etc/os-release",
) -> Optional[AptSourceStatus]:
    """Ensure a Debian install has current official remote package sources."""

    validate_filesystem_path(apt_dir, must_exist=True)
    release = read_os_release(os_release_path)
    if release.get("ID", "").lower() != "debian":
        return None

    codename = get_debian_codename(os_release_path)
    if _find_debian_archive_keyring() is None:
        raise RuntimeError(
            "Debian archive keyring is missing; expected "
            f"{DEBIAN_ARCHIVE_KEYRING_PGP} or {DEBIAN_ARCHIVE_KEYRING}. "
            "Install debian-archive-keyring before continuing"
        )

    initial_status = inspect_apt_sources(codename, apt_dir)
    components = _components_for_managed_sources(initial_status.entries)
    disabled_paths = _disable_cdrom_sources(apt_dir)
    stale_paths = _disable_stale_official_sources(apt_dir, codename)
    status = inspect_apt_sources(codename, apt_dir)
    removed_redundant_managed_source = _remove_redundant_managed_source(
        apt_dir,
        status,
    )
    if removed_redundant_managed_source:
        status = inspect_apt_sources(codename, apt_dir)
    added_managed_sources = False
    if not status.has_official_base or not status.has_official_security:
        added_managed_sources = _ensure_managed_sources(
            apt_dir,
            codename,
            components,
            include_base=not status.has_official_base,
            include_security=not status.has_official_security,
        )
        status = inspect_apt_sources(codename, apt_dir)

    if status.cdrom_sources:
        raise RuntimeError(
            "Active CD-ROM-only APT sources remain in: "
            + ", ".join(status.cdrom_sources)
        )
    if not status.has_official_base or not status.has_official_security:
        raise RuntimeError(
            f"Could not configure official Debian {codename} and security APT sources"
        )

    if disabled_paths:
        print("  ✓ Disabled CD-ROM-only APT sources")
    if stale_paths:
        print("  ✓ Disabled stale official Debian APT sources")
    if removed_redundant_managed_source:
        print("  ✓ Removed redundant basaltwater Debian APT source")
    if added_managed_sources:
        print(f"  ✓ Added official Debian {codename} APT sources")
    else:
        print(f"  ✓ Official Debian {codename} APT sources detected")
    return status

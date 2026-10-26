"""Validate the narrow runtime contract for a staged prebuilt release.

This module does not authenticate a release, verify its artifact descriptor, or
activate it. Callers must do those things separately and pass the private,
verified extraction directory here before activation.
"""

from __future__ import annotations

import os
import shlex
import stat
import struct
from collections.abc import Mapping

from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import validate_artifact_path
from lib.project_manifest import Component, Manifest, artifact_paths
from lib.setup_directories import directory_fd
from lib.validation import (
    validate_filesystem_path,
    validate_no_control_characters,
    validate_systemd_exec_command,
)


_ELF_HEADER = struct.Struct("<HHIQQQIHHHHHH")
_PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
_DYNAMIC_ENTRY = struct.Struct("<qQ")
_MAX_PROGRAM_HEADERS = 1024
_MAX_DYNAMIC_BYTES = 1024 * 1024
_U64_LIMIT = 1 << 64

_ET_EXEC = 2
_ET_DYN = 3
_EM_X86_64 = 62
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PT_INTERP = 3
_PF_X = 1
_DT_NULL = 0
_DT_NEEDED = 1

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def validate_prebuilt_runtime(
    manifest: Manifest,
    release_directory: str,
    *,
    platform: object,
) -> None:
    """Check supported runtime compatibility for a verified private release.

    Static components have no executable runtime requirement. Each service must
    resolve to one declared, executable, self-contained x86-64 ELF file. This
    intentionally accepts only the first Debian 13 Linux platform slice.
    """
    if not isinstance(platform, Mapping) or dict(platform) != DEBIAN_PLATFORM:
        raise ValueError(
            "Unsupported prebuilt runtime platform; only linux/x86_64/debian-13 is supported"
        )
    if not isinstance(manifest, Manifest) or type(manifest.version) is not int or manifest.version != 1:
        raise ValueError("Unsupported prebuilt runtime manifest")
    if not isinstance(manifest.components, list) or not manifest.components:
        raise ValueError("Prebuilt runtime validation requires manifest components")
    if not isinstance(release_directory, str):
        raise ValueError("Release directory must be an absolute filesystem path")

    validate_filesystem_path(release_directory)
    if not os.path.isabs(release_directory):
        raise ValueError("Release directory must be absolute")

    service_paths: list[tuple[str, str]] = []
    for component in manifest.components:
        if not isinstance(component, Component):
            raise ValueError("Prebuilt runtime manifest contains an invalid component")
        if not isinstance(component.type, str) or component.type not in {"static", "service"}:
            raise ValueError(
                f"Unsupported prebuilt runtime component type {component.type!r}; "
                "Godot and other runtime adapters are deferred"
            )
        exports = artifact_paths(component)
        if component.type == "static":
            continue

        if component.binary is not None:
            executable = _normalize_binary_path(component.binary)
        else:
            executable = _exec_binary_path(component.exec)
        _reject_interpreter(executable)
        if not _is_exported(executable, exports):
            raise ValueError(
                f"Service executable for {component.name!r} is not covered by manifest artifacts"
            )
        service_paths.append((component.name, executable))

    try:
        with directory_fd(release_directory) as root:
            for component_name, executable in service_paths:
                _validate_service_executable(root, executable, component_name)
    except OSError as exc:
        raise ValueError("Cannot safely inspect the prebuilt release directory") from exc


def _normalize_binary_path(value: object) -> str:
    """Apply the manifest's legacy normpath behavior, then validate canonically."""
    if not isinstance(value, str) or not value:
        raise ValueError("Service binary must be a non-empty relative path")
    validate_no_control_characters(value, "Service binary")
    validate_filesystem_path(value, must_exist=False)
    if os.path.isabs(value):
        raise ValueError("Service binary must be relative to the release directory")
    normalized = os.path.normpath(value)
    try:
        return validate_artifact_path(normalized)
    except ValueError as exc:
        raise ValueError("Service binary is not a safe relative release path") from exc


def _exec_binary_path(value: object) -> str:
    """Accept only an explicit release-root executable as argv[0]."""
    if not isinstance(value, str) or not value:
        raise ValueError("Exec-only services require an explicit executable command")
    validate_systemd_exec_command(value, "Service exec")
    try:
        arguments = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError("Service exec must use valid command quoting") from exc
    if not arguments:
        raise ValueError("Service exec must name an executable")

    prefix = "{{release_dir}}/"
    first = arguments[0]
    if not first.startswith(prefix):
        raise ValueError(
            "Exec-only services must start with {{release_dir}}/relative/path"
        )
    relative = first[len(prefix):]
    if "{{" in relative or "}}" in relative:
        raise ValueError("Service executable path cannot contain additional placeholders")
    try:
        return validate_artifact_path(relative)
    except ValueError as exc:
        raise ValueError("Service exec must name a safe relative release executable") from exc


def _is_exported(executable: str, exports: list[str]) -> bool:
    return any(
        export == "." or executable == export or executable.startswith(export + "/")
        for export in exports
    )


def _reject_interpreter(path: str) -> None:
    name = path.rsplit("/", 1)[-1].casefold()
    shells = {"ash", "bash", "busybox", "dash", "fish", "ksh", "sh", "zsh"}
    if name in shells or name in {"node", "nodejs", "python"}:
        raise ValueError("Interpreter and shell service commands are unsupported for prebuilt runtimes")
    if name in {"python2", "python3"} or name.startswith(("python2.", "python3.")):
        raise ValueError("Interpreter and shell service commands are unsupported for prebuilt runtimes")


def _open_relative_parent(root: int, path: str) -> tuple[int, str]:
    """Open relative directory components without following links."""
    validate_artifact_path(path)
    parts = path.split("/")
    parent = os.dup(root)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=parent)
            os.close(parent)
            parent = child
        return parent, parts[-1]
    except BaseException:
        os.close(parent)
        raise


def _stat_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _pread_exact(descriptor: int, size: int, offset: int) -> bytes:
    """Read an already-bounded metadata region, allowing short OS reads."""
    chunks = bytearray()
    while len(chunks) < size:
        data = os.pread(descriptor, size - len(chunks), offset + len(chunks))
        if not data:
            raise ValueError("Truncated ELF executable")
        chunks.extend(data)
    return bytes(chunks)


def _validate_service_executable(root: int, path: str, component_name: str) -> None:
    parent, leaf = _open_relative_parent(root, path)
    try:
        entry_before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(entry_before.st_mode) or entry_before.st_nlink != 1:
            raise ValueError(f"Service executable for {component_name!r} must be a regular unlinked file")
        if not entry_before.st_mode & 0o111:
            raise ValueError(f"Service executable for {component_name!r} is not executable")

        descriptor = os.open(leaf, _FILE_FLAGS, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if _stat_identity(before) != _stat_identity(entry_before):
                raise ValueError("Service executable changed while it was being opened")
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError(f"Service executable for {component_name!r} must be a regular unlinked file")
            if not before.st_mode & 0o111:
                raise ValueError(f"Service executable for {component_name!r} is not executable")

            _validate_elf64_static_x86_64(descriptor, before.st_size)

            after = os.fstat(descriptor)
            entry_after = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            identity = _stat_identity(before)
            if _stat_identity(after) != identity or _stat_identity(entry_after) != identity:
                raise ValueError("Service executable changed during runtime validation")
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _validate_elf64_static_x86_64(descriptor: int, file_size: int) -> None:
    if file_size < 64:
        raise ValueError("Service executable is not a complete ELF64 file")
    ident = _pread_exact(descriptor, 16, 0)
    if ident[:4] != b"\x7fELF":
        raise ValueError("Service executable is not ELF")
    if ident[4] != 2 or ident[5] != 1 or ident[6] != 1:
        raise ValueError("Service executable must use ELF64 little-endian version 1")

    header = _ELF_HEADER.unpack(_pread_exact(descriptor, _ELF_HEADER.size, 16))
    (elf_type, machine, version, entry, program_offset, _section_offset, flags,
     header_size, program_entry_size, program_count, _section_entry_size,
     _section_count, _section_names_index) = header
    if version != 1:
        raise ValueError("Service executable has an unsupported ELF version")
    if machine != _EM_X86_64:
        raise ValueError("Service executable must target x86_64")
    if elf_type not in {_ET_EXEC, _ET_DYN}:
        raise ValueError("Service executable must be ET_EXEC or ET_DYN")
    if flags != 0 or header_size != 64 or program_entry_size != _PROGRAM_HEADER.size:
        raise ValueError("Service executable has unsupported ELF header sizes or flags")
    if program_count == 0 or program_count == 0xFFFF or program_count > _MAX_PROGRAM_HEADERS:
        raise ValueError("Service executable has an unsupported ELF program-header count")
    if program_offset < header_size:
        raise ValueError("Service executable has an invalid ELF program-header offset")

    table_size = program_count * program_entry_size
    if table_size > _MAX_PROGRAM_HEADERS * _PROGRAM_HEADER.size or program_offset > file_size - table_size:
        raise ValueError("Service executable has an out-of-bounds ELF program-header table")
    table = _pread_exact(descriptor, table_size, program_offset)

    loads: list[tuple[int, int, int, int]] = []
    dynamic: tuple[int, int, int, int] | None = None
    has_interpreter = False
    for index in range(program_count):
        program = _PROGRAM_HEADER.unpack_from(table, index * _PROGRAM_HEADER.size)
        (kind, segment_flags, offset, virtual_address, _physical_address,
         file_bytes, memory_bytes, alignment) = program
        if offset > file_size or file_bytes > file_size - offset:
            raise ValueError("Service executable has program data outside the ELF file")
        if kind == _PT_INTERP:
            has_interpreter = True
        elif kind == _PT_LOAD:
            if file_bytes > memory_bytes or virtual_address > _U64_LIMIT - memory_bytes:
                raise ValueError("Service executable has invalid ELF load-segment bounds")
            if alignment not in {0, 1}:
                if alignment & (alignment - 1) or virtual_address % alignment != offset % alignment:
                    raise ValueError("Service executable has unsupported ELF load alignment")
            loads.append((segment_flags, offset, virtual_address, file_bytes))
        elif kind == _PT_DYNAMIC:
            if dynamic is not None:
                raise ValueError("Service executable has multiple ELF dynamic segments")
            dynamic = (offset, virtual_address, file_bytes, memory_bytes)

    if has_interpreter:
        raise ValueError("Service executable requires a dynamic ELF interpreter")
    if not any(flags_value & _PF_X and size and address <= entry < address + size
               for flags_value, _offset, address, size in loads):
        raise ValueError("Service executable entry is not covered by an executable ELF load segment")

    if dynamic is None:
        return
    dynamic_offset, dynamic_address, dynamic_size, dynamic_memory_size = dynamic
    if (dynamic_size < _DYNAMIC_ENTRY.size or dynamic_size > _MAX_DYNAMIC_BYTES
            or dynamic_size % _DYNAMIC_ENTRY.size or dynamic_memory_size < dynamic_size):
        raise ValueError("Service executable has an invalid or oversized ELF dynamic segment")
    if not any(
        load_offset <= dynamic_offset
        and dynamic_offset + dynamic_size <= load_offset + load_size
        and load_address + (dynamic_offset - load_offset) == dynamic_address
        for _load_flags, load_offset, load_address, load_size in loads
    ):
        raise ValueError("Service executable dynamic data is outside a file-backed ELF load segment")

    entries = _pread_exact(descriptor, dynamic_size, dynamic_offset)
    found_null = False
    for offset in range(0, dynamic_size, _DYNAMIC_ENTRY.size):
        tag, _value = _DYNAMIC_ENTRY.unpack_from(entries, offset)
        if tag == _DT_NULL:
            found_null = True
            break
        if tag == _DT_NEEDED:
            raise ValueError("Service executable requires a shared ELF library")
    if not found_null:
        raise ValueError("Service executable has an unterminated ELF dynamic segment")

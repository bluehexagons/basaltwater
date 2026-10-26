"""Runtime compatibility checks for verified prebuilt release trees."""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import build_runtime
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_runtime import validate_prebuilt_runtime
from lib.project_manifest import parse_manifest


_ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
_PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
_DYNAMIC_ENTRY = struct.Struct("<qQ")
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PT_INTERP = 3


def _elf64(*, elf_type: int = 2, machine: int = 62, version: int = 1,
           osabi: int = 0, abi_version: int = 0,
           dynamic: str | None = None, interpreter: bool = False,
           program_count: int | None = None, truncated_table: bool = False) -> bytes:
    """Build a tiny ELF fixture without invoking a host compiler."""
    load_offset = 0
    dynamic_offset = 0x200
    dynamic_bytes = b""
    if dynamic == "needed":
        dynamic_bytes = _DYNAMIC_ENTRY.pack(1, 0x1000) + _DYNAMIC_ENTRY.pack(0, 0)
    elif dynamic == "static":
        dynamic_bytes = _DYNAMIC_ENTRY.pack(24, 0) + _DYNAMIC_ENTRY.pack(0, 0)
    elif dynamic == "unterminated":
        dynamic_bytes = _DYNAMIC_ENTRY.pack(24, 0)
    elif dynamic == "oversized":
        dynamic_bytes = bytes(1024 * 1024 + 16)

    interpreter_offset = 0x300
    interpreter_bytes = b"/lib64/ld-linux-x86-64.so.2\0" if interpreter else b""
    file_size = max(4096, dynamic_offset + len(dynamic_bytes),
                    interpreter_offset + len(interpreter_bytes))
    if truncated_table:
        file_size = 80

    programs = [
        ( _PT_LOAD, 5, load_offset, 0x400000, 0x400000,
         file_size, file_size, 0x1000),
    ]
    if dynamic is not None:
        programs.append((_PT_DYNAMIC, 6, dynamic_offset, 0x400000 + dynamic_offset,
                         0x400000 + dynamic_offset, len(dynamic_bytes), len(dynamic_bytes), 8))
    if interpreter:
        programs.append((_PT_INTERP, 4, interpreter_offset, 0x400000 + interpreter_offset,
                         0x400000 + interpreter_offset, len(interpreter_bytes),
                         len(interpreter_bytes), 1))

    ident = b"\x7fELF" + bytes((2, 1, 1, osabi, abi_version)) + bytes(7)
    count = len(programs) if program_count is None else program_count
    header = _ELF_HEADER.pack(
        ident, elf_type, machine, version, 0x400080, 64, 0, 0, 64, 56,
        count, 0, 0, 0,
    )
    table = b"".join(_PROGRAM_HEADER.pack(*program) for program in programs)
    contents = bytearray(file_size)
    contents[:64] = header
    contents[64:64 + len(table)] = table
    if dynamic_bytes and not truncated_table:
        contents[dynamic_offset:dynamic_offset + len(dynamic_bytes)] = dynamic_bytes
    if interpreter_bytes and not truncated_table:
        contents[interpreter_offset:interpreter_offset + len(interpreter_bytes)] = interpreter_bytes
    if truncated_table:
        return bytes(contents[:80])
    return bytes(contents)


class BuildRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.release = self.root / "release"
        self.release.mkdir(mode=0o700)

    def manifest(self, *, binary: str = "bin/api", artifacts: list[str] | None = None,
                 exec_command: str | None = None, component_type: str = "service"):
        component = {
            "name": "api", "type": component_type, "domain": "{{domain}}",
        }
        if component_type == "service":
            component["port"] = "auto"
            if exec_command is None:
                component["binary"] = binary
            else:
                component["exec"] = exec_command
            if artifacts is not None:
                component["artifacts"] = artifacts
        else:
            component["output"] = "dist"
        return parse_manifest({"version": 1, "components": [component]})

    def executable(self, content: bytes | None = None) -> Path:
        path = self.release / "bin/api"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_elf64() if content is None else content)
        path.chmod(0o755)
        return path

    def validate(self, manifest=None, *, platform=DEBIAN_PLATFORM) -> None:
        validate_prebuilt_runtime(
            self.manifest() if manifest is None else manifest,
            str(self.release),
            platform=platform,
        )

    def test_static_components_need_no_executable_runtime(self):
        manifest = self.manifest(component_type="static")
        self.validate(manifest)

    def test_static_executable_and_static_pie_are_accepted(self):
        self.executable(_elf64(elf_type=2))
        self.validate()

        self.executable(_elf64(elf_type=3, dynamic="static"))
        self.validate()

    def test_dynamic_interpreter_and_needed_library_are_rejected(self):
        for content, message in (
            (_elf64(interpreter=True), "interpreter"),
            (_elf64(dynamic="needed"), "shared ELF library"),
        ):
            with self.subTest(message=message):
                self.executable(content)
                with self.assertRaisesRegex(ValueError, message):
                    self.validate()

    def test_wrong_architecture_and_platform_are_rejected(self):
        self.executable(_elf64(machine=183))
        with self.assertRaisesRegex(ValueError, "x86_64"):
            self.validate()

        self.executable()
        for platform in (
            {"os": "linux", "architecture": "aarch64", "baseline": "debian-13"},
            {"os": "linux", "architecture": "x86_64", "baseline": "debian-12"},
            {"os": "windows", "architecture": "x86_64", "baseline": "debian-13"},
        ):
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, "only linux/x86_64/debian-13"):
                self.validate(platform=platform)

    def test_linux_compatible_osabi_values_are_accepted(self):
        for osabi in (0, 3):
            with self.subTest(osabi=osabi):
                self.executable(_elf64(osabi=osabi))
                self.validate()

    def test_other_osabi_and_nonzero_abi_version_are_rejected(self):
        for content, message in (
            (_elf64(osabi=9), "unsupported ELF OS ABI"),
            (_elf64(abi_version=1), "unsupported ELF ABI version"),
        ):
            with self.subTest(message=message):
                self.executable(content)
                with self.assertRaisesRegex(ValueError, message):
                    self.validate()

    def test_godot_runtime_is_explicitly_deferred(self):
        manifest = self.manifest(component_type="godot-web")
        with self.assertRaisesRegex(ValueError, "Godot and other runtime adapters are deferred"):
            self.validate(manifest)

    def test_truncated_and_oversized_elf_metadata_are_rejected(self):
        invalid = (
            bytes(63),
            _elf64(version=2),
            _elf64(truncated_table=True),
            _elf64(program_count=1025),
            _elf64(dynamic="oversized"),
            _elf64(dynamic="unterminated"),
        )
        for content in invalid:
            with self.subTest(size=len(content)):
                self.executable(content)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_missing_nonexecutable_symlink_hardlink_and_special_files_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Cannot safely inspect"):
            self.validate()

        executable = self.executable()
        executable.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "not executable"):
            self.validate()

        executable.unlink()
        target = self.root / "outside"
        target.write_bytes(_elf64())
        target.chmod(0o755)
        executable.symlink_to(target)
        with self.assertRaises(ValueError):
            self.validate()

        executable.unlink()
        os.link(target, executable)
        with self.assertRaisesRegex(ValueError, "unlinked"):
            self.validate()

        executable.unlink()
        os.mkfifo(executable)
        with self.assertRaises(ValueError):
            self.validate()

    def test_parent_symlink_is_not_traversed(self):
        other = self.root / "other"
        (other / "bin").mkdir(parents=True)
        (other / "bin/api").write_bytes(_elf64())
        (other / "bin/api").chmod(0o755)
        (self.release / "bin").symlink_to(other / "bin", target_is_directory=True)
        with self.assertRaises(ValueError):
            self.validate()

    def test_legacy_binary_path_is_normalized_and_must_be_exported(self):
        self.executable()
        self.validate(self.manifest(binary="./bin/./api"))

        manifest = self.manifest(binary="./bin/api", artifacts=["assets"])
        with self.assertRaisesRegex(ValueError, "not covered by manifest artifacts"):
            self.validate(manifest)

    def test_exec_only_service_accepts_explicit_release_path_and_artifacts(self):
        self.executable()
        manifest = self.manifest(
            exec_command="{{release_dir}}/bin/api --port {{port}}",
            artifacts=["bin/api", "assets"],
        )
        self.validate(manifest)

        missing_exports = self.manifest(exec_command="{{release_dir}}/bin/api")
        with self.assertRaisesRegex(ValueError, "explicit artifacts"):
            self.validate(missing_exports)

    def test_exec_interpreters_and_ambiguous_executable_paths_are_rejected(self):
        self.executable()
        commands = (
            "node {{release_dir}}/bin/api",
            "python {{release_dir}}/bin/api",
            "/bin/sh {{release_dir}}/bin/api",
            "/opt/app/bin/api",
            "{{binary}}",
            "{{release_dir}}/../outside",
            "{{release_dir}}/bin/{{binary}}",
        )
        for command in commands:
            with self.subTest(command=command):
                manifest = self.manifest(exec_command=command, artifacts=["bin/api"])
                with self.assertRaises(ValueError):
                    self.validate(manifest)

        with self.assertRaisesRegex(ValueError, "Interpreter and shell"):
            self.validate(self.manifest(binary="bin/python3"))

    def test_execstart_executable_token_must_remain_literal(self):
        self.executable()
        commands = (
            "{{release_dir}}/bin/%n",
            "{{release_dir}}/bin/$SERVICE",
            "'{{release_dir}}/bin/api'",
            '"{{release_dir}}/bin/api"',
            r"{{release_dir}}/bin/ser\ver",
        )
        for command in commands:
            with self.subTest(command=command):
                manifest = self.manifest(exec_command=command, artifacts=["bin/api"])
                with self.assertRaises(ValueError):
                    self.validate(manifest)

    def test_binary_and_resolved_release_paths_cannot_transform_in_execstart(self):
        for binary in (
            "bin/%n", "bin/$SERVICE", "bin/'api'", 'bin/"api"',
            "bin/server name", "{{release_dir}}/bin/api",
        ):
            with self.subTest(binary=binary):
                manifest = self.manifest(binary=binary)
                with self.assertRaisesRegex(ValueError, "literal|systemd specifiers"):
                    self.validate(manifest)

        manifest = self.manifest()
        unusual_release = str(self.release.with_name("release%n"))
        with self.assertRaisesRegex(ValueError, "systemd specifiers"):
            validate_prebuilt_runtime(manifest, unusual_release, platform=DEBIAN_PLATFORM)

    def test_file_replacement_during_metadata_reads_is_detected(self):
        executable = self.executable()
        original_pread = os.pread
        calls = 0

        def replace_after_header(descriptor: int, size: int, offset: int) -> bytes:
            nonlocal calls
            result = original_pread(descriptor, size, offset)
            calls += 1
            if calls == 2:
                replacement = executable.with_name("replacement")
                replacement.write_bytes(_elf64(elf_type=3))
                replacement.chmod(0o755)
                replacement.replace(executable)
            return result

        with patch.object(build_runtime.os, "pread", side_effect=replace_after_header):
            with self.assertRaisesRegex(ValueError, "changed during runtime validation"):
                self.validate()


if __name__ == "__main__":
    unittest.main()

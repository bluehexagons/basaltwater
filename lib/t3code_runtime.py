"""Compatibility helpers for upstream T3 Code service runtime state."""

from __future__ import annotations

import json
import os
import re

from lib.validation import validate_filesystem_path


# Protocols 2 and 3 have the activeVersion state field and runtime layouts
# understood below. Later protocol numbers may keep those fields and layouts;
# active runtime selection checks both before returning an executable.
T3_MINIMUM_ACTIVE_SERVICE_PROTOCOL = 2

# State-changing and destructive maintenance remains restricted to protocols
# whose complete state semantics have been reviewed here.
T3_KNOWN_SERVICE_PROTOCOLS = frozenset((2, 3))

T3_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

T3_VERSION_EXECUTABLES = (
    ("t3",),
    ("node_modules", "t3", "dist", "bin.mjs"),
)


def has_t3_active_runtime_contract(value: object) -> bool:
    """Return whether a protocol can use the validated active-runtime fields."""

    return type(value) is int and value >= T3_MINIMUM_ACTIVE_SERVICE_PROTOCOL


def is_known_t3_service_protocol(value: object) -> bool:
    """Return whether the complete service-state semantics are known."""

    return type(value) is int and value in T3_KNOWN_SERVICE_PROTOCOLS


def is_t3_service_version(value: object) -> bool:
    """Return whether ``value`` is a valid SemVer version string."""

    return isinstance(value, str) and T3_VERSION_PATTERN.fullmatch(value) is not None


def is_t3_stable_service_version(value: object) -> bool:
    """Return whether ``value`` is a stable SemVer version without metadata."""

    if not isinstance(value, str) or not is_t3_service_version(value):
        return False
    return "-" not in value and "+" not in value


def t3_version_binary(version_root: str) -> str | None:
    """Return the executable in one of the runtime layouts we understand."""

    for relative_path in T3_VERSION_EXECUTABLES:
        binary = os.path.join(version_root, *relative_path)
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return binary
    return None


def t3_version_root(binary: str) -> str | None:
    """Return the version directory for an executable in a known layout."""

    for relative_path in T3_VERSION_EXECUTABLES:
        version_root = binary
        for _ in relative_path:
            version_root = os.path.dirname(version_root)
        expected = os.path.join(version_root, *relative_path)
        if (
            os.path.normpath(binary) == os.path.normpath(expected)
            and os.path.isdir(version_root)
        ):
            return version_root
    return None


def is_t3_standalone_binary(binary: str) -> bool:
    """Return whether the runtime embeds Node rather than using the host Node."""

    root = t3_version_root(binary)
    return root is not None and os.path.normpath(binary) == os.path.join(root, "t3")


def t3_native_probe_command(binary: str | None, node: str | None) -> list[str] | None:
    """Load node-pty using the same Node runtime that runs the selected T3 CLI."""

    if binary is None or (root := t3_version_root(binary)) is None:
        return None
    if is_t3_standalone_binary(binary):
        probe = os.path.join(os.path.dirname(__file__), "t3code_native_probe.cjs")
        validate_filesystem_path(probe, must_exist=True)
        # A SEA cannot run `-e`. Node's preload runs before its embedded entry
        # point and exits after the check, without starting another server.
        # Replace inherited Node options for this child only.
        return [
            "/usr/bin/env",
            f"NODE_OPTIONS=--require={json.dumps(probe, ensure_ascii=False)}",
            binary,
            "--version",
        ]
    if node is None or not os.path.isfile(node) or not os.access(node, os.X_OK):
        return None
    return [
        node,
        "-e",
        "require(process.argv[1])",
        os.path.join(root, "node_modules", "node-pty"),
    ]

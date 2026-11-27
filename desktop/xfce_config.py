"""Targeted offline edits to desktop associations and XFCE settings."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET

from lib.atomic_io import write_text_atomic
from lib.validation import validate_filesystem_path

LEGACY_PM_STUB = """#!/bin/bash
# Stub for pm-is-supported to suppress XFCE warnings in headless/RDP sessions
# Always returns false (1) - no power management available
exit 1
"""

LEGACY_XFSETTINGSD = """[Desktop Entry]
Type=Application
Name=XFCE Settings Daemon
Comment=Settings daemon (display management disabled for RDP)
Hidden=true
"""

LEGACY_DISPLAYS = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="displays" version="1.0">
  <property name="ActiveProfile" type="string" value=""/>
  <property name="Default" type="empty">
    <property name="DP-1" type="string" value="Virtual Display">
      <property name="Active" type="bool" value="true"/>
      <property name="EDID" type="string" value=""/>
      <property name="Resolution" type="string" value="1920x1080"/>
      <property name="RefreshRate" type="double" value="60"/>
      <property name="Rotation" type="int" value="0"/>
      <property name="Reflection" type="string" value="0"/>
      <property name="Primary" type="bool" value="true"/>
      <property name="Position" type="empty">
        <property name="X" type="int" value="0"/>
        <property name="Y" type="int" value="0"/>
      </property>
    </property>
  </property>
</channel>
"""


def _read_settings(path: str) -> str | None:
    validate_filesystem_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"Refusing non-regular desktop settings: {path}")
        return stream.read()


def remove_legacy_override(path: str, expected: str) -> None:
    """Never delete user overrides based on their filename alone."""
    target = Path(path)
    if not target.is_symlink() and target.is_file() and _read_settings(path) == expected:
        target.unlink(missing_ok=True)


def update_browser_defaults(path: str, settings: str) -> None:
    """Update browser keys in one group, preserving other settings and comments."""
    previous = _read_settings(path)
    if previous is None:
        write_text_atomic(path, settings)
        return

    desired = settings.splitlines()
    header = desired.pop(0) if desired[0].startswith("[") else None
    values = dict(line.split("=", 1) for line in desired)
    lines = previous.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    start = 0
    if header:
        for start, line in enumerate(lines, 1):
            if line.strip() == header:
                break
        else:
            lines.append(header + "\n")
            start = len(lines)
    end = next((i for i in range(start, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    pending = values.copy()
    group = []
    for line in lines[start:end]:
        key, separator, _ = line.partition("=")
        key = key.strip()
        if separator and key in values:
            if key in pending:
                group.append(f"{key}={pending.pop(key)}\n")
        else:
            group.append(line)
    group.extend(f"{key}={value}\n" for key, value in pending.items())
    merged = "".join(lines[:start] + group + lines[end:])
    if merged != previous:
        write_text_atomic(path, merged, mode=stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode) & 0o777)


def remove_legacy_pm_stub(path: str = "/usr/local/bin/pm-is-supported") -> None:
    """Remove only the exact stub emitted by older setup versions."""
    for content in (LEGACY_PM_STUB, LEGACY_PM_STUB.replace("headless/RDP sessions", "containers/RDP")):
        remove_legacy_override(path, content)


def _load(path: str, channel: str) -> ET.Element:
    validate_filesystem_path(path)
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"Refusing symlinked XFCE settings: {path}")
    content = _read_settings(path)
    if content is None:
        return ET.Element("channel", name=channel, version="1.0")
    # Malformed settings must not be silently replaced with defaults.
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(content, parser=parser)
    if root.tag != "channel" or root.get("name") != channel:
        raise ValueError(f"Unexpected XFCE channel in {path}")
    return root


def _save(path: str, root: ET.Element) -> None:
    write_text_atomic(path, ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n")


def update_channel(path: str, settings: str) -> None:
    """Merge explicitly managed properties, retaining other user preferences."""
    desired = ET.fromstring(settings)
    root = _load(path, desired.attrib["name"])

    def merge(parent: ET.Element, source: ET.Element) -> None:
        for prop in source:
            existing = next((child for child in parent if child.tag == "property"
                             and child.get("name") == prop.get("name")), None)
            if existing is None:
                parent.append(deepcopy(prop))
            elif len(prop):
                merge(existing, prop)
            else:
                existing.attrib.clear()
                existing.attrib.update(prop.attrib)
                existing[:] = []

    merge(root, desired)
    _save(path, root)


def clean_legacy_xfwm(path: str) -> None:
    """Remove our unsupported nested settings without resetting the WM."""
    root = _load(path, "xfwm4")
    general = root.find("property[@name='general']")
    if general is None:
        return
    legacy = general.find("property[@name='Xfwm']")
    if legacy is None:
        return
    changed = False
    for child in list(legacy):
        if child.attrib in (
            {"name": "Xinerama", "type": "bool", "value": "false"},
            {"name": "theme", "type": "string", "value": "Default-xhdpi"},
        ) and not len(child):
            legacy.remove(child)
            changed = True
    if changed:
        if not len(legacy):
            general.remove(legacy)
        _save(path, root)

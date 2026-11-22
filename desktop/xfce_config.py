"""Targeted offline edits to XFCE settings while the desktop is logged out."""

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

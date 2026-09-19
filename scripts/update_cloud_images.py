#!/usr/bin/env python3
"""Refresh the curated Debian cloud-image catalog in lib/cloud_images.py.

Walks each entry in ``CLOUD_IMAGES``, resolves the latest snapshot directory
from cloud.debian.org for the matching codename, and rewrites the dict literal
with concrete ``snapshot``, ``url``, and ``sha512`` values.

Also surfaces any other version-pinned URLs in the repo so we don't quietly
drift on third-party downloads (e.g. browsh release deb). Those URLs aren't
auto-updated — pinning policy is bespoke per dependency — they're just listed
for review.

Usage::

    python3 scripts/update_cloud_images.py            # update + report
    python3 scripts/update_cloud_images.py --dry-run  # report only
    python3 scripts/update_cloud_images.py --pin-snapshot  # pin to latest snapshot id
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_FILE = REPO_ROOT / "lib" / "cloud_images.py"
BEGIN_MARKER = "# BEGIN CLOUD_IMAGES"
END_MARKER = "# END CLOUD_IMAGES"

DEBIAN_CLOUD_BASE = "https://cloud.debian.org/images/cloud"

_SNAPSHOT_RE = re.compile(r"^\d{8}-\d{3,4}/?$")


class _DirIndexParser(HTMLParser):
    """Pulls anchor hrefs out of an Apache-style autoindex page."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def _http_get(url: str, *, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "basaltwater-cloud-images/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_latest_snapshot(codename: str) -> str:
    """Return the most recent snapshot directory for ``codename``."""
    index_url = f"{DEBIAN_CLOUD_BASE}/{codename}/"
    try:
        body = _http_get(index_url).decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Failed to fetch {index_url}: {exc}")
    parser = _DirIndexParser()
    parser.feed(body)
    snapshots = sorted(
        {h.rstrip("/") for h in parser.hrefs if _SNAPSHOT_RE.match(h)}
    )
    if not snapshots:
        raise SystemExit(f"No snapshot directories found at {index_url}")
    return snapshots[-1]


def fetch_sha512(codename: str, snapshot: str, filename: str) -> str:
    """Return the SHA-512 hex for ``filename`` from the snapshot's SHA512SUMS."""
    sums_url = f"{DEBIAN_CLOUD_BASE}/{codename}/{snapshot}/SHA512SUMS"
    body = _http_get(sums_url).decode("utf-8", errors="replace")
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1]
        if name == filename or name == f"./{filename}":
            return digest.lower()
    raise SystemExit(
        f"Could not find SHA512 for {filename!r} in {sums_url}"
    )


def _read_catalog_entries() -> dict[str, dict[str, str]]:
    """Load the existing CLOUD_IMAGES dict from the catalog module."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from lib import cloud_images  # noqa: WPS433
    finally:
        sys.path.pop(0)
    return {key: dict(value) for key, value in cloud_images.CLOUD_IMAGES.items()}


def _format_entry(key: str, entry: dict[str, str]) -> str:
    fields = ["codename", "version", "snapshot", "filename", "url", "sha512"]
    lines = [f"    {json.dumps(key)}: {{"]
    for field in fields:
        value = entry.get(field, "")
        lines.append(f"        {json.dumps(field)}: {json.dumps(value)},")
    lines.append("    },")
    return "\n".join(lines)


def _render_catalog_block(entries: dict[str, dict[str, str]]) -> str:
    body = "\n".join(_format_entry(k, v) for k, v in entries.items())
    return (
        f"{BEGIN_MARKER}\n"
        f"CLOUD_IMAGES: dict[str, CloudImage] = {{\n"
        f"{body}\n"
        f"}}\n"
        f"{END_MARKER}"
    )


def _rewrite_catalog(entries: dict[str, dict[str, str]]) -> None:
    text = CATALOG_FILE.read_text()
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start < 0 or end < 0 or end < start:
        raise SystemExit(
            f"Could not locate {BEGIN_MARKER}/{END_MARKER} block in {CATALOG_FILE}"
        )
    end += len(END_MARKER)
    new_text = text[:start] + _render_catalog_block(entries) + text[end:]
    CATALOG_FILE.write_text(new_text)


def update_catalog(*, pin_snapshot: bool, dry_run: bool) -> dict[str, dict[str, str]]:
    entries = _read_catalog_entries()
    snapshot_cache: dict[str, str] = {}
    for key, entry in entries.items():
        codename = entry["codename"]
        version = entry["version"]
        if pin_snapshot:
            snapshot = snapshot_cache.get(codename) or fetch_latest_snapshot(codename)
            snapshot_cache[codename] = snapshot
            filename = f"debian-{version}-genericcloud-amd64-{snapshot}.qcow2"
            url = f"{DEBIAN_CLOUD_BASE}/{codename}/{snapshot}/{filename}"
            sha512 = fetch_sha512(codename, snapshot, filename)
        else:
            snapshot = "latest"
            filename = f"debian-{version}-genericcloud-amd64.qcow2"
            url = f"{DEBIAN_CLOUD_BASE}/{codename}/latest/{filename}"
            sha512 = ""
        entry["snapshot"] = snapshot
        entry["filename"] = filename
        entry["url"] = url
        entry["sha512"] = sha512
        print(f"  {key}: {codename}/{snapshot} -> {filename}")
    if not dry_run:
        _rewrite_catalog(entries)
        print(f"Wrote {CATALOG_FILE.relative_to(REPO_ROOT)}")
    else:
        print("(dry-run, catalog not modified)")
    return entries


_VERSION_URL_RE = re.compile(
    r'https?://[^\s"\']+/'
    r'(?:v?\d+\.\d+(?:\.\d+)?)'
    r'/[^\s"\']*\.(?:deb|tar(?:\.[a-z]+)?|zst|qcow2|iso|tgz)'
)
_SCAN_DIRS = ("desktop", "deploy", "game", "lib", "scripts", "service_tools",
              "shared", "smb", "steps", "sync", "web")


def scan_pinned_urls() -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for sub in _SCAN_DIRS:
        root = REPO_ROOT / sub
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            if path.suffix not in (".py", ".sh"):
                continue
            if "__pycache__" in path.parts or ".git" in path.parts:
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for match in _VERSION_URL_RE.finditer(line):
                    findings.append((path, lineno, match.group(0)))
    return findings


def report_pinned_urls(findings: Iterable[tuple[Path, int, str]]) -> None:
    findings = list(findings)
    if not findings:
        print("No other version-pinned URLs detected.")
        return
    print("Other version-pinned URLs to review:")
    for path, lineno, url in findings:
        print(f"  {path.relative_to(REPO_ROOT)}:{lineno}  {url}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without rewriting the catalog")
    parser.add_argument("--pin-snapshot", action="store_true",
                        help="Pin entries to the latest snapshot directory and record SHA512")
    args = parser.parse_args(argv)

    print("Updating Debian cloud-image catalog...")
    update_catalog(pin_snapshot=args.pin_snapshot, dry_run=args.dry_run)
    print()
    report_pinned_urls(scan_pinned_urls())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

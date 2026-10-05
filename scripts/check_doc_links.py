#!/usr/bin/env python3
"""Check local Markdown links in operator docs and shipped skills, without network I/O."""

from __future__ import annotations

import html
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_DOC_URL = "https://github.com/bluehexagons/basaltwater/blob/main/"
_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[\"'][^\n]*?[\"'])?\s*\)")
_REFERENCE = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))")


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def _prose(text: str) -> str:
    """Hide fenced examples and comments while preserving source line numbers."""
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            lines.append(_blank(line))
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = None
        elif marker:
            fence = marker[1]
            lines.append(_blank(line))
        else:
            lines.append(line)
    return re.sub(r"<!--.*?-->", lambda match: _blank(match[0]), "".join(lines), flags=re.S)


def _anchors(text: str) -> set[str]:
    text = _prose(text)
    anchors: set[str] = set()
    for match in re.finditer(r"(?m)^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", text):
        heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", match[1])
        heading = html.unescape(re.sub(r"<[^>]+>", "", heading))
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        candidate = slug
        suffix = 0
        while candidate in anchors:
            suffix += 1
            candidate = f"{slug}-{suffix}"
        anchors.add(candidate)
    anchors.update(re.findall(r"\b(?:id|name)=[\"']([^\"']+)[\"']", text))
    return anchors


def documentation_paths(repository: Path) -> list[Path]:
    """Include root guides, all documentation, and every bundled skill reference."""
    return sorted({
        *repository.glob("*.md"),
        *(repository / "docs").rglob("*.md"),
        *(repository / "common" / "agent_skills").rglob("*.md"),
    })


def check_links(repository: Path) -> list[str]:
    """Return source locations for broken files or heading anchors in local links."""
    repository = repository.resolve()
    errors: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    for source in documentation_paths(repository):
        text = _prose(source.read_text(encoding="utf-8"))
        text = re.sub(r"(`+).*?\1", lambda match: _blank(match[0]), text, flags=re.S)
        for pattern in (_LINK, _REFERENCE):
            for match in pattern.finditer(text):
                target = match[1] or match[2]
                base = source.parent
                if target.startswith(REPOSITORY_DOC_URL):
                    target = target[len(REPOSITORY_DOC_URL):]
                    base = repository
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
                    continue  # External URLs and illustrative absolute artifact paths.
                destination = (base / unquote(parsed.path)).resolve() if parsed.path else source
                line_number = text.count("\n", 0, match.start()) + 1
                location = f"{source.relative_to(repository)}:{line_number}"
                if not destination.is_relative_to(repository) or not destination.exists():
                    errors.append(f"{location}: missing local path: {match[1] or match[2]}")
                elif parsed.fragment and destination.is_file() and destination.suffix.lower() in {".md", ".html"}:
                    if destination not in anchor_cache:
                        anchor_cache[destination] = _anchors(destination.read_text(encoding="utf-8"))
                    if unquote(parsed.fragment) not in anchor_cache[destination]:
                        errors.append(f"{location}: missing heading or anchor: {match[1] or match[2]}")
    return errors


def main() -> int:
    errors = check_links(ROOT)
    if errors:
        print("Documentation link check failed:\n" + "\n".join(errors))
        return 1
    print(f"Documentation link check passed ({len(documentation_paths(ROOT))} Markdown files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

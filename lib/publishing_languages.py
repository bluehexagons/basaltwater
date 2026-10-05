"""Project communication languages; provider locale mappings remain explicit."""

from __future__ import annotations

import re

from lib.validation import validate_no_control_characters


def language_tag(value: object) -> str:
    """Normalize ordinary BCP 47 tags without guessing provider locales."""
    if not isinstance(value, str) or len(value) > 63:
        raise ValueError("Language must be a tag such as en, es, or es-MX")
    validate_no_control_characters(value, "language")
    if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*", value):
        raise ValueError("Invalid language tag")
    parts = value.split("-")
    return "-".join([parts[0].lower(), *(
        part.title() if len(part) == 4 and part.isalpha() else
        part.upper() if len(part) == 2 and part.isalpha() else part.lower()
        for part in parts[1:]
    )])


def parse_publishing(value: object) -> dict:
    """Validate the additive version-1 manifest publishing declaration."""
    if not isinstance(value, dict) or set(value) - {"languages"}:
        raise ValueError("publishing accepts only a languages object")
    languages = value.get("languages", {})
    if not isinstance(languages, dict) or set(languages) - {"source", "supported"}:
        raise ValueError("publishing.languages accepts source and supported")
    supported = languages.get("supported", ["en"])
    if not isinstance(supported, list) or not 1 <= len(supported) <= 50:
        raise ValueError("publishing.languages.supported requires 1–50 language tags")
    supported = [language_tag(item) for item in supported]
    if len(set(supported)) != len(supported):
        raise ValueError("Duplicate supported language")
    source = language_tag(languages.get("source", "en"))
    if source not in supported:
        raise ValueError("Source language must be supported")
    return {"languages": {"source": source, "supported": supported}}

"""Safe paths for nginx configuration."""

from __future__ import annotations

import posixpath
import re

from lib.validation import validate_filesystem_path


def validate_nginx_path(value: object) -> str:
    """Accept normalized absolute paths that cannot introduce nginx syntax."""
    if not isinstance(value, str):
        raise ValueError("nginx path must be a string")
    validate_filesystem_path(value)
    if (
        len(value) > 4096
        or not re.fullmatch(r"/[A-Za-z0-9/._-]*", value)
        or value.startswith('//')
        or (posixpath.normpath(value) != value.rstrip('/') and value != '/')
    ):
        raise ValueError("nginx path must be absolute, normalized, and contain only safe components")
    return value

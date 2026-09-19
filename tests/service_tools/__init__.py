"""Tests for service tools."""

import os


# ``unittest discover -s tests`` imports this directory as the top-level
# ``service_tools`` package, bypassing tests/__init__.py. Enable test logging
# before any maintenance service module creates its syslog handler.
os.environ.setdefault("BASALTWATER_TEST", "1")

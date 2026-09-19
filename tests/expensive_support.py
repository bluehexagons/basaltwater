"""Helpers for marking tests that should not run with the default suite.

Tests decorated with :func:`expensive` are skipped unless the user explicitly
opts in. This keeps the default ``python3 -m unittest discover -s tests`` run
fast and free of side-effects (network, real Proxmox hosts, etc.) while still
allowing those tests to be exercised on demand.

Categories
----------

Each expensive test belongs to a *category* so that contributors can opt in to
just the slice they need. The default categories are documented in
:data:`KNOWN_CATEGORIES`. Custom categories are also accepted - the test is
gated on ``BASALTWATER_RUN_<CATEGORY>=1`` (case-insensitive) or the global
``BASALTWATER_RUN_EXPENSIVE=1`` flag.

Usage::

    from tests.expensive_support import expensive

    @expensive("live_proxmox", "Talks to a real Proxmox host over SSH")
    class TestRealProxmox(unittest.TestCase):
        ...

To run::

    # Just one category:
    BASALTWATER_RUN_LIVE_PROXMOX=1 python3 -m unittest discover -s tests

    # Everything expensive:
    BASALTWATER_RUN_EXPENSIVE=1 python3 -m unittest discover -s tests

    # Or via the test runner:
    ./run_tests.py --expensive live_proxmox
    ./run_tests.py --expensive all
"""

from __future__ import annotations

import os
import unittest


EXPENSIVE_ENV_VAR = "BASALTWATER_RUN_EXPENSIVE"
CATEGORY_ENV_PREFIX = "BASALTWATER_RUN_"

# Categories used in this repo. Custom categories work too, but listing the
# common ones here keeps `run_tests.py --list-categories` and documentation in
# sync with what's actually decorated in the test tree.
KNOWN_CATEGORIES: dict[str, str] = {
    "live_proxmox": "Talks to a real Proxmox host (creates/destroys real VMs or LXCs)",
    "network": "Requires outbound network access (downloads, DNS, etc.)",
    "slow": "Long-running tests that aren't useful for the inner dev loop",
}

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def expensive_tests_enabled() -> bool:
    """Return True if the global expensive opt-in flag is set."""
    return _truthy(os.environ.get(EXPENSIVE_ENV_VAR))


def category_env_var(category: str) -> str:
    """Return the env var name for a given category."""
    return f"{CATEGORY_ENV_PREFIX}{category.upper()}"


def category_enabled(category: str) -> bool:
    """Return True if the given expensive-test category should run.

    A category is enabled when either:
      * ``BASALTWATER_RUN_EXPENSIVE`` is truthy (runs everything), or
      * ``BASALTWATER_RUN_<CATEGORY>`` is truthy.
    """
    if expensive_tests_enabled():
        return True
    return _truthy(os.environ.get(category_env_var(category)))


def expensive(category: str = "general", reason: str | None = None):
    """Skip the wrapped test/class unless its category is enabled.

    Args:
        category: Logical group this test belongs to (see ``KNOWN_CATEGORIES``).
            Custom names are accepted; they map to ``BASALTWATER_RUN_<NAME>``.
        reason: Optional human description; appears in the skip message.
    """
    env = category_env_var(category)
    msg = reason or f"expensive test ({category})"
    return unittest.skipUnless(
        category_enabled(category),
        f"{msg} (set {env}=1 or {EXPENSIVE_ENV_VAR}=1 to run)",
    )


# Backwards-compatible alias: old call sites used `@expensive("reason text")`
# with a single positional reason. New call sites should prefer
# `@expensive("category", "reason")`. Both forms still work.
__all__ = [
    "EXPENSIVE_ENV_VAR",
    "CATEGORY_ENV_PREFIX",
    "KNOWN_CATEGORIES",
    "expensive_tests_enabled",
    "category_env_var",
    "category_enabled",
    "expensive",
]

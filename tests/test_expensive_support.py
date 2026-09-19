"""Tests for tests/expensive_support.py: ensure the gate honours env flags."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.expensive_support import (
    EXPENSIVE_ENV_VAR,
    KNOWN_CATEGORIES,
    category_enabled,
    category_env_var,
    expensive,
    expensive_tests_enabled,
)


class TestExpensiveSupport(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with _env_unset(EXPENSIVE_ENV_VAR):
            self.assertFalse(expensive_tests_enabled())

    def test_enabled_when_truthy(self) -> None:
        for value in ("1", "true", "yes", "on", "TRUE", "On"):
            with _env_set(EXPENSIVE_ENV_VAR, value):
                self.assertTrue(expensive_tests_enabled(), f"value={value!r}")

    def test_disabled_when_falsy(self) -> None:
        for value in ("0", "false", "no", "off", ""):
            with _env_set(EXPENSIVE_ENV_VAR, value):
                self.assertFalse(expensive_tests_enabled(), f"value={value!r}")


class TestCategoryGating(unittest.TestCase):
    def test_known_categories_present(self) -> None:
        # Sanity: documented categories should at least include live_proxmox,
        # which is what the proxmox lifecycle test uses.
        self.assertIn("live_proxmox", KNOWN_CATEGORIES)

    def test_category_env_var(self) -> None:
        self.assertEqual(category_env_var("live_proxmox"),
                         "BASALTWATER_RUN_LIVE_PROXMOX")
        self.assertEqual(category_env_var("Slow"),
                         "BASALTWATER_RUN_SLOW")

    def test_category_disabled_by_default(self) -> None:
        with _env_unset(EXPENSIVE_ENV_VAR), \
             _env_unset("BASALTWATER_RUN_LIVE_PROXMOX"):
            self.assertFalse(category_enabled("live_proxmox"))

    def test_category_specific_flag_enables_just_that_category(self) -> None:
        with _env_unset(EXPENSIVE_ENV_VAR), \
             _env_set("BASALTWATER_RUN_LIVE_PROXMOX", "1"), \
             _env_unset("BASALTWATER_RUN_SLOW"):
            self.assertTrue(category_enabled("live_proxmox"))
            self.assertFalse(category_enabled("slow"))

    def test_global_flag_enables_all_categories(self) -> None:
        with _env_set(EXPENSIVE_ENV_VAR, "1"), \
             _env_unset("BASALTWATER_RUN_LIVE_PROXMOX"), \
             _env_unset("BASALTWATER_RUN_SLOW"), \
             _env_unset("BASALTWATER_RUN_NEWCATEGORY"):
            self.assertTrue(category_enabled("live_proxmox"))
            self.assertTrue(category_enabled("slow"))
            self.assertTrue(category_enabled("newcategory"))

    def test_expensive_decorator_skips_when_disabled(self) -> None:
        with _env_unset(EXPENSIVE_ENV_VAR), \
             _env_unset("BASALTWATER_RUN_DEMO"):

            @expensive("demo", "demo reason")
            class _Inner(unittest.TestCase):
                def test_thing(self) -> None:
                    self.fail("should never run")

            test = _Inner("test_thing")
            result = unittest.TestResult()
            test.run(result)
            self.assertEqual(len(result.skipped), 1)
            self.assertEqual(len(result.failures), 0)
            _, reason = result.skipped[0]
            self.assertIn("BASALTWATER_RUN_DEMO=1", reason)

    def test_expensive_decorator_runs_when_category_enabled(self) -> None:
        with _env_unset(EXPENSIVE_ENV_VAR), \
             _env_set("BASALTWATER_RUN_DEMO", "1"):

            ran = []

            @expensive("demo", "demo reason")
            class _Inner(unittest.TestCase):
                def test_thing(self) -> None:
                    ran.append(True)

            test = _Inner("test_thing")
            result = unittest.TestResult()
            test.run(result)
            self.assertEqual(len(result.skipped), 0)
            self.assertEqual(len(result.failures), 0)
            self.assertEqual(ran, [True])


class _env_set:
    def __init__(self, key: str, value: str) -> None:
        self.key = key
        self.value = value
        self._previous: object = None

    def __enter__(self) -> None:
        self._previous = os.environ.get(self.key)
        os.environ[self.key] = self.value

    def __exit__(self, *exc) -> None:
        if self._previous is None:
            os.environ.pop(self.key, None)
        else:
            os.environ[self.key] = str(self._previous)


class _env_unset:
    def __init__(self, key: str) -> None:
        self.key = key
        self._previous: object = None

    def __enter__(self) -> None:
        self._previous = os.environ.get(self.key)
        os.environ.pop(self.key, None)

    def __exit__(self, *exc) -> None:
        if self._previous is not None:
            os.environ[self.key] = str(self._previous)


if __name__ == "__main__":
    unittest.main()

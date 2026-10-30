"""Tests for the repository test runner."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import run_tests


class TestRunTestsSuites(unittest.TestCase):
    def test_build_named_suite(self) -> None:
        loader = unittest.TestLoader()
        suite = run_tests._build_suite(loader, [], ["smoke"])
        self.assertGreater(suite.countTestCases(), 0)

    def test_combined_suites_do_not_duplicate_test_cases(self) -> None:
        loader = unittest.TestLoader()
        suite = run_tests._build_suite(loader, [], ["smoke", "security"])
        test_ids = [test.id() for test in run_tests._iter_test_cases(suite)]
        self.assertEqual(len(test_ids), len(set(test_ids)))

    def test_domain_suites_cover_all_discovered_modules(self) -> None:
        test_root = run_tests._REPO_ROOT / "tests"
        discovered = {
            ".".join(path.relative_to(run_tests._REPO_ROOT).with_suffix("").parts)
            for path in test_root.rglob("test_*.py")
            if path.is_file()
        }
        grouped = set().union(
            *(run_tests.TEST_SUITES[name] for name in run_tests.TEST_SUITE_PATTERNS)
        )
        self.assertEqual(discovered, grouped)

    def test_list_suites_output(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = run_tests.main(["--list-suites"])
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("smoke", output)
        self.assertIn("proxmox", output)
        self.assertIn("module(s)", output)
        self.assertNotIn("tests.test_", output)


class TestRunTestsExpensivePrereqs(unittest.TestCase):
    def test_all_expands_to_known_prereq_categories(self) -> None:
        categories = run_tests._requested_prereq_categories(["all"])
        self.assertIn("live_proxmox", categories)

    def test_live_proxmox_prereq_check_reports_missing_env(self) -> None:
        stdout = io.StringIO()
        with _env_unset("PROXMOX_TEST_HOST"), \
             _env_unset("PROXMOX_TEST_IP"), \
             contextlib.redirect_stdout(stdout):
            rc = run_tests.main(["--check-prereqs", "--expensive", "live_proxmox"])
        self.assertEqual(rc, 2)
        output = stdout.getvalue()
        self.assertIn("live_proxmox", output)
        self.assertIn("PROXMOX_TEST_HOST", output)
        self.assertIn("PROXMOX_TEST_IP", output)

    def test_live_proxmox_lxc_prereq_check_requires_template(self) -> None:
        stdout = io.StringIO()
        with patch.dict(os.environ, {"PROXMOX_TEST_GUEST_TYPE": "lxc"}), \
             _env_unset("PROXMOX_TEST_HOST"), \
             _env_unset("PROXMOX_TEST_TEMPLATE"), \
             contextlib.redirect_stdout(stdout):
            rc = run_tests.main(["--check-prereqs", "--expensive", "live_proxmox"])
        self.assertEqual(rc, 2)
        output = stdout.getvalue()
        self.assertIn("live_proxmox", output)
        self.assertIn("PROXMOX_TEST_HOST", output)
        self.assertIn("PROXMOX_TEST_TEMPLATE", output)


class TestRunTestsDurations(unittest.TestCase):
    def test_duration_output_can_be_requested(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = run_tests.main(["--durations", "1", "test_expensive_support"])
        self.assertEqual(rc, 0)
        self.assertIn("Slowest 1 tests:", stdout.getvalue())
        self.assertIn("Slowest 1 modules:", stdout.getvalue())
        self.assertIn("Total test time:", stdout.getvalue())

    def test_module_totals_rank_accumulated_cost_without_hiding_slow_tests(self) -> None:
        result = run_tests.TimedTextTestResult(io.StringIO(), True, 0)
        result.test_durations = [
            (3.0, "tests.single.Contract.test_one"),
            (2.0, "tests.repeated.First.test_one"),
            (2.0, "tests.repeated.Second.test_two"),
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            run_tests._print_durations(result, 1)
        output = stdout.getvalue()
        self.assertIn("Total test time: 7.000s", output)
        self.assertIn("4.000s      2 tests  tests.repeated", output)
        self.assertIn("3.000s  tests.single.Contract.test_one", output)
        self.assertNotIn("tests.repeated.First.test_one", output)


class TestRunTestsSelectors(unittest.TestCase):
    def test_file_stems_and_extensions_are_case_insensitive(self) -> None:
        loader = unittest.TestLoader()
        expected = run_tests._resolve_selector(loader, "test_expensive_support")
        actual = run_tests._resolve_selector(loader, "TEST_EXPENSIVE_SUPPORT.PY")
        self.assertEqual(
            [test.id() for test in run_tests._iter_test_cases(actual)],
            [test.id() for test in run_tests._iter_test_cases(expected)],
        )

    def test_windows_file_separators_work_on_linux(self) -> None:
        loader = unittest.TestLoader()
        expected = run_tests._resolve_selector(loader, "tests.test_expensive_support")
        actual = run_tests._resolve_selector(loader, "tests\\test_expensive_support.py")
        self.assertEqual(
            [test.id() for test in run_tests._iter_test_cases(actual)],
            [test.id() for test in run_tests._iter_test_cases(expected)],
        )

    def test_missing_selector_is_a_usage_error_without_a_test_run(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = run_tests.main(["test_selector_that_does_not_exist"])
        self.assertEqual(status, 2)
        self.assertIn("Could not resolve test selector", stderr.getvalue())
        self.assertNotIn("All tests passed", stdout.getvalue())

    def test_module_with_no_tests_cannot_report_success(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = run_tests.main(["tests.expensive_support"])
        self.assertEqual(status, 2)
        self.assertIn("no test cases", stderr.getvalue())
        self.assertNotIn("All tests passed", stdout.getvalue())

    def test_empty_discovery_or_named_suite_cannot_report_success(self) -> None:
        for arguments in ([], ["--suite", "all"]):
            with self.subTest(arguments=arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    patch.object(run_tests, "_build_suite", return_value=unittest.TestSuite()),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    status = run_tests.main(arguments)
                self.assertEqual(status, 2)
                self.assertIn("No tests selected", stderr.getvalue())
                self.assertNotIn("All tests passed", stdout.getvalue())

    def test_failed_lookup_does_not_poison_a_later_valid_selection(self) -> None:
        loader = unittest.TestLoader()
        with self.assertRaises(RuntimeError):
            run_tests._resolve_selector(loader, "test_selector_that_does_not_exist")
        self.assertEqual(loader.errors, [])
        suite = run_tests._resolve_selector(loader, "tests.test_expensive_support.TestCategoryGating.test_category_env_var")
        self.assertEqual(suite.countTestCases(), 1)


class TestRunTestsOutput(unittest.TestCase):
    def test_failure_report_does_not_include_captured_output_by_default(self) -> None:
        class NoisyFailure(unittest.TestCase):
            def test_failure(self) -> None:
                print("large task log that should stay captured")
                self.fail("expected failure")

        stdout = io.StringIO()
        with patch.object(
            run_tests,
            "_build_suite",
            return_value=unittest.TestSuite([NoisyFailure("test_failure")]),
        ), contextlib.redirect_stdout(stdout):
            rc = run_tests.main([])

        self.assertEqual(rc, 1)
        self.assertIn("expected failure", stdout.getvalue())
        self.assertNotIn("large task log", stdout.getvalue())

    def test_requested_output_keeps_recent_unicode_diagnostics_with_a_bound(self) -> None:
        class NoisyFailure(unittest.TestCase):
            def test_failure(self) -> None:
                print("old diagnostic")
                print("\U0001f30a" * (256 * 1024))
                sys.stderr.write("recent diagnostic\n")
                self.fail("expected failure")

        stdout = io.StringIO()
        with patch.object(
            run_tests, "_build_suite",
            return_value=unittest.TestSuite([NoisyFailure("test_failure")]),
        ), contextlib.redirect_stdout(stdout):
            rc = run_tests.main(["--show-output"])

        output = stdout.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("expected failure", output)
        self.assertIn("Earlier test output omitted", output)
        self.assertIn("recent diagnostic", output)
        self.assertNotIn("old diagnostic", output)
        self.assertLess(len(output), 256 * 1024 + 4096)

    def test_requested_small_output_remains_complete(self) -> None:
        class Failure(unittest.TestCase):
            def test_failure(self) -> None:
                print("first diagnostic")
                sys.stderr.write("last diagnostic\n")
                self.fail("expected failure")

        stdout = io.StringIO()
        with patch.object(
            run_tests, "_build_suite",
            return_value=unittest.TestSuite([Failure("test_failure")]),
        ), contextlib.redirect_stdout(stdout):
            rc = run_tests.main(["--show-output"])

        self.assertEqual(rc, 1)
        self.assertIn("first diagnostic\nlast diagnostic\n", stdout.getvalue())
        self.assertNotIn("Earlier test output omitted", stdout.getvalue())


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

"""Tests for the repository test runner."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import run_tests
from scripts import check_development, test_inventory


class TestDevelopmentChecks(unittest.TestCase):
    def test_compile_excludes_payloads_but_rejects_maintained_syntax_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for folder in ("cachyos-t3/releases/app", "lib/node_modules/package",
                           "build", "artifacts", "environment"):
                path = root / folder
                path.mkdir(parents=True)
                (path / "legacy.py").write_text('print "Python 2"\n')
            (root / "environment/pyvenv.cfg").write_text("home = /example\n")
            (root / "lib/maintained.py").write_text("value = 1\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(check_development.compile_sources(root), 0)
                self.assertIn("Compiled 1 maintained", output.getvalue())
                (root / "lib/maintained.py").write_text("invalid syntax !\n")
                self.assertEqual(check_development.compile_sources(root), 1)
                self.assertIn("maintained.py", output.getvalue())

    @unittest.skipIf(os.name == "nt", "Symlink privileges vary on Windows")
    def test_compile_does_not_follow_external_source_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "checkout"
            root.mkdir()
            external = parent / "external"
            external.mkdir()
            (external / "bad.py").write_text("invalid syntax !\n")
            (root / "linked").symlink_to(external, target_is_directory=True)
            (root / "linked.py").symlink_to(external / "bad.py")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_development.compile_sources(root), 0)
            self.assertFalse((external / "__pycache__").exists())

    def test_dependency_check_reports_missing_modules_and_recovery(self) -> None:
        for missing in (True, False):
            with self.subTest(missing=missing):
                output = io.StringIO()
                with (patch.object(check_development.importlib.util, "find_spec",
                                   side_effect=lambda name: None if missing and name == "aiohttp" else object()),
                      contextlib.redirect_stdout(output), contextlib.redirect_stderr(output)):
                    self.assertEqual(check_development.main(["dependencies"]), int(missing))
                self.assertIn(sys.executable, output.getvalue())
                if missing:
                    self.assertIn("Missing development modules: aiohttp", output.getvalue())
                    self.assertIn(".[dev,completion]", output.getvalue())


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
        errors = test_inventory.selection_errors(
            run_tests._REPO_ROOT, run_tests.TEST_SUITE_PATTERNS, run_tests.TEST_SUITES,
        )
        self.assertEqual(errors, [])

    def test_inventory_reports_unowned_modules_and_stale_selections_without_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests/nested").mkdir(parents=True)
            (root / "tests/test_owned.py").write_text("raise RuntimeError('must not import')\ndef test_one(): pass\n")
            (root / "tests/nested/test_new.py").write_text("async def test_two(): pass\n")
            report = test_inventory.inventory(root, {"core": ("tests/test_owned.py", "tests/test_removed.py")},
                                              {"core": ["tests.test_owned"], "integration": ["tests.test_removed"]})
        self.assertEqual(report["modules"], 2)
        self.assertEqual(report["definitions"], 2)
        self.assertEqual(report["errors"], [
            "Unowned test module: tests.nested.test_new",
            "Stale selector in integration: tests.test_removed",
            "Empty pattern in core: tests/test_removed.py",
        ])

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
    def test_suite_time_includes_class_fixtures_outside_test_case_timings(self) -> None:
        result = run_tests.TimedTextTestResult(io.StringIO(), True, 0)
        case = unittest.FunctionTestCase(lambda: None)
        with patch.object(run_tests.time, "perf_counter", side_effect=[10, 12, 15, 18]):
            result.startTestRun()
            result.startTest(case)
            result.stopTest(case)
            result.stopTestRun()
        self.assertEqual(result.total_duration, 8)
        self.assertEqual(result.test_durations, [(3, case.id())])

    def test_json_timings_include_results_even_on_failure_without_captured_secrets(self) -> None:
        class Sample(unittest.TestCase):
            def test_failure(self):
                print("secret captured output")
                self.fail("secret traceback")

            @unittest.skip("private skip reason")
            def test_skip(self):
                pass

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "timings.json"
            with (patch.object(run_tests, "_build_suite", return_value=unittest.defaultTestLoader.loadTestsFromTestCase(Sample)),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(run_tests.main(["--timings-json", str(path)]), 1)
            report = json.loads(path.read_text())
        self.assertEqual((report["tests_run"], report["skipped"], report["failures"]), (2, 1, 1))
        self.assertFalse(report["successful"])
        self.assertEqual(len(report["tests"]), 2)
        self.assertGreaterEqual(report["suite_seconds"], report["test_case_seconds"])
        self.assertNotIn("secret captured output", json.dumps(report))
        self.assertNotIn("secret traceback", json.dumps(report))
        self.assertNotIn("private skip reason", json.dumps(report))

    def test_unwritable_report_path_is_rejected_before_tests_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (patch.object(run_tests, "_build_suite") as build,
                  contextlib.redirect_stderr(io.StringIO())):
                self.assertEqual(run_tests.main(["--timings-json", str(Path(temporary) / "missing/report.json")]), 2)
            build.assert_not_called()

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

    def test_requested_tail_handles_incremental_writes_then_a_large_replacement(self) -> None:
        output = run_tests._TestOutput(32)
        content = "initial diagnostic" * 3
        output.write(content)
        for character in "new diagnostics " * 5:
            output.write(character)
            content += character
            self.assertEqual(output.getvalue(),
                             f"[Earlier test output omitted: {len(content) - 32} characters]\n" + content[-32:])
        replacement = "replacement diagnostic" * 3
        output.write(replacement)
        content += replacement
        self.assertEqual(output.getvalue(),
                         f"[Earlier test output omitted: {len(content) - 32} characters]\n" + content[-32:])

    def test_discarded_output_does_not_hide_invalid_text_writes(self) -> None:
        class InvalidOutput(unittest.TestCase):
            def test_output(self) -> None:
                sys.stdout.write(b"invalid text output")

        stdout = io.StringIO()
        with patch.object(
            run_tests, "_build_suite",
            return_value=unittest.TestSuite([InvalidOutput("test_output")]),
        ), contextlib.redirect_stdout(stdout):
            rc = run_tests.main([])
        self.assertEqual(rc, 1)
        self.assertIn("TypeError", stdout.getvalue())


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

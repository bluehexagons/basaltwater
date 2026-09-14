#!/usr/bin/env python3
"""Test runner for infra_tools.

By default, expensive tests (live Proxmox round-trips, network downloads, etc.)
are skipped. Opt in with ``--expensive CATEGORY`` (repeatable) or
``--expensive all``.

Examples:
    ./run_tests.py                                  # full default suite, concise output
    ./run_tests.py -v                               # verbose
    ./run_tests.py --suite smoke                    # quick high-value checks
    ./run_tests.py --suite proxmox                  # mocked Proxmox + skipped live test
    ./run_tests.py test_proxmox_manage              # one test module
    ./run_tests.py tests.test_proxmox_manage.TestHealthCheck   # one class
    ./run_tests.py --durations 20                   # show slowest tests
    ./run_tests.py --list-suites                    # show named suites
    ./run_tests.py --list-categories                # show known expensive categories
    ./run_tests.py --check-prereqs --expensive live_proxmox
    PROXMOX_TEST_GUEST_TYPE=vm PROXMOX_TEST_IP=10.0.0.50 \
        ./run_tests.py --expensive live_proxmox tests.test_proxmox_live
    ./run_tests.py --expensive live_proxmox \
        tests.test_proxmox_live                     # run a real Proxmox guest round-trip
    ./run_tests.py --expensive all                  # run everything including expensive

Selectors are matched case-insensitively against test module file names; you
can also pass a fully-qualified ``tests.module.Class.method`` selector and it
will be loaded directly.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import unittest
from io import StringIO
from pathlib import Path

# Make `tests/` importable when running from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))

from tests.expensive_support import (  # noqa: E402  (after sys.path tweak)
    EXPENSIVE_ENV_VAR,
    KNOWN_CATEGORIES,
    category_env_var,
)


TEST_SUITE_PATTERNS: dict[str, tuple[str, ...]] = {
    "agent": (
        "tests/test_agent_*.py",
        "tests/test_browser_automation.py",
        "tests/test_cachyos_*.py",
        "tests/test_device_pairing.py",
        "tests/test_git_credentials.py",
        "tests/test_t3_agent_skills.py",
        "tests/test_t3code_admin_pair.py",
        "tests/test_t3code_steps.py",
        "tests/test_workspace_cli.py",
    ),
    "core": (
        "tests/test_arg_parser_hosted.py",
        "tests/test_atomic_io.py",
        "tests/test_cache.py",
        "tests/test_channel_manager.py",
        "tests/test_config*.py",
        "tests/test_completions.py",
        "tests/test_concurrent_operations.py",
        "tests/test_credentials.py",
        "tests/test_display.py",
        "tests/test_expensive_support.py",
        "tests/test_interactive_shell.py",
        "tests/test_logging_utils.py",
        "tests/test_machine_state.py",
        "tests/test_notifications.py",
        "tests/test_operation_*.py",
        "tests/test_plugin_registry.py",
        "tests/test_progress*.py",
        "tests/test_project_manifest.py",
        "tests/test_remote_utils.py",
        "tests/test_run_tests.py",
        "tests/test_setup_report.py",
        "tests/test_systemd_service.py",
        "tests/test_recall.py",
        "tests/test_sysadmin_cli.py",
        "tests/test_sysadmin_handlers.py",
        "tests/test_sysadmin_user*.py",
        "tests/test_update_policy.py",
        "tests/test_user_rename.py",
        "tests/test_validation.py",
        "tests/test_validators.py",
    ),
    "desktop": (
        "tests/test_av_tools.py",
        "tests/test_browser_steps.py",
        "tests/test_data_analysis_tools.py",
        "tests/test_desktop_accessibility.py",
        "tests/test_desktop_apps.py",
        "tests/test_desktop_session.py",
        "tests/test_desktop_smoke.py",
        "tests/test_firmware.py",
        "tests/test_gl_tools.py",
        "tests/test_go_setup.py",
        "tests/test_godot*.py",
        "tests/test_rdp_validation.py",
        "tests/test_xfce_config.py",
        "tests/test_xrdp*.py",
    ),
    "deployment": (
        "tests/test_cicd*.py",
        "tests/test_common_steps.py",
        "tests/test_deploy*.py",
        "tests/test_install*.py",
        "tests/test_legacy_deployment.py",
        "tests/test_manifest_deploy.py",
        "tests/test_orchestrator_bootstrap.py",
        "tests/test_python_setup.py",
        "tests/test_release_management.py",
        "tests/test_remote_setup.py",
        "tests/test_remote_deploy.py",
        "tests/test_required_setup_failures.py",
        "tests/test_setup_common.py",
        "tests/test_setup_dry_run.py",
        "tests/test_upgrade_safety.py",
        "tests/test_uv_install.py",
        "tests/test_wheel_artifact.py",
    ),
    "network": (
        "tests/test_apt_sources.py",
        "tests/test_cloudflare*.py",
        "tests/test_mdns.py",
        "tests/test_network*.py",
        "tests/test_ssh_enrollment.py",
        "tests/test_ssh_utils.py",
    ),
    "proxmox": (
        "tests/test_arg_parser_hosted.py",
        "tests/test_cloud_images.py",
        "tests/test_cluster_update.py",
        "tests/test_network_proxmox.py",
        "tests/test_node_setup.py",
        "tests/test_provisioning_cache.py",
        "tests/test_proxmox_*.py",
        "tests/test_vm_*.py",
    ),
    "security": (
        "tests/test_access_filters.py",
        "tests/test_agent_security_*.py",
        "tests/test_auth_failure_bans.py",
        "tests/test_firewall_output.py",
        "tests/test_git_samba_boundaries.py",
        "tests/test_samba*.py",
        "tests/test_security_activity.py",
        "tests/test_security_steps.py",
        "tests/test_shell_safety.py",
        "tests/test_smb_mount_hardening.py",
        "tests/test_ssh_*.py",
    ),
    "services": (
        "tests/service_tools/test_*.py",
        "tests/test_codex_auth_maintenance.py",
        "tests/test_github_maintenance.py",
        "tests/test_local_cli.py",
        "tests/test_maintenance_systemd.py",
        "tests/test_kernel_restart.py",
        "tests/test_setup_maintenance.py",
    ),
    "storage": (
        "tests/service_tools/test_check_storage_ops_mounts.py",
        "tests/service_tools/test_scrub_par2.py",
        "tests/service_tools/test_storage_ops.py",
        "tests/service_tools/test_sync_rsync.py",
        "tests/service_tools/test_user_cache_maintenance.py",
        "tests/test_backup_config.py",
        "tests/test_disk_utils.py",
        "tests/test_scrub_par2.py",
        "tests/test_storage*.py",
        "tests/test_mount_utils.py",
        "tests/test_swap*.py",
        "tests/test_syncthing*.py",
    ),
    "web": (
        "tests/test_antistatic_steps.py",
        "tests/test_cloudflare*.py",
        "tests/test_gogs*.py",
        "tests/test_homebox.py",
        "tests/test_infra_web.py",
        "tests/test_manifest_deploy.py",
        "tests/test_nginx_config.py",
        "tests/test_ssl_steps.py",
        "tests/test_static_web_publish.py",
        "tests/test_web*.py",
    ),
}


def _discover_pattern_suite(patterns: tuple[str, ...]) -> list[str]:
    """Return dotted test-module selectors matching the supplied patterns."""
    selectors: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(_REPO_ROOT.glob(pattern)):
            if not path.is_file() or not path.name.startswith("test_"):
                continue
            relative = path.relative_to(_REPO_ROOT).with_suffix("")
            selector = ".".join(relative.parts)
            if selector not in seen:
                selectors.append(selector)
                seen.add(selector)
    return selectors


TEST_SUITES: dict[str, list[str]] = {
    "smoke": [
        "tests.test_config",
        "tests.test_validation",
        "tests.test_proxmox_hosts",
        "tests.test_proxmox_manage",
        "tests.test_proxmox_cli",
        "tests.test_expensive_support",
    ],
    "proxmox": [
        "tests.test_arg_parser_hosted",
        "tests.test_proxmox_node",
        "tests.test_proxmox_hosts",
        "tests.test_proxmox_manage",
        "tests.test_proxmox_shell",
        "tests.test_proxmox_cli",
        "tests.test_proxmox_live",
    ],
    "security": [
        "tests.test_security_steps",
        "tests.test_shell_safety",
        "tests.test_samba_hardening",
        "tests.test_smb_mount_hardening",
        "tests.test_ssh_utils",
        "tests.test_validation",
        "tests.test_validators",
    ],
    "integration": [
        "tests.test_cicd",
        "tests.test_deploy_utils",
        "tests.test_legacy_deployment",
        "tests.test_orchestrator_bootstrap",
        "tests.test_remote_setup",
        "tests.test_remote_utils",
        "tests.test_setup_common",
        "tests.test_proxmox_node",
        "tests.test_upgrade_safety",
    ],
    **{
        name: _discover_pattern_suite(patterns)
        for name, patterns in TEST_SUITE_PATTERNS.items()
    },
    "all": [],
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the infra_tools test suite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "selectors",
        nargs="*",
        help=(
            "Optional test selectors: file stems (test_proxmox_manage), "
            "module dotted paths (tests.test_proxmox_manage), or fully "
            "qualified test ids."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose test output (does not suppress infra_tools console logs).",
    )
    parser.add_argument(
        "--show-output",
        action="store_true",
        help="Include captured test stdout/stderr when a test fails.",
    )
    parser.add_argument(
        "--expensive",
        action="append",
        metavar="CATEGORY",
        default=[],
        help=(
            "Enable an expensive test category. Repeatable. "
            "Use 'all' to enable every expensive test."
        ),
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=sorted(TEST_SUITES),
        metavar="SUITE",
        default=[],
        help=(
            "Run a named suite. Repeatable. "
            f"Choices: {', '.join(sorted(TEST_SUITES))}."
        ),
    )
    parser.add_argument(
        "--durations",
        type=int,
        metavar="N",
        default=0,
        help="Show the N slowest test durations after the run.",
    )
    parser.add_argument(
        "--check-prereqs",
        action="store_true",
        help=(
            "Check prerequisites for requested expensive categories and exit. "
            "Useful before destructive live tests."
        ),
    )
    parser.add_argument(
        "--list-suites", action="store_true",
        help="List named suites and exit.",
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="List the known expensive-test categories and exit.",
    )
    parser.add_argument(
        "--list-tests", action="store_true",
        help="List the discovered test files and exit.",
    )
    return parser


def _list_suites() -> int:
    print("Named test suites:")
    print()
    width = max(len(name) for name in TEST_SUITES) if TEST_SUITES else 0
    for name, selectors in sorted(TEST_SUITES.items()):
        if name == "all":
            print(f"  {name:<{width}}  all discovered tests (expensive tests still gated)")
            continue
        print(f"  {name:<{width}}  {len(selectors)} module(s)")
        for selector in selectors:
            print(f"  {'':<{width}}    {selector}")
    return 0


def _list_categories() -> int:
    print("Known expensive-test categories:")
    print()
    width = max(len(c) for c in KNOWN_CATEGORIES) if KNOWN_CATEGORIES else 0
    for name, desc in sorted(KNOWN_CATEGORIES.items()):
        env = category_env_var(name)
        print(f"  {name:<{width}}  {desc}")
        print(f"  {'':<{width}}    enable: {env}=1  (or --expensive {name})")
    print()
    print(f"Or set {EXPENSIVE_ENV_VAR}=1 (--expensive all) to enable everything.")
    return 0


def _list_tests() -> int:
    test_dir = _REPO_ROOT / "tests"
    print("Discovered test files:")
    for test_file in sorted(test_dir.rglob("test_*.py")):
        rel_path = test_file.relative_to(test_dir)
        print(f"  {rel_path}")
    return 0


def _apply_expensive_flags(categories: list[str]) -> list[str]:
    """Set the env vars that gate expensive tests. Returns the categories enabled."""
    enabled: list[str] = []
    for raw in categories:
        cat = raw.strip().lower()
        if not cat:
            continue
        if cat == "all":
            os.environ[EXPENSIVE_ENV_VAR] = "1"
            enabled.append("all")
            continue
        os.environ[category_env_var(cat)] = "1"
        enabled.append(cat)
    return enabled


def _requested_prereq_categories(expensive: list[str]) -> list[str]:
    categories: list[str] = []
    for raw in expensive:
        cat = raw.strip().lower()
        if not cat:
            continue
        if cat == "all":
            categories.extend(KNOWN_CATEGORIES)
            continue
        categories.append(cat)
    if not categories:
        categories.extend(KNOWN_CATEGORIES)
    return sorted(set(categories))


def _check_prereqs(categories: list[str]) -> int:
    checks = {
        "live_proxmox": _check_live_proxmox_prereqs,
    }
    unknown = [cat for cat in categories if cat not in checks]
    failures: list[str] = []
    print("Prerequisite checks:")
    for category in categories:
        check = checks.get(category)
        if check is None:
            continue
        errors = check()
        if errors:
            print(f"  ✗ {category}")
            for error in errors:
                print(f"    - {error}")
            failures.extend(f"{category}: {error}" for error in errors)
        else:
            print(f"  ✓ {category}")
    for category in unknown:
        print(f"  - {category}: no prerequisite checker registered")
    if failures:
        return 2
    return 0


def _check_live_proxmox_prereqs() -> list[str]:
    from tests.test_proxmox_live import check_live_proxmox_prereqs

    return check_live_proxmox_prereqs()


def _resolve_selector(loader: unittest.TestLoader, selector: str):
    """Resolve a single selector to a test suite.

    Accepted forms:
      * file stem:           "test_proxmox_manage" or "test_proxmox_manage.py"
      * dotted module:       "tests.test_proxmox_manage"
      * dotted test id:      "tests.test_proxmox_manage.TestHealthCheck.test_x"
      * bare class/method:   tries "tests.<selector>" as a fallback
    """
    candidates: list[str] = []
    s = selector
    if s.endswith(".py"):
        s = s[:-3]
    if "/" in s or os.sep in s:
        s = s.replace("/", ".").replace(os.sep, ".")

    if s.startswith("tests."):
        candidates.append(s)
    else:
        # Try direct module under tests/ (file stem like "test_foo").
        test_dir = _REPO_ROOT / "tests"
        matches = list(test_dir.rglob(f"{s}.py"))
        for match in matches:
            rel = match.relative_to(test_dir).with_suffix("")
            candidates.append("tests." + str(rel).replace(os.sep, "."))
        # Also try the selector as-is, in case it's a class.method.
        candidates.append(f"tests.{s}")
        candidates.append(s)

    last_error: Exception | None = None
    for name in candidates:
        try:
            return loader.loadTestsFromName(name)
        except (ImportError, AttributeError) as exc:
            last_error = exc
            continue
    raise RuntimeError(
        f"Could not resolve test selector {selector!r}. Tried: {candidates}. "
        f"Last error: {last_error}"
    )


def _iter_test_cases(test):
    """Yield individual test cases from a possibly nested unittest suite."""
    if isinstance(test, unittest.TestSuite):
        for child in test:
            yield from _iter_test_cases(child)
        return
    yield test


def _build_suite(
    loader: unittest.TestLoader,
    selectors: list[str],
    suites: list[str],
) -> unittest.TestSuite:
    if "all" in suites:
        return loader.discover("tests", pattern="test_*.py")
    if not selectors and not suites:
        return loader.discover("tests", pattern="test_*.py")
    suite = unittest.TestSuite()
    seen_test_ids: set[str] = set()

    def add_selector(selector: str) -> None:
        for test in _iter_test_cases(_resolve_selector(loader, selector)):
            test_id = test.id()
            if test_id in seen_test_ids:
                continue
            suite.addTest(test)
            seen_test_ids.add(test_id)

    for suite_name in suites:
        for selector in TEST_SUITES[suite_name]:
            add_selector(selector)
    for sel in selectors:
        add_selector(sel)
    return suite


class TimedTextTestResult(unittest.TextTestResult):
    """Text test result that records per-test durations."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.test_durations: list[tuple[float, str]] = []
        self._test_started_at = 0.0

    def startTest(self, test) -> None:
        self._test_started_at = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test) -> None:
        elapsed = time.perf_counter() - self._test_started_at
        self.test_durations.append((elapsed, test.id()))
        super().stopTest(test)


def _print_durations(result: unittest.TestResult, count: int) -> None:
    if count <= 0:
        return
    durations = getattr(result, "test_durations", [])
    if not durations:
        return
    print()
    print(f"Slowest {min(count, len(durations))} tests:")
    for elapsed, test_id in sorted(durations, reverse=True)[:count]:
        print(f"  {elapsed:8.3f}s  {test_id}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_suites:
        return _list_suites()
    if args.list_categories:
        return _list_categories()
    if args.list_tests:
        return _list_tests()

    enabled = _apply_expensive_flags(args.expensive)
    if enabled:
        print(f"Expensive categories enabled: {', '.join(enabled)}")

    if args.check_prereqs:
        return _check_prereqs(_requested_prereq_categories(args.expensive))

    if not args.verbose:
        os.environ.setdefault("INFRA_TOOLS_TEST", "1")

    os.chdir(_REPO_ROOT)

    loader = unittest.TestLoader()
    try:
        suite = _build_suite(loader, args.selectors, args.suite)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.verbose:
        runner = unittest.TextTestRunner(verbosity=2, resultclass=TimedTextTestResult)
        result = runner.run(suite)
    else:
        test_output = StringIO()
        result_output = StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = test_output
        sys.stderr = test_output
        try:
            runner = unittest.TextTestRunner(
                stream=result_output,
                verbosity=0,
                resultclass=TimedTextTestResult,
            )
            result = runner.run(suite)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        if not result.wasSuccessful():
            sys.stdout.write(result_output.getvalue())
            if args.show_output and test_output.getvalue():
                sys.stdout.write("\nCaptured test output:\n")
                sys.stdout.write(test_output.getvalue())

    _print_durations(result, args.durations)

    print()
    skipped = len(result.skipped)
    if result.wasSuccessful():
        msg = f"✓ All tests passed ({result.testsRun} run"
        if skipped:
            msg += f", {skipped} skipped"
        msg += ")"
        print(msg)
        return 0
    print(
        f"✗ Tests failed: {len(result.failures)} failures, "
        f"{len(result.errors)} errors, {skipped} skipped"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

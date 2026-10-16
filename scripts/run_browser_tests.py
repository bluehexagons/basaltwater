#!/usr/bin/env python3
"""Run intensive first-party Chromium/Firefox checks on a development machine."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.browser_automation_steps import PLAYWRIGHT_VERSION
from lib.agent_browser import _environment, _runtime
from lib.atomic_io import read_json_file, write_json_atomic
from lib.guacamole_config import RELEASE
from lib.remote_utils import run
from lib.validation import validate_filesystem_path

# all.js extracted from the checksum-verified 1.6.0-bw2 release archive.
GUACAMOLE_JS_SHA256 = "3a6126a9487ea2e37a1684613b54335c2d32e35d2e33286e719700154701f562"


def summarize_results(output: Path, *, engines: list[str], playwright_version: str, context: str,
                      returncode: int, guacamole_available: bool, selection: str | None = None) -> dict:
    """Derive qualification from completed checks, never installed prerequisites."""
    checks = {engine: {"first_party": [], "guacamole": []} for engine in engines}
    versions = {engine: set() for engine in engines}
    issues = []
    try:
        report = read_json_file(str(output / "results.json"), max_bytes=16 * 1024 * 1024)
        if not isinstance(report, dict) or not isinstance(report.get("suites"), list):
            raise ValueError("Playwright report has no suite inventory")
        if report.get("errors"):
            issues.append("playwright_report_errors")
        suites = list(report["suites"])
        while suites:
            suite = suites.pop()
            suites.extend(suite.get("suites", []))
            for spec in suite.get("specs", []):
                boundary = "guacamole" if spec["title"].startswith("Guacamole ") else "first_party"
                for test in spec["tests"]:
                    engine = test.get("projectName")
                    if engine not in checks:
                        continue
                    results = test.get("results", [])
                    status = results[-1].get("status") if results else "not-run"
                    if status == "passed" and (test.get("status") != "expected" or test.get("expectedStatus") != "passed"):
                        status = "failed"
                    checks[engine][boundary].append(status)
                    for result in results:
                        for attachment in result.get("attachments", []):
                            if attachment.get("name") != "browser-context":
                                continue
                            try:
                                details = json.loads(base64.b64decode(attachment["body"], validate=True))
                                version = details.get("browser_version")
                                if (details.get("engine") == engine and details.get("playwright_version") == playwright_version
                                        and isinstance(version, str) and version):
                                    versions[engine].add(version)
                            except (KeyError, ValueError, AttributeError):
                                continue
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        issues.append("playwright_report_unavailable_or_invalid")

    def outcome(statuses: list[str]) -> dict:
        counts = {status: statuses.count(status) for status in sorted(set(statuses))}
        if not statuses or all(status == "not-run" for status in statuses):
            status = "not-run"
        elif any(status not in {"passed", "skipped", "not-run"} for status in statuses):
            status = "failed"
        elif all(status == "passed" for status in statuses):
            status = "passed"
        elif all(status == "skipped" for status in statuses):
            status = "skipped"
        else:
            status = "incomplete"
        return {"status": status, "counts": counts}

    outcomes = {}
    complete = not issues and returncode == 0
    for engine in engines:
        first_party = outcome(checks[engine]["first_party"])
        guacamole = outcome(checks[engine]["guacamole"])
        if not guacamole_available:
            guacamole["reason"] = "pinned assets missing"
        tested = not issues and first_party["status"] == "passed" and bool(versions[engine]) and selection is None
        selected_statuses = checks[engine]["first_party"] + checks[engine]["guacamole"]
        selected_passed = not issues and bool(versions[engine]) and "passed" in selected_statuses and all(
            status == "passed" for status in checks[engine]["first_party"]) and all(
            status == "passed" or (status == "skipped" and not guacamole_available)
            for status in checks[engine]["guacamole"])
        complete &= selected_passed if selection is not None else tested and (
            guacamole["status"] == "passed" if guacamole_available else guacamole["status"] == "skipped")
        outcomes[engine] = {"browser_versions": sorted(versions[engine]), "application_tested": tested,
                            "selected_checks_passed": selected_passed,
                            "first_party": first_party, "guacamole": guacamole}
    return {"engines": engines, "playwright_version": playwright_version, "context": context,
            "network_origin": "host", "workers": 1, "test_exit_code": returncode,
            "exit_code": returncode or (0 if complete else 1), "issues": issues, "outcomes": outcomes,
            "guacamole_boundary": "disposable protocol fixture", "native_esr": "not tested: separate release qualification",
            "project_ui": "out of scope", "scope": "focused" if selection is not None else "full", "selection": selection}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("chromium", "firefox"), action="append", help="Default: both engines")
    parser.add_argument("--context", choices=("managed", "project"), default="managed")
    parser.add_argument("--project", help="Existing locked Playwright package directory")
    parser.add_argument("--guacamole-assets", type=Path, help="Existing pinned Guacamole share/all.js")
    parser.add_argument("--require-guacamole", action="store_true", help="Fail instead of reporting missing integration prerequisites")
    parser.add_argument("--grep", help="Run tests matching this Playwright title regex; results are focused, not full qualification")
    parser.add_argument("--headed", action="store_true", help="Show the selected browser on the development desktop")
    parser.add_argument("--trace", action="store_true", help="Keep traces for successful checks too")
    args = parser.parse_args(argv)
    if args.grep is not None and (not args.grep.strip() or len(args.grep) > 512):
        parser.error("--grep must be a non-empty title regex of at most 512 characters")
    if os.environ.get("CI"):
        parser.error("Intensive browser checks run on development machines, outside CI")
    try:
        runtime = _runtime(args.context, args.project)
        if not runtime or runtime["version"] != PLAYWRIGHT_VERSION:
            raise ValueError(f"Install the repository-pinned Playwright {PLAYWRIGHT_VERSION} explicitly; no packages are installed by this command")
        package = read_json_file(str(Path(runtime["module"]) / "package.json"), max_bytes=64 * 1024)
        if package.get("name") not in {"playwright", "@playwright/test"}:
            raise ValueError("The selected package must include the Playwright test runner")
        for executable in ("openssl", "certutil"):
            if not shutil.which(executable):
                raise ValueError(f"{executable} is required; install developer prerequisites explicitly")
        node = "/usr/bin/node" if args.context == "managed" else shutil.which("node")
        if not node:
            raise ValueError("Node.js is required")
        asset = args.guacamole_assets or Path(RELEASE) / "share/all.js"
        validate_filesystem_path(str(asset), must_exist=False)
        if not asset.is_file():
            if args.guacamole_assets or args.require_guacamole:
                raise ValueError("Pinned Guacamole all.js is required for the disposable integration checks")
            asset = None
        elif hashlib.sha256(asset.read_bytes()).hexdigest() != GUACAMOLE_JS_SHA256:
            raise ValueError("Guacamole all.js does not match the reviewed pinned client")
        os.umask(0o077)
        evidence = Path.home() / ".local/state/basaltwater/browser-tests"
        validate_filesystem_path(str(evidence), must_exist=False)
        for component in (evidence, *list(evidence.parents)[:-1]):
            if component.is_symlink():
                raise ValueError("Browser evidence directory must not traverse symlinks")
        evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        evidence.chmod(0o700)
        output = Path(tempfile.mkdtemp(prefix="run-", dir=evidence))
        print(f"Private evidence: {output}", flush=True)
        with tempfile.TemporaryDirectory(prefix="basaltwater-browser-fixtures-") as temporary:
            directory = Path(temporary)
            command = [sys.executable, "-m", "tests.browser.fixtures", str(directory)]
            if asset:
                command += ["--guacamole-assets", str(asset.resolve())]
            with (output / "fixtures.log").open("w") as log:
                server = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 30
                    metadata = directory / "fixture.json"
                    while not metadata.is_file():
                        if server.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(f"Fixture startup failed; inspect {output / 'fixtures.log'}")
                        time.sleep(0.1)
                    environment = _environment(args.context)
                    environment.update({"BASALTW_BROWSER_FIXTURE": str(metadata), "BASALTW_BROWSER_OUTPUT": str(output),
                                        "BASALTW_PLAYWRIGHT_MODULE": runtime["module"], "NODE_EXTRA_CA_CERTS": str(directory / "ca.crt"),
                                        "BASALTW_BROWSER_HEADED": "1" if args.headed else "0",
                                        "BASALTW_BROWSER_TRACE": "1" if args.trace else "0", "BASALTW_BROWSER_PYTHON": sys.executable})
                    engines = list(dict.fromkeys(args.engine or ("chromium", "firefox")))
                    result = run([node, runtime["cli"], "test", "--config", str(ROOT / "tests/browser/playwright.config.cjs"),
                                  *[f"--project={engine}" for engine in engines], *(["--grep", args.grep] if args.grep is not None else [])],
                                 check=False, env=environment, cwd=ROOT, timeout=1800)
                    summary = summarize_results(output, engines=engines, playwright_version=runtime["version"],
                                                context=args.context, returncode=result.returncode, guacamole_available=asset is not None,
                                                selection=args.grep)
                    write_json_atomic(str(output / "summary.json"), summary)
                    return summary["exit_code"]
                finally:
                    server.terminate()
                    try:
                        server.wait(15)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        print(f"Browser qualification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

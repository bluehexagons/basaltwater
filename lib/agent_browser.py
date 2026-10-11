"""Engine-specific checks using an existing managed or project Playwright runtime."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
import re
import shutil
import sys

from common.browser_automation_steps import PLAYWRIGHT_ROOT
from lib.atomic_io import read_json_file
from lib.remote_utils import CommandTimeoutError, run as run_command
from lib.validation import validate_filesystem_path


_PACKAGES = ("playwright", "@playwright/test", "playwright-core")
_SMOKE_SCRIPT = """
const [modulePath, engine] = process.argv.slice(1);
(async () => {
  const browserType = require(modulePath)[engine];
  const browser = await browserType.launch({headless: true, timeout: 60000,
    executablePath: browserType.executablePath(),
    args: engine === 'chromium' ? ['--disable-features=NativeNotifications'] : []});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(30000);
    await page.setContent(`<button onclick="this.textContent='ready'">verify</button>`);
    await page.getByRole('button', {name: 'verify', exact: true}).click();
    if (await page.getByRole('button', {name: 'ready', exact: true}).count() !== 1 ||
        (await page.screenshot()).length < 100) throw new Error('Interaction or rendering failed');
    process.stdout.write(JSON.stringify({healthy: true, engine, browser_version: browser.version()}));
  } finally { await browser.close(); }
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""


def _runtime(context: str, project: str | None) -> dict[str, str] | None:
    if context not in ("managed", "project"):
        raise ValueError("Browser context must be managed or project")
    if context == "managed":
        if project is not None:
            raise ValueError("--project requires --context project")
        root = PLAYWRIGHT_ROOT
    else:
        if not project:
            raise ValueError("--context project requires --project DIRECTORY")
        root = os.path.abspath(os.path.expanduser(project))
        validate_filesystem_path(root, must_exist=True)
        if not os.path.isdir(root):
            raise ValueError("Browser project must be a directory")
    for name in _PACKAGES:
        # pnpm and monorepos may link packages; deliberately resolve the installed
        # package rather than searching global modules or downloading a fallback.
        module = os.path.realpath(os.path.join(root, "node_modules", name))
        try:
            package = read_json_file(os.path.join(module, "package.json"), max_bytes=64 * 1024)
        except (OSError, ValueError):
            continue
        if (isinstance(package, dict) and package.get("name") == name
                and isinstance(package.get("version"), str)
                and re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,99}", package["version"])):
            return {"module": module, "version": package["version"], "root": root,
                    "cli": os.path.join(module, "cli.js")}
    return None


def browser_context_inventory(project: str) -> list[dict[str, object]]:
    """Report prerequisites without launching browsers or asserting test coverage."""
    managed = _runtime("managed", None)
    local = _runtime("project", project)
    contexts = [{"engine": None, "context": "collaborative-preview",
                 "network_origin": "connected-client", "readiness": "session-dependent"}]
    for context, runtime, engines in (("managed", managed, ("chromium", "firefox")),
                                      ("project", local, ("chromium", "firefox"))):
        for engine in engines:
            command = ["basaltw", "agent", "browser", "doctor", "--context", context, "--engine", engine]
            if context == "project":
                command.extend(("--project", project))
            command.append("--json")
            contexts.append({"engine": engine, "context": context, "headless": True,
                             "network_origin": "host", "readiness": "unverified" if runtime else "runtime-missing",
                             "playwright_version": runtime["version"] if runtime else None,
                             "verification_argv": command})
    desktop = shutil.which("firefox") or shutil.which("firefox-esr")
    contexts.append({"engine": "firefox", "context": "interactive-desktop", "headless": False,
                     "network_origin": "host", "executable": desktop,
                     "readiness": "unverified" if desktop else "not-on-path",
                     "instructions": "Verify in the active desktop session; this browser is separate from Playwright's patched Firefox."})
    return contexts


def _environment(context: str) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in ("NODE_OPTIONS", "NODE_PATH")}
    if context == "managed":
        environment["PLAYWRIGHT_BROWSERS_PATH"] = os.path.expanduser("~/.cache/ms-playwright")
    # An optional engine installation must not garbage-collect other projects'
    # payloads. Retain the project's explicit browser-cache selection.
    environment["PLAYWRIGHT_SKIP_BROWSER_GC"] = "1"
    return environment


def check_browser(engine: str, context: str, project: str | None, *, install: bool = False) -> dict[str, object]:
    """Install only an optional payload, then verify the selected existing runtime."""
    if engine not in ("chromium", "firefox"):
        raise ValueError("Browser engine must be chromium or firefox")
    runtime = _runtime(context, project)
    result = {"engine": engine, "context": context, "network_origin": "host", "headless": True,
              "healthy": False, "ok": False, "playwright_version": runtime["version"] if runtime else None,
              "rendering_verified": False, "application_verified": False}
    if runtime is None:
        return {**result, "issue": "runtime_missing", "remediation": "rerun_setup_with_browser_automation" if context == "managed" else "install_project_dependencies_from_its_lockfile"}
    node = "/usr/bin/node" if context == "managed" else shutil.which("node")
    if not node or not os.path.isfile(node) or not os.access(node, os.X_OK):
        return {**result, "issue": "node_missing", "remediation": "select_installed_project_node_runtime" if context == "project" else "rerun_setup_with_browser_automation"}
    environment = _environment(context)
    try:
        if install:
            if not os.path.isfile(runtime["cli"]):
                return {**result, "issue": "cli_missing", "remediation": "inspect_selected_playwright_package"}
            with redirect_stdout(sys.stderr):
                installed = run_command([node, runtime["cli"], "install", engine], check=False,
                                        capture_output=True, text=True, timeout=600,
                                        env=environment, cwd=runtime["root"])
            if installed.returncode != 0:
                return {**result, "issue": "browser_install_failed", "remediation": "inspect_browser_download_or_system_dependencies",
                        "error": (installed.stderr or installed.stdout or "Browser install failed")[-2000:]}
            result["payload_install_completed"] = True
        with redirect_stdout(sys.stderr):
            tested = run_command([node, "-e", _SMOKE_SCRIPT, runtime["module"], engine], check=False,
                                 capture_output=True, text=True, timeout=180, env=environment, cwd=runtime["root"])
        if tested.returncode == 0:
            smoke = json.loads(tested.stdout)
            if isinstance(smoke, dict) and smoke.get("healthy") is True and smoke.get("engine") == engine:
                return {**result, "ok": True, "healthy": True, "rendering_verified": True,
                        "browser_version": smoke.get("browser_version"), "issue": None, "remediation": None}
        error = (tested.stderr or tested.stdout or "Browser smoke test failed")[-2000:]
        issue, remediation = "browser_smoke_failed", "inspect_browser_runtime_and_system_dependencies"
        if "Executable doesn't exist" in error:
            issue, remediation = "browser_payload_missing", "install_selected_browser_payload"
        return {**result, "issue": issue, "remediation": remediation, "error": error}
    except CommandTimeoutError:
        return {**result, "issue": "browser_timeout", "remediation": "inspect_host_capacity_and_browser_runtime"}
    except (OSError, ValueError) as exc:
        return {**result, "issue": "browser_check_failed", "remediation": "inspect_selected_playwright_package", "error": str(exc)}


def add_browser_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("browser", help="Check or install a selected existing Playwright engine")
    actions = parser.add_subparsers(dest="browser_command", required=True)
    for name in ("doctor", "install"):
        action = actions.add_parser(name)
        action.add_argument("--engine", choices=("chromium", "firefox"), default="chromium")
        action.add_argument("--context", choices=("managed", "project"), default="managed")
        action.add_argument("--project")
        action.add_argument("--json", action="store_true")


def run_browser_command(args: argparse.Namespace) -> int:
    try:
        result = check_browser(args.engine, args.context, args.project, install=args.browser_command == "install")
    except (OSError, ValueError) as exc:
        result = {"ok": False, "healthy": False, "issue": "invalid_browser_request",
                  "remediation": "check_context_and_project_path", "error": str(exc)}
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0 if result["ok"] else 1

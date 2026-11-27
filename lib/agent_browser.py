"""Readiness checks for T3 preview and existing Playwright runtimes."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
import re
import shutil
import sys

from common.browser_automation_steps import (
    PLAYWRIGHT_ROOT, PLAYWRIGHT_STABLE_ROOT, PLAYWRIGHT_VERSION, PLAYWRIGHT_MCP_PLAYWRIGHT_VERSION,
)
from desktop.browser_session import BROWSER_APPLICATIONS, FLATPAK_BROWSER_IDS, supervised_browser_argv
from lib.atomic_io import read_json_file
from lib.browser_registration import browser_registrations, register_browser_agent, registration_status
from lib.cachyos import is_cachyos
from lib.remote_utils import CommandTimeoutError, get_os_id, run as run_command
from lib.node_toolchain import select_node
from lib.service_node import SERVICE_NODE, verify_service_node
from lib.t3_preview import add_t3_preview_arguments, run_t3_preview_command
from lib.tool_results import add_json_option, exception_issue, from_report, write_result
from lib.validation import validate_filesystem_path


_PACKAGES = ("playwright", "@playwright/test", "playwright-core")
_SMOKE_SCRIPT = """
const [modulePath, engine] = process.argv.slice(1);
(async () => {
  const browserType = require(modulePath)[engine];
  const executablePath = browserType.executablePath();
  if (!require('node:fs').existsSync(executablePath)) {
    throw new Error(`Browser executable missing: ${executablePath}`);
  }
  const browser = await browserType.launch({headless: true, timeout: 60000,
    executablePath,
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
    if context not in ("managed", "managed-mcp", "project"):
        raise ValueError("Browser context must be managed, managed-mcp or project")
    if context in ("managed", "managed-mcp"):
        if project is not None:
            raise ValueError("--project requires --context project")
        root = PLAYWRIGHT_STABLE_ROOT if context == "managed" else PLAYWRIGHT_ROOT
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


def browser_node(context: str, project: str | None) -> str:
    """Select the verified service interpreter or the project's supported Node."""
    if context in ("managed", "managed-mcp"):
        with redirect_stdout(sys.stderr):
            verify_service_node()
        return SERVICE_NODE
    return select_node(project or ".").executable


def browser_context_inventory(project: str) -> list[dict[str, object]]:
    """Report prerequisites without launching browsers or asserting test coverage."""
    managed = _runtime("managed", None)
    mcp = _runtime("managed-mcp", None)
    local = _runtime("project", project)
    contexts = [{"engine": "unknown", "context": "collaborative-preview", "browser_version": None,
                 "network_origin": None, "readiness": "session-dependent",
                 "instructions": "Inspect the active session's tab runtime: server-hosted tabs use the server's network; desktop-hosted tabs use the connected client's network. Follow session policy and verify automation support before reusing a tab.",
                 "discovered": None, "launchable": None, "mcp_registered": None, "application_tested": False}]
    for context, runtime, engines in (("managed", managed, ("chromium", "firefox")),
                                      ("managed-mcp", mcp, ("chromium", "firefox")),
                                      ("project", local, ("chromium", "firefox"))):
        for engine in engines:
            readiness = "unverified" if runtime else "runtime-missing"
            expected = PLAYWRIGHT_MCP_PLAYWRIGHT_VERSION if context == "managed-mcp" else PLAYWRIGHT_VERSION
            if context != "project" and runtime and runtime["version"] != expected:
                readiness = "runtime-version-mismatch"
            command = ["basaltw", "agent", "browser", "doctor", "--context", context, "--engine", engine]
            if context == "project":
                command.extend(("--project", project))
            command.append("--json")
            contexts.append({"engine": engine, "context": context, "headless": True,
                             "network_origin": "host", "readiness": readiness,
                             "discovered": runtime is not None, "launchable": None,
                             "mcp_registered": registration_status(browser_registrations(engine)) if context == "managed-mcp" else False,
                             "prerelease_dependency": context == "managed-mcp",
                             "application_tested": False, "browser_version": None,
                             "playwright_version": runtime["version"] if runtime else None,
                             "verification_argv": command})
    contexts.extend(desktop_firefox_inventory())
    contexts.extend(_desktop_inventory("chromium"))
    return contexts


def desktop_firefox_inventory() -> list[dict[str, object]]:
    return _desktop_inventory("firefox")


def _desktop_inventory(engine: str) -> list[dict[str, object]]:
    """Discover desktop installations without opening a browser profile."""
    found = []
    for name in BROWSER_APPLICATIONS[engine]:
        executable = shutil.which(name)
        if executable and not any(item["executable"] == os.path.realpath(executable) for item in found):
            found.append({"installation": "native", "application": name,
                          "channel": "esr" if name == "firefox-esr" else "unknown",
                          "executable": os.path.realpath(executable), "version_argv": [executable, "--version"]})
    if flatpak := shutil.which("flatpak"):
        for application, app_id in FLATPAK_BROWSER_IDS.items():
            if application not in BROWSER_APPLICATIONS[engine]:
                continue
            try:
                with redirect_stdout(sys.stderr):
                    info = run_command([flatpak, "info", "--show-version", app_id],
                                       check=False, capture_output=True, text=True, timeout=5)
            except (OSError, CommandTimeoutError):
                continue
            if info.returncode == 0:
                found.append({"installation": "flatpak", "application": application, "channel": "unknown",
                              "executable": flatpak, "launch_argv": [flatpak, "run", app_id],
                              "browser_version": info.stdout.strip()[:120]})
    for item in found:
        version = item.pop("version_argv", None)
        if version:
            try:
                with redirect_stdout(sys.stderr):
                    info = run_command(version, check=False, capture_output=True, text=True, timeout=5)
                item["browser_version"] = info.stdout.strip()[:120] if info.returncode == 0 else None
            except (OSError, CommandTimeoutError):
                item["browser_version"] = None
    return [{"engine": engine, "context": "interactive-desktop", "headless": False,
             "network_origin": "host", "readiness": "unverified" if found else "not-found",
             "discovered": bool(found), "launchable": None, "mcp_registered": False,
             "application_tested": False, "browser_version": None,
             "instructions": "Verify in the active desktop session. Playwright uses its own browser payload.",
             **item} for item in (found or [{"installation": None, "executable": None}])]


def _environment(context: str) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in ("NODE_OPTIONS", "NODE_PATH")}
    if context in ("managed", "managed-mcp"):
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
              "discovered": runtime is not None, "launchable": None,
              "registrations": browser_registrations(engine) if context == "managed-mcp" else {},
              "prerelease_dependency": context == "managed-mcp",
              "application_tested": False,
              "rendering_verified": False, "application_verified": False}
    result["mcp_registered"] = registration_status(result["registrations"]) if context == "managed-mcp" else False
    if runtime is None:
        return {**result, "issue": "runtime_missing", "remediation": "rerun_setup_with_browser_automation" if context != "project" else "install_project_dependencies_from_its_lockfile"}
    expected = PLAYWRIGHT_MCP_PLAYWRIGHT_VERSION if context == "managed-mcp" else PLAYWRIGHT_VERSION
    if context != "project" and runtime["version"] != expected:
        return {**result, "issue": "runtime_version_mismatch", "expected_playwright_version": expected,
                "remediation": "rerun_setup_with_browser_automation"}
    try:
        node = browser_node(context, project)
        if not os.path.isfile(node) or not os.access(node, os.X_OK):
            raise ValueError("Selected Node executable is missing or not executable")
    except (OSError, ValueError, RuntimeError) as exc:
        return {**result, "issue": "node_unavailable", "error": str(exc),
                "remediation": "select_installed_project_node_runtime" if context == "project" else "rerun_setup_with_browser_automation"}
    environment = _environment(context)
    environment["PATH"] = os.pathsep.join((os.path.dirname(node), environment.get("PATH", os.defpath)))
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
            result["launchable"] = False
            tested = run_command([node, "-e", _SMOKE_SCRIPT, runtime["module"], engine], check=False,
                                 capture_output=True, text=True, timeout=180, env=environment, cwd=runtime["root"])
        if tested.returncode == 0:
            smoke = json.loads(tested.stdout)
            if isinstance(smoke, dict) and smoke.get("healthy") is True and smoke.get("engine") == engine:
                return {**result, "ok": True, "healthy": True, "launchable": True, "rendering_verified": True,
                        "browser_version": smoke.get("browser_version"), "issue": None, "remediation": None}
        error = tested.stderr or tested.stdout or "Browser smoke test failed"
        issue, remediation = "browser_smoke_failed", "inspect_browser_runtime_and_system_dependencies"
        if "Browser executable missing:" in error or "Executable doesn't exist" in error:
            issue, remediation = "browser_payload_missing", "install_selected_browser_payload"
        return {**result, "issue": issue, "remediation": remediation, "error": error[-2000:]}
    except CommandTimeoutError:
        return {**result, "issue": "browser_timeout", "remediation": "inspect_host_capacity_and_browser_runtime"}
    except (OSError, ValueError) as exc:
        return {**result, "issue": "browser_check_failed", "remediation": "inspect_selected_playwright_package", "error": str(exc)}


def add_browser_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("browser", help="Check native T3 preview or manage a Playwright engine")
    actions = parser.add_subparsers(dest="browser_command", required=True)
    preview = actions.add_parser("resolve-preview", help="Check a loopback service for native T3 preview")
    add_t3_preview_arguments(preview)
    for name in ("doctor", "install", "register"):
        action = actions.add_parser(name, help={
            "doctor": "Test the chosen Playwright runtime and discover native browsers for its engine",
            "install": "Install and check the engine payload for an existing Playwright runtime",
            "register": "Register a checked managed engine with an agent client",
        }[name])
        action.add_argument("--engine", choices=("chromium", "firefox"), default="chromium")
        action.add_argument("--context", choices=("managed", "managed-mcp", "project"), default="managed")
        action.add_argument("--project")
        add_json_option(action, default=True)
        if name == "register":
            action.add_argument("--agent-tool", choices=("codex", "opencode"), required=True)


def _doctor_options(result: dict[str, object], project: str | None) -> None:
    engine, context = result["engine"], result["context"]
    native = is_cachyos()
    desktop = _desktop_inventory(engine)
    for installation in desktop:
        if installation["installation"] == "native":
            installation["test_argv"] = supervised_browser_argv(
                installation["application"], project or ".", native=native,
            )
        elif installation["installation"] == "flatpak" and not native:
            installation["test_argv"] = ["basaltw", "desktop", "exec", "--", *installation["launch_argv"]]
    result["desktop"] = desktop
    if result["issue"] == "browser_payload_missing":
        command = ["basaltw", "agent", "browser", "install", "--engine", engine, "--context", context]
        if context == "project":
            command.extend(("--project", project))
        result["provisioning_argv"] = [*command, "--json"]
    elif result["remediation"] == "rerun_setup_with_browser_automation":
        result["provisioning"] = "Rerun the saved VM setup with --browser-automation playwright and a compatible agent tool."
    elif result["remediation"] == "install_project_dependencies_from_its_lockfile":
        result["provisioning"] = "Install the project's locked dependencies, then run this doctor again."
    if not native and get_os_id() == "debian" and not any(item["discovered"] for item in desktop):
        result["desktop_provisioning_argv"] = ["basaltw", "local", "browser", engine, "--no-default"]


def run_browser_command(args: argparse.Namespace) -> int:
    if args.browser_command == "resolve-preview":
        return run_t3_preview_command(args)
    result = None
    stage = "check"
    try:
        context = args.context
        if args.browser_command == "register":
            if context == "project":
                raise ValueError("MCP registration requires a managed context")
            context = "managed-mcp"
        result = check_browser(args.engine, context, args.project, install=args.browser_command == "install")
        if args.browser_command == "doctor":
            _doctor_options(result, args.project)
        if args.browser_command == "register" and result["ok"]:
            from lib.agent_cli import _browser_launchers_secure
            from common.browser_automation_steps import PLAYWRIGHT_MCP_WRAPPER

            launcher_security = _browser_launchers_secure()
            if launcher_security is None:
                raise ValueError("Managed launcher security is unverified in this context; verify ownership and permissions from an authorized host context")
            if launcher_security is not True:
                raise ValueError("Managed launchers are missing or unsafe; rerun the saved setup")
            with open(PLAYWRIGHT_MCP_WRAPPER, encoding="utf-8") as source:
                if args.engine == "firefox" and '--engine' not in source.read():
                    raise ValueError("Managed launcher lacks Firefox support; rerun the saved setup")
            with redirect_stdout(sys.stderr):
                stage = "register"
                register_browser_agent(args.engine, args.agent_tool)
            result["registrations"] = browser_registrations(args.engine)
            result["mcp_registered"] = result["registrations"].get(args.agent_tool) is True
            if not result["mcp_registered"]:
                raise ValueError("Agent registration could not be verified in the user's configuration")
            result["registered_agent"] = args.agent_tool
    except (OSError, RuntimeError, ValueError, CommandTimeoutError) as exc:
        if result is not None and args.browser_command == "register":
            result = {**result, "ok": False, "issue": "browser_registration_failed",
                      "remediation": "inspect_managed_launcher_and_agent_configuration", "error": str(exc)}
        else:
            result = {"engine": args.engine, "context": args.context, "ok": False, "healthy": False,
                      "issue": exception_issue(exc), "remediation": "check_context_and_project_path", "error": str(exc)}
    if not result["ok"] and args.browser_command != "doctor":
        result.update(stage=stage, effect="none" if args.browser_command == "register" and stage == "check"
                      or result.get("issue") == "invalid_input" else "unknown")
    return write_result(from_report(result, ok=result["ok"], issue="browser_check_failed",
                                    error="The browser check failed. Inspect the reported remediation."))

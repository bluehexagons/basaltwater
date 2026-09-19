"""Provision isolated Playwright browser automation for coding agents."""

from __future__ import annotations

import json
import os
import shlex
import stat
from typing import cast

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import is_dry_run, run
from lib.types import JSONDict

from .agent_steps import (
    _chown_path,
    _ensure_agent_directory,
    _reject_symlinked_agent_destination,
    _tool_available,
    _user_home,
    reconcile_agent_workflow_skills,
)
from .common_steps import _run_as_login_user


PLAYWRIGHT_MCP_VERSION = "0.0.79"
PLAYWRIGHT_MCP_SERVER_NAME = "basaltwater-playwright"
PLAYWRIGHT_MCP_INTEGRITY = (
    "sha512-VpqD4a3vFyGQMY9sh3UJiO6wjcurggkljKfAyCHL0QWGY5m6Ehr3MNsAAHPDHO//"
    "n13g0PCjpHatAOiulrqdZQ=="
)
PLAYWRIGHT_VERSION = "1.63.0-alpha-2026-08-05"
PLAYWRIGHT_INTEGRITY = (
    "sha512-zbGZUK+JYkoDV3cUgfvh2czTBJL34Gmz5gHVI25xiIpvYSR17Q1M7TS8hnwECUe+"
    "IkKaeXbKrSyJTyogm2DVWw=="
)
PLAYWRIGHT_CORE_INTEGRITY = (
    "sha512-YussvUybTfBtyYbGXWh43f+5kNP03wg98M6mu4DphYET7PSbNVajsdLGjWE1xrsj"
    "qOw32i2wFlRP7U5mcOpMZg=="
)

PLAYWRIGHT_ROOT = "/opt/basaltwater-playwright"
PLAYWRIGHT_MCP_CLI = os.path.join(
    PLAYWRIGHT_ROOT,
    "node_modules",
    "@playwright",
    "mcp",
    "cli.js",
)
PLAYWRIGHT_CLI = os.path.join(
    PLAYWRIGHT_ROOT,
    "node_modules",
    "playwright",
    "cli.js",
)
PLAYWRIGHT_MODULE = os.path.join(
    PLAYWRIGHT_ROOT,
    "node_modules",
    "playwright",
)
PLAYWRIGHT_SMOKE_SCRIPT = os.path.join(PLAYWRIGHT_ROOT, "browser-smoke.js")
PLAYWRIGHT_MCP_WRAPPER = "/usr/local/bin/basaltwater-playwright-mcp"
PLAYWRIGHT_DOCTOR_WRAPPER = "/usr/local/bin/basaltwater-playwright-doctor"
SYSTEM_NODE = "/usr/bin/node"
SYSTEM_TIMEOUT = "/usr/bin/timeout"
PLAYWRIGHT_SMOKE_ACTION_TIMEOUT_MS = 120_000
PLAYWRIGHT_SMOKE_PROCESS_TIMEOUT_SECONDS = 180
PLAYWRIGHT_MCP_OUTPUT_MAX_BYTES = 256 * 1024 * 1024
PLAYWRIGHT_MCP_SETTLE_TIMEOUT_MS = 1_000
PLAYWRIGHT_DEPS_MARKER = (
    f"/var/lib/basaltwater/state/playwright-deps-{PLAYWRIGHT_VERSION}"
)

_BROWSER_EXECUTABLE_SCRIPT = (
    f'const {{ chromium }} = require("{PLAYWRIGHT_MODULE}"); '
    "process.stdout.write(chromium.executablePath());"
)
_BROWSER_EXECUTABLE_COMMAND = (
    f"{SYSTEM_NODE} -e {shlex.quote(_BROWSER_EXECUTABLE_SCRIPT)}"
)


_MCP_WRAPPER_CONTENT = (
    "#!/bin/sh\n"
    "set -eu\n"
    "umask 077\n"
    'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"\n'
    f'browser_path="$({_BROWSER_EXECUTABLE_COMMAND})"\n'
    'if [ ! -f "$browser_path" ] || [ ! -x "$browser_path" ]; then\n'
    '  echo "Managed Playwright Chromium is not executable: $browser_path" >&2\n'
    "  exit 1\n"
    "fi\n"
    'output_dir="$HOME/.local/state/basaltwater/playwright-mcp"\n'
    'mkdir -p "$output_dir"\n'
    'chmod 0700 "$output_dir"\n'
    f"exec {SYSTEM_NODE} {PLAYWRIGHT_MCP_CLI} --headless --isolated \\\n"
    '  --executable-path "$browser_path" \\\n'
    "  --caps vision \\\n"
    '  --output-dir "$output_dir" \\\n'
    f"  --timeout-settle {PLAYWRIGHT_MCP_SETTLE_TIMEOUT_MS} \\\n"
    f'  --output-max-size {PLAYWRIGHT_MCP_OUTPUT_MAX_BYTES} "$@"\n'
)

_DOCTOR_WRAPPER_CONTENT = (
    "#!/bin/sh\n"
    "set -eu\n"
    'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"\n'
    f"exec {SYSTEM_TIMEOUT} --signal=TERM --kill-after=10s "
    f"{PLAYWRIGHT_SMOKE_PROCESS_TIMEOUT_SECONDS}s "
    f"{SYSTEM_NODE} {PLAYWRIGHT_SMOKE_SCRIPT}\n"
)

_SMOKE_SCRIPT_CONTENT = f"""'use strict';

const {{ chromium }} = require('{PLAYWRIGHT_ROOT}/node_modules/playwright');

(async () => {{
  const executablePath = chromium.executablePath();
  const browser = await chromium.launch({{
    executablePath,
    headless: true,
    timeout: {PLAYWRIGHT_SMOKE_ACTION_TIMEOUT_MS},
  }});
  try {{
    const page = await browser.newPage({{
      viewport: {{ width: 640, height: 480 }},
    }});
    page.setDefaultTimeout({PLAYWRIGHT_SMOKE_ACTION_TIMEOUT_MS});
    page.setDefaultNavigationTimeout({PLAYWRIGHT_SMOKE_ACTION_TIMEOUT_MS});
    await page.setContent(`
      <button id="verify" onclick="this.textContent='browser-ready'">verify</button>
    `);
    await page.locator('#verify').click();
    const text = await page.locator('#verify').textContent();
    const screenshot = await page.screenshot();
    if (text !== 'browser-ready' || screenshot.length < 100) {{
      throw new Error('browser interaction or rendering verification failed');
    }}
    process.stdout.write('browser-ready\\n');
  }} finally {{
    await browser.close();
  }}
}})().catch((error) => {{
  process.stderr.write(`${{error.stack || error}}\\n`);
  process.exitCode = 1;
}});
"""


def _opencode_config_path(config_dir: str) -> str:
    """Return the existing OpenCode global config, preferring JSON."""
    json_path = os.path.join(config_dir, "opencode.json")
    jsonc_path = os.path.join(config_dir, "opencode.jsonc")
    if os.path.lexists(json_path):
        return json_path
    if os.path.lexists(jsonc_path):
        return jsonc_path
    return json_path


def _strip_jsonc_comments(content: str) -> str:
    """Remove JSONC comments without interpreting comment markers in strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        character = content[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue

        if character == '"':
            in_string = True
            output.append(character)
            index += 1
        elif character == "/" and index + 1 < len(content) and content[index + 1] == "/":
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                index += 1
        elif character == "/" and index + 1 < len(content) and content[index + 1] == "*":
            # A comment is whitespace, not token concatenation (1/*x*/2 must
            # remain invalid JSONC instead of silently becoming 12).
            output.append(" ")
            index += 2
            while index + 1 < len(content) and content[index:index + 2] != "*/":
                if content[index] in "\r\n":
                    output.append(content[index])
                index += 1
            if index + 1 >= len(content):
                raise ValueError("Unterminated JSONC block comment")
            index += 2
        else:
            output.append(character)
            index += 1
    return "".join(output)


def _strip_jsonc_trailing_commas(content: str) -> str:
    """Remove trailing commas without changing commas inside JSON strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        character = content[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue

        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == ",":
            lookahead = index + 1
            while lookahead < len(content) and content[lookahead].isspace():
                lookahead += 1
            if lookahead < len(content) and content[lookahead] in "}]":
                index += 1
                continue
        output.append(character)
        index += 1
    return "".join(output)


def _load_opencode_config(path: str) -> JSONDict:
    """Load JSON or JSONC OpenCode configuration into a JSON object."""
    # Setup may run as root against a user-controlled destination. Do not
    # follow a substituted link or block opening a FIFO before checking it.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, encoding="utf-8") as file_obj:
        file_stat = os.fstat(file_obj.fileno())
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise ValueError(f"OpenCode config must be a singly linked regular file: {path}")
        content = file_obj.read()
    normalized = _strip_jsonc_trailing_commas(_strip_jsonc_comments(content))
    loaded = json.loads(normalized)
    if not isinstance(loaded, dict):
        raise ValueError(f"OpenCode config must contain a JSON object: {path}")
    return cast(JSONDict, loaded)


def _ensure_safe_root_path(path: str) -> None:
    """Reject a symlinked root-owned install path before mutation."""
    absolute_path = os.path.abspath(path)
    current = os.path.sep
    for component in absolute_path.split(os.path.sep):
        if not component:
            continue
        current = os.path.join(current, component)
        if os.path.lexists(current) and os.path.islink(current):
            raise RuntimeError(f"Refusing symlinked browser automation path: {current}")


def _install_runtime_package(*, refresh: bool = False) -> bool:
    """Install and verify the pinned MCP package without lifecycle scripts."""
    _ensure_safe_root_path(PLAYWRIGHT_ROOT)
    run("apt-get -o DPkg::Lock::Timeout=60 install -y -qq ca-certificates nodejs npm")
    run(f"install -d -m 0755 -o root -g root {shlex.quote(PLAYWRIGHT_ROOT)}")
    package_path = os.path.join(
        PLAYWRIGHT_ROOT,
        "node_modules",
        "@playwright",
        "mcp",
        "package.json",
    )
    lock_path = os.path.join(PLAYWRIGHT_ROOT, "package-lock.json")
    try:
        _verify_runtime_package(package_path, lock_path)
    except RuntimeError:
        pass
    else:
        if not refresh:
            print("  ✓ Playwright MCP runtime already verified; skipping npm install")
            return False
    if refresh:
        print("  Refreshing the pinned Playwright MCP runtime")
    if not refresh:
        print("  Repairing the missing or invalid Playwright MCP runtime")

    run(
        "npm install --ignore-scripts --no-audit --no-fund --save-exact "
        f"--prefix {shlex.quote(PLAYWRIGHT_ROOT)} "
        f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}"
    )

    node_result = run(f"{SYSTEM_NODE} --version", capture_output=True)
    raw_node_version = node_result.stdout.strip().removeprefix("v")
    try:
        node_major = int(raw_node_version.split(".", 1)[0])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"Could not determine Node.js version: {raw_node_version}") from exc
    if node_major < 18:
        raise RuntimeError("Playwright MCP requires Node.js 18 or newer")

    _verify_runtime_package(package_path, lock_path)

    run(f"chown -R root:root {shlex.quote(PLAYWRIGHT_ROOT)}")
    run(f"chmod -R go-w {shlex.quote(PLAYWRIGHT_ROOT)}")
    return True


def _verify_runtime_package(package_path: str, lock_path: str) -> None:
    """Verify every npm package whose code is executed during provisioning."""
    expected_lock_entries = {
        "node_modules/@playwright/mcp": (
            PLAYWRIGHT_MCP_VERSION,
            PLAYWRIGHT_MCP_INTEGRITY,
        ),
        "node_modules/playwright": (PLAYWRIGHT_VERSION, PLAYWRIGHT_INTEGRITY),
        "node_modules/playwright-core": (
            PLAYWRIGHT_VERSION,
            PLAYWRIGHT_CORE_INTEGRITY,
        ),
    }
    try:
        with open(package_path, encoding="utf-8") as file_obj:
            package = json.load(file_obj)
        with open(lock_path, encoding="utf-8") as file_obj:
            package_lock = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not verify the installed Playwright MCP package") from exc

    if not isinstance(package, dict) or package.get("version") != PLAYWRIGHT_MCP_VERSION:
        raise RuntimeError("Installed Playwright MCP package failed version verification")
    lock_packages = package_lock.get("packages") if isinstance(package_lock, dict) else None
    if not isinstance(lock_packages, dict):
        raise RuntimeError("Installed Playwright package lock has no package inventory")

    for package_name, (expected_version, expected_integrity) in expected_lock_entries.items():
        lock_entry = lock_packages.get(package_name)
        if (
            not isinstance(lock_entry, dict)
            or lock_entry.get("version") != expected_version
            or lock_entry.get("integrity") != expected_integrity
        ):
            raise RuntimeError(
                f"Installed Playwright package failed integrity verification: {package_name}"
            )


def _write_launchers() -> None:
    """Install fixed headless launchers and the local browser smoke test."""
    for path in (
        PLAYWRIGHT_MCP_WRAPPER,
        PLAYWRIGHT_DOCTOR_WRAPPER,
        PLAYWRIGHT_SMOKE_SCRIPT,
    ):
        _ensure_safe_root_path(path)

    write_text_atomic(PLAYWRIGHT_MCP_WRAPPER, _MCP_WRAPPER_CONTENT, mode=0o755)
    write_text_atomic(PLAYWRIGHT_DOCTOR_WRAPPER, _DOCTOR_WRAPPER_CONTENT, mode=0o755)
    write_text_atomic(PLAYWRIGHT_SMOKE_SCRIPT, _SMOKE_SCRIPT_CONTENT, mode=0o644)


def reconcile_existing_browser_automation(config: SetupConfig) -> None:
    """Refresh launchers for an existing managed browser installation."""
    if config.browser_automation or config.disable_browser_automation:
        return
    if is_dry_run():
        print(
            "  [DRY-RUN] Would reconcile existing managed Playwright launchers "
            "when installed"
        )
        return

    required_paths = (
        PLAYWRIGHT_MCP_CLI,
        PLAYWRIGHT_CLI,
        PLAYWRIGHT_MCP_WRAPPER,
        PLAYWRIGHT_DOCTOR_WRAPPER,
        PLAYWRIGHT_SMOKE_SCRIPT,
    )
    if not all(os.path.isfile(path) for path in required_paths):
        print("  ✓ No complete existing Playwright browser installation to reconcile")
        return

    _write_launchers()
    print("  ✓ Existing Playwright browser launchers reconciled")


def _install_browser(config: SetupConfig) -> bool:
    """Install OS dependencies and a user-owned Chromium browser payload."""
    user_home = _user_home(config)
    deps_changed = config.refresh_packages or not os.path.exists(PLAYWRIGHT_DEPS_MARKER)
    if deps_changed:
        run(f"{SYSTEM_NODE} {shlex.quote(PLAYWRIGHT_CLI)} install-deps chromium")
        os.makedirs(os.path.dirname(PLAYWRIGHT_DEPS_MARKER), mode=0o755, exist_ok=True)
        with open(PLAYWRIGHT_DEPS_MARKER, "w", encoding="utf-8") as marker:
            marker.write("basaltwater Playwright OS dependencies installed\n")
        os.chmod(PLAYWRIGHT_DEPS_MARKER, 0o644)
    else:
        print("  ✓ Playwright OS dependencies already installed; skipping apt transaction")

    browser_marker = (
        f"$HOME/.cache/ms-playwright/.basaltwater-chromium-"
        f"{PLAYWRIGHT_VERSION}.installed"
    )
    browser_ready = not config.refresh_packages and _run_as_login_user(
        config.username,
        user_home,
        'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" && '
        f'test -f "{browser_marker}" && '
        f'test -f "$({_BROWSER_EXECUTABLE_COMMAND})" && '
        f'test -x "$({_BROWSER_EXECUTABLE_COMMAND})"',
        check=False,
    ).returncode == 0
    if browser_ready:
        print("  ✓ Playwright Chromium already installed; skipping browser download")
        return deps_changed

    _run_as_login_user(
        config.username,
        user_home,
        "export PLAYWRIGHT_BROWSERS_PATH=\"$HOME/.cache/ms-playwright\" && "
        f"{SYSTEM_NODE} {shlex.quote(PLAYWRIGHT_CLI)} install chromium && "
        f'test -f "$({_BROWSER_EXECUTABLE_COMMAND})" && '
        f'test -x "$({_BROWSER_EXECUTABLE_COMMAND})" && '
        f"mkdir -p \"$HOME/.cache/ms-playwright\" && "
        f"printf '%s\\n' {shlex.quote(PLAYWRIGHT_VERSION)} > \"{browser_marker}\"",
    )
    return True


def _configure_codex(config: SetupConfig) -> None:
    """Register the managed Playwright MCP launcher through the Codex CLI."""
    if not _tool_available(config, "codex"):
        raise RuntimeError("Codex was selected but is not available for MCP registration")
    user_home = _user_home(config)
    path_setup = 'export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH" && '
    _run_as_login_user(
        config.username,
        user_home,
        f"{path_setup}codex mcp remove {PLAYWRIGHT_MCP_SERVER_NAME} "
        ">/dev/null 2>&1 || true",
        check=False,
    )
    result = _run_as_login_user(
        config.username,
        user_home,
        f"{path_setup}codex mcp add {PLAYWRIGHT_MCP_SERVER_NAME} -- "
        f"{PLAYWRIGHT_MCP_WRAPPER}",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "registration failed").strip()
        raise RuntimeError(f"Codex MCP registration failed: {detail}")


def _configure_opencode(config: SetupConfig) -> None:
    """Merge the managed local MCP server into stable OpenCode configuration."""
    if not _tool_available(config, "opencode"):
        raise RuntimeError("OpenCode was selected but is not available for MCP registration")
    user_home = _user_home(config)
    config_dir = os.path.join(user_home, ".config", "opencode")
    _ensure_agent_directory(config_dir)
    _reject_symlinked_agent_destination(config_dir)
    config_path = _opencode_config_path(config_dir)

    value: JSONDict = {}
    if os.path.lexists(config_path):
        try:
            value = _load_opencode_config(config_path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Cannot update malformed OpenCode config: {config_path}") from exc

    existing_mcp = value.get("mcp")
    if existing_mcp is None:
        mcp: JSONDict = {}
    elif isinstance(existing_mcp, dict):
        mcp = cast(JSONDict, existing_mcp)
    else:
        raise RuntimeError("OpenCode config 'mcp' entry must contain a JSON object")
    mcp[PLAYWRIGHT_MCP_SERVER_NAME] = {
        "type": "local",
        "command": [PLAYWRIGHT_MCP_WRAPPER],
        "enabled": True,
        "timeout": 30000,
    }
    value["mcp"] = mcp
    write_json_atomic(config_path, value, mode=0o600, sort_keys=True)
    _chown_path(config, config_dir)


def _run_smoke_test(config: SetupConfig) -> None:
    """Verify browser startup, DOM interaction, rendering, and clean shutdown."""
    result = _run_as_login_user(
        config.username,
        _user_home(config),
        PLAYWRIGHT_DOCTOR_WRAPPER,
        check=False,
        capture_output=True,
    )
    if result.returncode == 124:
        raise RuntimeError(
            "Playwright browser smoke test timed out after "
            f"{PLAYWRIGHT_SMOKE_PROCESS_TIMEOUT_SECONDS} seconds; the target may "
            "be under memory, swap, or storage pressure"
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "browser check failed").strip()
        raise RuntimeError(f"Playwright browser smoke test failed: {detail}")
    if result.stdout.strip() != "browser-ready":
        raise RuntimeError("Playwright browser smoke test did not return browser-ready")
    print("  Playwright browser smoke test passed")


def install_browser_automation(config: SetupConfig) -> None:
    """Install Playwright and register it for each selected compatible agent."""
    if config.browser_automation != "playwright":
        raise RuntimeError(f"Unsupported browser automation provider: {config.browser_automation}")
    if is_dry_run():
        print("  [DRY-RUN] Would install pinned Playwright MCP and Chromium")
        print("  [DRY-RUN] Would register browser automation for selected agents")
        return

    if config.refresh_packages:
        _install_runtime_package(refresh=True)
    else:
        _install_runtime_package()
    _write_launchers()
    _install_browser(config)
    reconcile_agent_workflow_skills(config)

    selected_tools = set(config.selected_agent_tools())
    if "codex" in selected_tools:
        _configure_codex(config)
    if "opencode" in selected_tools:
        _configure_opencode(config)
    _run_smoke_test(config)

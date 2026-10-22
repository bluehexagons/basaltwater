"""Provision isolated Playwright browser automation for coding agents."""

from __future__ import annotations

import json
import os
import shlex
import stat
import tempfile
from typing import cast

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import is_dry_run, run
from lib.service_node import SERVICE_NODE, SERVICE_NPM_CLI, install_service_node, node_environment, verify_service_node
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


PLAYWRIGHT_MCP_VERSION = "0.0.83"
PLAYWRIGHT_MCP_SERVER_NAME = "basaltwater-playwright"
PLAYWRIGHT_MCP_INTEGRITY = (
    "sha512-oNcl+Ae2/IAjhfPeP46BfIkSakfmprY+aOtkv5MjrQ4lPav4/yNtPhL0iq8SlIM90o"
    "ApWgBDUxaNKvktazUKOg=="
)
PLAYWRIGHT_MCP_PLAYWRIGHT_VERSION = "1.64.0-alpha-1790635538000"
PLAYWRIGHT_MCP_PLAYWRIGHT_INTEGRITY = (
    "sha512-/5XDUMxpOd/9AojJtWDFIRV3EJZNQ0AwvK69wgt3CPzy8wTgiBG9pX45VfxtqtvIR"
    "GwfWzMhrvawNKreG69Snw=="
)
PLAYWRIGHT_MCP_CORE_INTEGRITY = (
    "sha512-pNwaXirhXMRLaRQs4NQ18EpTdtDoFwyPH9FOaaDC7YXG4hpwE2xmwCic/1srbuEx"
    "VQC7zT9LJSJpDBAru+Vo9A=="
)
PLAYWRIGHT_VERSION = "1.64.1"
PLAYWRIGHT_INTEGRITY = (
    "sha512-HCrVhHr5QQuwYoswEUGTb7gGp522yflbv/ST8nF4sFWGuLjQCt17Q/EubpGGSNKYn"
    "1tsTCPZxqHhHCfVURu7UA=="
)
PLAYWRIGHT_CORE_INTEGRITY = (
    "sha512-BC4s8y6SEVA6Q1rSxZTto8hI1KUI3xXlHGZgvGqP4IqPdI/a6FX9H29IDNz7vKo9"
    "l21b6LfYosScPL6M3xxyDQ=="
)
PLAYWRIGHT_TEST_INTEGRITY = (
    "sha512-IaYGml/nSOiZWROfPtuIL7neuu9qDOZ3TNvNljUIrhDMxiGTgPjzj/g9p9RxpS2t"
    "SepPDRY+UwAaEeiKtUvIAg=="
)

PLAYWRIGHT_ROOT = "/opt/basaltwater-playwright"
PLAYWRIGHT_STABLE_ROOT = "/opt/basaltwater-playwright-stable"
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
PLAYWRIGHT_MCP_CONFIG = os.path.join(PLAYWRIGHT_ROOT, "mcp-config.json")
PLAYWRIGHT_MCP_WRAPPER = "/usr/local/bin/basaltwater-playwright-mcp"
PLAYWRIGHT_DOCTOR_WRAPPER = "/usr/local/bin/basaltwater-playwright-doctor"
SYSTEM_NODE = SERVICE_NODE
SYSTEM_TIMEOUT = "/usr/bin/timeout"
PLAYWRIGHT_SMOKE_ACTION_TIMEOUT_MS = 120_000
PLAYWRIGHT_SMOKE_PROCESS_TIMEOUT_SECONDS = 180
PLAYWRIGHT_MCP_OUTPUT_MAX_BYTES = 256 * 1024 * 1024
PLAYWRIGHT_MCP_SETTLE_TIMEOUT_MS = 1_000

_BROWSER_EXECUTABLE_SCRIPT = (
    f'const {{ chromium }} = require("{PLAYWRIGHT_MODULE}"); '
    "process.stdout.write(chromium.executablePath());"
)
_BROWSER_EXECUTABLE_COMMAND = (
    f"{SYSTEM_NODE} -e {shlex.quote(_BROWSER_EXECUTABLE_SCRIPT)}"
)
_FIREFOX_EXECUTABLE_COMMAND = (
    f"{SYSTEM_NODE} -e "
    + shlex.quote(f'const {{ firefox }} = require("{PLAYWRIGHT_MODULE}"); process.stdout.write(firefox.executablePath());')
)


_MCP_WRAPPER_CONTENT = (
    "#!/bin/sh\n"
    "set -eu\n"
    "umask 077\n"
    "unset NODE_OPTIONS NODE_PATH\n"
    'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"\n'
    'export PLAYWRIGHT_SKIP_BROWSER_GC=1\n'
    'engine="chromium"\n'
    'if [ "${1-}" = "--engine" ]; then\n'
    '  engine="${2-}"\n'
    '  [ "$#" -ge 2 ] || exit 2\n'
    '  shift 2\n'
    'fi\n'
    'case "$engine" in\n'
    'chromium)\n'
    f'browser_path="$({_BROWSER_EXECUTABLE_COMMAND})"\n'
    f'config_path="{PLAYWRIGHT_MCP_CONFIG}"\n'
    'output_dir="$HOME/.local/state/basaltwater/playwright-mcp"\n'
    ';;\n'
    'firefox)\n'
    f'browser_path="$({_FIREFOX_EXECUTABLE_COMMAND})"\n'
    f'config_path="{PLAYWRIGHT_ROOT}/mcp-firefox-config.json"\n'
    'output_dir="$HOME/.local/state/basaltwater/playwright-mcp/firefox"\n'
    ';;\n'
    '*) echo "Unsupported managed browser engine: $engine" >&2; exit 2;;\n'
    'esac\n'
    'if [ ! -f "$browser_path" ] || [ ! -x "$browser_path" ]; then\n'
    '  echo "Managed Playwright $engine is not executable: $browser_path" >&2\n'
    "  exit 1\n"
    "fi\n"
    'mkdir -p "$output_dir"\n'
    'chmod 0700 "$output_dir"\n'
    f"exec {SYSTEM_NODE} {PLAYWRIGHT_MCP_CLI} --headless --isolated \\\n"
    '  --browser "$engine" --config "$config_path" \\\n'
    '  --executable-path "$browser_path" \\\n'
    "  --caps vision \\\n"
    '  --output-dir "$output_dir" \\\n'
    f"  --timeout-settle {PLAYWRIGHT_MCP_SETTLE_TIMEOUT_MS} \\\n"
    f'  --output-max-size {PLAYWRIGHT_MCP_OUTPUT_MAX_BYTES} "$@"\n'
)

_DOCTOR_WRAPPER_CONTENT = (
    "#!/bin/sh\n"
    "set -eu\n"
    "unset NODE_OPTIONS NODE_PATH\n"
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
    args: ['--disable-features=NativeNotifications'],
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


def _install_runtime_package(*, refresh: bool = False, stable: bool = False) -> bool:
    """Keep the stable test runner separate from MCP's required alpha runtime."""
    root = PLAYWRIGHT_STABLE_ROOT if stable else PLAYWRIGHT_ROOT
    _ensure_safe_root_path(root)
    verify_service_node()
    run(f"install -d -m 0755 -o root -g root {shlex.quote(root)}")

    package_path = os.path.join(
        root,
        "node_modules",
        "@playwright",
        "test" if stable else "mcp",
        "package.json",
    )
    lock_path = os.path.join(root, "package-lock.json")
    try:
        _verify_runtime_package(package_path, lock_path, stable=stable)
    except RuntimeError:
        pass
    else:
        if not refresh:
            print(f"  ✓ Playwright {'stable' if stable else 'MCP'} runtime already verified; skipping npm install")
            return False
    if refresh:
        print(f"  Refreshing the pinned Playwright {'stable' if stable else 'MCP'} runtime")
    if not refresh:
        print(f"  Repairing the missing or invalid Playwright {'stable' if stable else 'MCP'} runtime")

    package = f"@playwright/test@{PLAYWRIGHT_VERSION}" if stable else f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}"
    with tempfile.TemporaryDirectory(prefix="basaltwater-browser-npm-") as temporary:
        environment = node_environment()
        for kind in ("user", "global"):
            config_path = os.path.join(temporary, kind + ".npmrc")
            write_text_atomic(config_path, "", mode=0o600)
            environment[f"NPM_CONFIG_{kind.upper()}CONFIG"] = config_path
        run([SYSTEM_NODE, SERVICE_NPM_CLI, "install", "--ignore-scripts", "--no-audit", "--no-fund",
             "--save-exact", "--prefix", root, "--registry=https://registry.npmjs.org", package],
            env=environment, cwd=temporary)

    _verify_runtime_package(package_path, lock_path, stable=stable)

    run(f"chown -R root:root {shlex.quote(root)}")
    run(f"chmod -R go-w {shlex.quote(root)}")
    return True


def _verify_runtime_package(package_path: str, lock_path: str, *, stable: bool = False) -> None:
    """Verify every npm package whose code is executed during provisioning."""
    version = PLAYWRIGHT_VERSION if stable else PLAYWRIGHT_MCP_PLAYWRIGHT_VERSION
    expected_package_version = PLAYWRIGHT_VERSION if stable else PLAYWRIGHT_MCP_VERSION
    expected_lock_entries = {
        "node_modules/@playwright/" + ("test" if stable else "mcp"): (
            expected_package_version, PLAYWRIGHT_TEST_INTEGRITY if stable else PLAYWRIGHT_MCP_INTEGRITY,
        ),
        "node_modules/playwright": (version, PLAYWRIGHT_INTEGRITY if stable else PLAYWRIGHT_MCP_PLAYWRIGHT_INTEGRITY),
        "node_modules/playwright-core": (
            version,
            PLAYWRIGHT_CORE_INTEGRITY if stable else PLAYWRIGHT_MCP_CORE_INTEGRITY,
        ),
    }
    try:
        with open(package_path, encoding="utf-8") as file_obj:
            package = json.load(file_obj)
        with open(lock_path, encoding="utf-8") as file_obj:
            package_lock = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not verify the installed Playwright MCP package") from exc

    if not isinstance(package, dict) or package.get("version") != expected_package_version:
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
    firefox_config = os.path.join(os.path.dirname(PLAYWRIGHT_MCP_CONFIG), "mcp-firefox-config.json")
    for path in (
        PLAYWRIGHT_MCP_WRAPPER,
        PLAYWRIGHT_DOCTOR_WRAPPER,
        PLAYWRIGHT_SMOKE_SCRIPT,
        PLAYWRIGHT_MCP_CONFIG,
        firefox_config,
    ):
        _ensure_safe_root_path(path)

    # Publish the config before a newly launched MCP process can reference it.
    write_json_atomic(PLAYWRIGHT_MCP_CONFIG, {
        "browser": {"launchOptions": {"args": ["--disable-features=NativeNotifications"]}},
    }, mode=0o644)
    write_json_atomic(firefox_config, {"browser": {"browserName": "firefox"}}, mode=0o644)
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

    try:
        verify_service_node()
        _verify_runtime_package(os.path.join(PLAYWRIGHT_ROOT, "node_modules/@playwright/mcp/package.json"),
                                os.path.join(PLAYWRIGHT_ROOT, "package-lock.json"))
        _verify_runtime_package(os.path.join(PLAYWRIGHT_STABLE_ROOT, "node_modules/@playwright/test/package.json"),
                                os.path.join(PLAYWRIGHT_STABLE_ROOT, "package-lock.json"), stable=True)
    except (OSError, RuntimeError, ValueError):
        print("  Browser runtime migration required; rerun saved setup with --browser-automation playwright")
        return
    _write_launchers()
    print("  ✓ Existing Playwright browser launchers reconciled")


def _install_browser(config: SetupConfig, *, stable: bool = False) -> bool:
    """Install OS dependencies and a user-owned Chromium browser payload."""
    user_home = _user_home(config)
    version = PLAYWRIGHT_VERSION if stable else PLAYWRIGHT_MCP_PLAYWRIGHT_VERSION
    root = PLAYWRIGHT_STABLE_ROOT if stable else PLAYWRIGHT_ROOT
    cli = os.path.join(root, "node_modules/playwright/cli.js")
    script = f'const {{ chromium }} = require("{root}/node_modules/playwright"); process.stdout.write(chromium.executablePath());'
    executable_command = f"{SYSTEM_NODE} -e {shlex.quote(script)}"
    deps_marker = f"/var/lib/basaltwater/state/playwright-deps-{version}"
    deps_changed = config.refresh_packages or not os.path.exists(deps_marker)
    if deps_changed:
        run(f"{SYSTEM_NODE} {shlex.quote(cli)} install-deps chromium", env=node_environment())
        os.makedirs(os.path.dirname(deps_marker), mode=0o755, exist_ok=True)
        with open(deps_marker, "w", encoding="utf-8") as marker:
            marker.write("basaltwater Playwright OS dependencies installed\n")
        os.chmod(deps_marker, 0o644)
    else:
        print("  ✓ Playwright OS dependencies already installed; skipping apt transaction")

    browser_marker = (
        f"$HOME/.cache/ms-playwright/.basaltwater-chromium-"
        f"{version}.installed"
    )
    browser_ready = not config.refresh_packages and _run_as_login_user(
        config.username,
        user_home,
        'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" && '
        'export PLAYWRIGHT_SKIP_BROWSER_GC=1 && unset NODE_OPTIONS NODE_PATH && '
        f'test -f "{browser_marker}" && '
        f'test -f "$({executable_command})" && '
        f'test -x "$({executable_command})"',
        check=False,
    ).returncode == 0
    if browser_ready:
        print("  ✓ Playwright Chromium already installed; skipping browser download")
        return deps_changed

    _run_as_login_user(
        config.username,
        user_home,
        "export PLAYWRIGHT_BROWSERS_PATH=\"$HOME/.cache/ms-playwright\" && "
        'export PLAYWRIGHT_SKIP_BROWSER_GC=1 && unset NODE_OPTIONS NODE_PATH && '
        f"{SYSTEM_NODE} {shlex.quote(cli)} install chromium && "
        f'test -f "$({executable_command})" && '
        f'test -x "$({executable_command})" && '
        f"mkdir -p \"$HOME/.cache/ms-playwright\" && "
        f"printf '%s\\n' {shlex.quote(version)} > \"{browser_marker}\"",
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
        print("  [DRY-RUN] Would install verified service Node, stable Playwright tests, MCP and Chromium")
        print("  [DRY-RUN] Would register browser automation for selected agents")
        return

    install_service_node()
    _install_runtime_package(refresh=config.refresh_packages)
    _install_runtime_package(refresh=config.refresh_packages, stable=True)
    _write_launchers()
    _install_browser(config)
    _install_browser(config, stable=True)
    reconcile_agent_workflow_skills(config)

    selected_tools = set(config.selected_agent_tools())
    if "codex" in selected_tools:
        _configure_codex(config)
    if "opencode" in selected_tools:
        _configure_opencode(config)
    _run_smoke_test(config)

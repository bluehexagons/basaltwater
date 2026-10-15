#!/usr/bin/env python3
"""Run intensive first-party Chromium/Firefox checks on a development machine."""

from __future__ import annotations

import argparse
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
from lib.guacamole_config import RELEASE
from lib.remote_utils import run
from lib.validation import validate_filesystem_path

# all.js extracted from the checksum-verified 1.6.0-bw2 release archive.
GUACAMOLE_JS_SHA256 = "3a6126a9487ea2e37a1684613b54335c2d32e35d2e33286e719700154701f562"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("chromium", "firefox"), action="append", help="Default: both engines")
    parser.add_argument("--context", choices=("managed", "project"), default="managed")
    parser.add_argument("--project", help="Existing locked Playwright package directory")
    parser.add_argument("--guacamole-assets", type=Path, help="Existing pinned Guacamole share/all.js")
    parser.add_argument("--require-guacamole", action="store_true", help="Fail instead of reporting missing integration prerequisites")
    args = parser.parse_args(argv)
    if os.environ.get("CI"):
        parser.error("Intensive browser checks run on development machines, outside CI")
    try:
        runtime = _runtime(args.context, args.project)
        if not runtime or runtime["version"] != PLAYWRIGHT_VERSION:
            raise ValueError(f"Install the repository-pinned Playwright {PLAYWRIGHT_VERSION} explicitly; no packages are installed by this command")
        package = json.loads((Path(runtime["module"]) / "package.json").read_text())
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
                                        "BASALTW_PLAYWRIGHT_MODULE": runtime["module"], "NODE_EXTRA_CA_CERTS": str(directory / "ca.crt")})
                    engines = list(dict.fromkeys(args.engine or ("chromium", "firefox")))
                    result = run([node, runtime["cli"], "test", "--config", str(ROOT / "tests/browser/playwright.config.cjs"),
                                  *[f"--project={engine}" for engine in engines]],
                                 check=False, env=environment, cwd=ROOT, timeout=1800)
                    (output / "summary.json").write_text(json.dumps({"engines": engines, "playwright_version": runtime["version"],
                        "context": args.context, "network_origin": "host", "workers": 1, "exit_code": result.returncode,
                        "first_party": "see results.json for individual outcomes",
                        "guacamole_boundary": "disposable protocol fixture" if asset else "not tested: pinned assets missing",
                        "native_esr": "not tested: separate release qualification", "project_ui": "out of scope"}, indent=2) + "\n")
                    return result.returncode
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

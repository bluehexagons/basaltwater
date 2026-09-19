"""Opt-in live Geany check using the managed desktop's ordinary operations."""

from __future__ import annotations

from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
from typing import Any

from desktop import client, session_runtime as runtime
from lib.validation import validate_filesystem_path


def run_smoke_check(generation: str) -> dict[str, Any]:
    """Verify editing, saving and a scoped dialog; retain failures for inspection."""
    if shutil.which("geany") is None:
        raise RuntimeError("Geany is required for the desktop smoke check; it ships in agent_code_vm")
    report: dict[str, Any] = {"generation": generation, "completed": []}
    stage = "prepare"

    def mutate(action: str, **fields: Any) -> dict[str, Any]:
        lease = runtime.request({"action": "acquire", "generation": generation})["lease"]
        try:
            return runtime.request({"action": action, "generation": generation, "lease": lease, **fields})
        finally:
            try:
                runtime.request({"action": "release", "generation": generation, "lease": lease})
            except (OSError, ValueError, RuntimeError):
                pass  # Pause or session loss may have revoked control.

    def observe(**selectors: Any) -> dict[str, Any]:
        result = client.wait_for_element(generation, pid=report["pid"], **selectors)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["elements"][0]

    def click(**selectors: Any) -> None:
        row = observe(state="enabled", **selectors)
        if "click" not in row["actions"]:
            raise RuntimeError("Expected control does not advertise a click action")
        mutate("element", operation="invoke", ref=row["ref"], action_name="click")

    try:
        directory = Path(tempfile.mkdtemp(prefix="basaltwater-desktop-smoke-"))
        report["directory"] = str(directory)
        document = directory / "check.txt"
        validate_filesystem_path(str(document))
        seed = f"Desktop smoke {secrets.token_hex(8)}\n"
        expected = seed + "Edited: café 日本語\n"
        document.write_text(seed, encoding="utf-8")
        (directory / "expected.txt").write_text(expected, encoding="utf-8")
        stage = "launch"
        launched = mutate("exec", argv=["env", "LC_ALL=C.UTF-8", "LANGUAGE=C", "geany",
                          "--new-instance", "--no-session", "--config", str(directory / "profile"),
                          str(document)], cwd=str(directory))
        report.update(pid=launched["pid"], launch=launched["launch"])
        ready = client.wait_for_window(generation, pid=report["pid"], title=str(directory),
                                       launch=report["launch"])
        if len(ready["windows"]) != 1:
            raise RuntimeError("Expected exactly one window belonging to the smoke check")
        report["window"] = ready["windows"][0]["id"]
        report["completed"].append(stage)

        stage = "edit"
        editor = observe(role="text", text=seed)
        mutate("element", operation="set-text", ref=editor["ref"], text=expected)
        observe(role="text", text=expected)
        report["completed"].append(stage)
        stage = "save"
        click(name="Save", role="button")
        deadline = time.monotonic() + 15
        while document.read_bytes() != expected.encode("utf-8"):
            if time.monotonic() >= deadline:
                raise RuntimeError("Saved file did not match the expected UTF-8 bytes")
            time.sleep(0.1)
        report["completed"].append(stage)

        stage = "dialog"
        click(name="Search", role="menu")
        click(name="Find...", role="menu item")
        root = observe(name="Find", role="dialog")["ref"]
        field = observe(root=root, role="text", state="focused")
        query = "scoped café 日本語"
        mutate("element", operation="set-text", ref=field["ref"], text=query)
        observe(root=root, role="text", text=query)
        click(root=root, name="Close", role="button")
        absent = client.wait_for_element(generation, pid=report["pid"], name="Find",
                                         role="dialog", state="absent")
        if "error" in absent:
            raise RuntimeError(absent["error"])
        report["completed"].append(stage)

        stage = "close"
        current = client.wait_for_window(generation, window=report["window"], pid=report["pid"],
                                         title=str(directory))
        window = current["windows"][0]
        mutate("window", operation="close", window=window["id"], identity=window["identity"])
        client.wait_for_window(generation, pid=report["pid"], condition="absent")
        report["completed"].append(stage)
        stage = "cleanup"
        shutil.rmtree(directory)
        report.update(passed=True, artifacts_removed=True)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        report.update(passed=False, stage=stage, error=str(exc), artifacts_removed=False,
                      recovery="Inspect the reported test instance and directory; no automatic retry or forced cleanup was attempted")
    return report

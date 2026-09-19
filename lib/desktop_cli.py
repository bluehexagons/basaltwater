"""CLI for shared desktop lifecycle and bounded native application control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from desktop import session_runtime as runtime
from desktop import client
from lib.validation import validate_filesystem_path


def add_desktop_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("desktop", help="Start and use the shared desktop as its owner")
    commands = parser.add_subparsers(dest="desktop_command", required=True)
    for name in ("status", "start", "logout", "windows", "doctor", "handoff"):
        commands.add_parser(name).add_argument("--json", action="store_true")
    commands.add_parser("smoke", help="Live Geany edit/save/dialog check in an isolated test instance")
    screenshot = commands.add_parser("screenshot")
    screenshot.add_argument("--output", help="New PNG path; defaults to private Pictures/basaltwater artifact storage")
    screenshot.add_argument("--json", action="store_true")
    target = screenshot.add_mutually_exclusive_group()
    target.add_argument("--window", help="Capture a window ID from 'desktop windows'")
    target.add_argument("--active-window", action="store_true", help="Capture the active application without changing focus")
    execute = commands.add_parser("exec")
    execute.add_argument("--wait-window", help="Wait for a visible window with this literal title substring")
    execute.add_argument("--timeout", type=float, default=15)
    execute.add_argument("argv", nargs=argparse.REMAINDER)
    opening = commands.add_parser("open", help="Open a local document with its default application")
    opening.add_argument("path")
    opening.add_argument("--reveal", action="store_true", help="Open its parent directory")
    opening.add_argument("--wait-window")
    opening.add_argument("--timeout", type=float, default=15)
    window = commands.add_parser("window", help="Request a normal window-manager action")
    window.add_argument("operation", choices=("focus", "move", "resize", "maximize", "minimize", "restore", "close"))
    window.add_argument("--window", required=True)
    window.add_argument("--identity", required=True, help="Window identity from 'desktop windows'")
    window.add_argument("--generation", required=True)
    for name in ("x", "y", "width", "height"):
        window.add_argument(f"--{name}", type=int)
    waiting = commands.add_parser("wait", help="Wait for an observed window condition without holding control")
    waiting.add_argument("--window")
    waiting.add_argument("--title")
    waiting.add_argument("--pid", type=int)
    waiting.add_argument("--condition", choices=("present", "visible", "absent", "active"), default="visible")
    waiting.add_argument("--timeout", type=float, default=15)
    waiting.add_argument("--generation")
    launch = commands.add_parser("launch-status")
    launch.add_argument("launch")
    launch.add_argument("--generation", required=True)
    sequence = commands.add_parser("sequence", help="Run up to 20 short JSON actions under one revocable lease")
    sequence.add_argument("path")
    sequence.add_argument("--generation", required=True)
    control = commands.add_parser("control")
    control.add_argument("operation", choices=("pause", "resume"))
    inspect = commands.add_parser("inspect", help="Read a bounded AT-SPI tree for one application")
    inspect.add_argument("--pid", required=True, type=int)
    inspect.add_argument("--name", help="Exact accessible name")
    inspect.add_argument("--role", help="Exact accessible role")
    inspect.add_argument("--root", help="Inspect only this observed element and its descendants")
    inspect.add_argument("--generation", help="Required with --root; use the observed session generation")
    element = commands.add_parser("element", help="Act on a recent accessibility reference")
    element.add_argument("operation", choices=("invoke", "set-text", "focus"))
    element.add_argument("--ref", required=True)
    element.add_argument("--generation", required=True)
    element.add_argument("--action-name")
    element.add_argument("--text")
    semantic_wait = commands.add_parser("wait-element", help="Wait for a uniquely matched accessible control")
    semantic_wait.add_argument("--pid", required=True, type=int)
    semantic_wait.add_argument("--name")
    semantic_wait.add_argument("--role")
    semantic_wait.add_argument("--root", help="Wait within this observed subtree")
    semantic_wait.add_argument("--state", choices=("present", "absent", "enabled", "showing", "focused"), default="present")
    semantic_wait.add_argument("--text", help="Exact complete text to wait for")
    semantic_wait.add_argument("--timeout", type=float, default=15)
    semantic_wait.add_argument("--generation", required=True)
    action = commands.add_parser("input", help="Apply a bounded input using screenshot generation/geometry")
    action.add_argument("--generation", required=True)
    action.add_argument("--geometry", required=True, nargs=2, type=int, metavar=("WIDTH", "HEIGHT"))
    action.add_argument("kind", choices=("key", "text", "click", "move"))
    action.add_argument("--key")
    action.add_argument("--text")
    action.add_argument("--x", type=int)
    action.add_argument("--y", type=int)
    action.add_argument("--button", type=int, default=1)


def run_desktop_command(args: argparse.Namespace) -> int:
    try:
        command = args.desktop_command
        if command == "status":
            result = runtime.status()
        elif command == "start":
            result = runtime.start()
        elif command == "control":
            result = runtime.request({"action": args.operation})
        elif command == "doctor":
            result = client.doctor()
        else:
            current = runtime.status()
            if current["state"] != "running":
                state = current["state"]
                if state == "stopped":
                    raise RuntimeError("Desktop is stopped; run 'basaltw desktop start' first")
                if state == "starting":
                    raise RuntimeError("Desktop is starting; run 'basaltw desktop start' to wait for readiness")
                raise RuntimeError(f"Desktop is {state}; inspect 'basaltw desktop status' before retrying")
            payload = {"action": command, "generation": current["generation"]}
            wait_title = getattr(args, "wait_window", None)
            baseline = []
            if wait_title is not None:
                if not wait_title or len(wait_title) > 512:
                    raise ValueError("Window title must be a nonempty string of at most 512 characters")
                if not 0 < args.timeout <= 120:
                    raise ValueError("Wait timeout must be greater than zero and at most 120 seconds")
                baseline = runtime.request({"action": "windows", "generation": current["generation"]})["windows"]
            if command == "smoke":
                from desktop.smoke import run_smoke_check
                result = run_smoke_check(current["generation"])
            elif command == "windows":
                result = runtime.request(payload)
            elif command == "inspect":
                if args.root is not None and args.generation is None:
                    raise ValueError("Scoped inspection requires --generation from the observation")
                result = runtime.request({**payload, "generation": args.generation or current["generation"],
                    **{name: getattr(args, name) for name in ("pid", "name", "role", "root")}})
            elif command == "wait-element":
                result = client.wait_for_element(args.generation,
                    **{name: getattr(args, name) for name in ("pid", "name", "role", "state", "text", "timeout", "root")})
            elif command == "launch-status":
                result = runtime.request({**payload, "launch": args.launch, "generation": args.generation})
            elif command == "wait":
                result = client.wait_for_window(args.generation or current["generation"],
                    **{name: getattr(args, name) for name in ("window", "title", "pid", "condition", "timeout")})
            elif command == "sequence":
                result = client.run_sequence(args.path, args.generation)
            elif command == "screenshot":
                payload["output"] = str(Path(args.output).absolute()) if args.output else client.artifact_path()
                payload["window"] = args.window
                payload["active_window"] = args.active_window
                result = runtime.request(payload)
            else:
                if command == "exec":
                    payload["argv"] = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
                    payload["cwd"] = str(Path.cwd())
                elif command == "open":
                    path = Path(args.path).absolute()
                    validate_filesystem_path(str(path), must_exist=True)
                    payload.update(action="exec", argv=["xdg-open", str(path.parent if args.reveal else path)], cwd=str(Path.cwd()))
                elif command == "handoff":
                    payload.update(action="exec", argv=["/usr/bin/python3", str(Path(runtime.__file__).with_name("handoff.py"))])
                elif command == "window":
                    payload.update({name: getattr(args, name) for name in
                                    ("generation", "identity", "window", "operation", "x", "y", "width", "height")})
                elif command == "element":
                    payload.update({name: getattr(args, name) for name in
                                    ("generation", "operation", "ref", "action_name", "text")})
                elif command == "input":
                    payload.update({name: getattr(args, name) for name in ("generation", "geometry", "kind", "key", "text", "x", "y", "button")})
                lease = runtime.request({"action": "acquire", "generation": payload["generation"]})
                payload["lease"] = lease["lease"]
                try:
                    result = runtime.request(payload)
                finally:
                    try:
                        runtime.request({"action": "release", "generation": payload["generation"], "lease": lease["lease"]})
                    except (OSError, RuntimeError):
                        pass  # Logout or human takeover may have revoked the lease.
                if wait_title:
                    try:
                        observed = client.wait_for_window(current["generation"], title=wait_title,
                                                          timeout=args.timeout, launch=result["launch"])
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                        result["error"] = str(exc)  # Preserve launch identity for inspection after timeout.
                    else:
                        previous = {item["id"] for item in baseline}
                        result.update(windows=observed["windows"], launch_status=observed["launch_status"],
                                      existing_window_ids=[item["id"] for item in observed["windows"] if item["id"] in previous],
                                      window_association="title substring; inspect PID/class before acting")
        print(json.dumps(result, indent=2))
        return 1 if "error" in result or result.get("healthy") is False else 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

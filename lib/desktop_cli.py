"""CLI for shared desktop lifecycle and bounded native application control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from desktop import session_runtime as runtime
from desktop import client
from lib.validation import validate_filesystem_path


class _NativeBackendAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, True)
        screenshot = parser.native_screenshot_parser
        screenshot.description = "KDE Wayland: capture the consent-selected monitor. Window capture is unavailable."
        for action in screenshot._actions:
            if action.dest in ("window", "active_window"):
                action.help = argparse.SUPPRESS


def add_desktop_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("desktop", help="Use the shared Debian desktop or opt in to native CachyOS automation")
    parser.add_argument("--native", action=_NativeBackendAction, nargs=0, default=False,
                        help="Use KDE Wayland portal automation on CachyOS; start requests user consent")
    commands = parser.add_subparsers(dest="desktop_command", required=True)
    preflight = commands.add_parser("preflight", help="Read-only compact manifest/provenance/status bundle with explicit gaps")
    preflight.add_argument("--repository", default=".")
    preflight.add_argument("--managed-source")
    preflight.add_argument("--instruction", action="append", default=[], help="Relevant required file; repeat up to 16, return retrievable range checklist")
    preflight.add_argument("--output", help="New private evidence directory outside Git")
    preflight.add_argument("--json", action="store_true")
    instructions = commands.add_parser("instructions", help="Read at most 4000 Unicode characters with explicit remaining ranges")
    instructions.add_argument("path")
    instructions.add_argument("--start", type=int, default=0)
    instructions.add_argument("--characters", type=int, default=4000)
    instructions.add_argument("--sha256", help="Refuse a changed file when continuing a bounded read")
    instructions.add_argument("--json", action="store_true")
    from desktop.development import add_development_parser
    add_development_parser(commands)
    evidence = commands.add_parser("evidence", help="Native: record manual launches, inputs and captures with project/task provenance")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    from desktop.qa_cli import add_qa_parser
    add_qa_parser(commands, evidence_commands)
    from desktop.accessibility_recording import add_recording_parser
    add_recording_parser(commands)
    recording = evidence_commands.add_parser("start")
    recording.add_argument("--project", required=True)
    finishing = evidence_commands.add_parser("finish")
    finalizing = evidence_commands.add_parser("finalize", help="Finalize a saved recording after helper exit or interrupted collection")
    finalizing.add_argument("artifact")
    verifying = evidence_commands.add_parser("verify", help="Check recorded capture files without contacting the desktop or modifying evidence")
    verifying.add_argument("artifact")
    verifying.add_argument("--retention", nargs=2, action="append", default=[], metavar=("RECEIPT", "SHA256"),
                           help="Explicitly pinned wait receipt for intentional ring evictions; repeat up to 16")
    verifying.add_argument("--json", action="store_true")
    for command in (finishing, finalizing):
        command.add_argument("--task", action="append", default=[], help="Also attach a pre-existing development task; repeat as needed")
        command.add_argument("--renderer", help="Renderer/device observed in the application's log; recorded as operator-observed")
    for command in (recording, finishing, finalizing):
        command.add_argument("--json", action="store_true")
    for name in ("status", "stop", "logout", "windows", "doctor", "handoff", "revoke"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true")
        if name == "windows":
            command.add_argument("--save-output", action="store_true", help="Save full JSON privately and print a bounded preview")
    wake = commands.add_parser("wake", help="Native KDE: wake sleeping displays without keyboard/pointer input; requires enabled agent control")
    wake.add_argument("--json", action="store_true")
    starting = commands.add_parser("start")
    starting.add_argument("--json", action="store_true")
    starting.add_argument("--remember", action="store_true", help="Native opt-in to a reusable KDE grant; initial approval remains required")
    starting.add_argument("--session-seconds", type=int, default=0, help="Optional native lifetime, 60–28800 seconds; 0 (default) is unlimited")
    renewal = commands.add_parser("renew", help="Change an optional native backend lifetime without changing agent permission")
    renewal.add_argument("--generation", required=True)
    renewal.add_argument("--seconds", type=int, default=0)
    renewal.add_argument("--json", action="store_true")
    commands.add_parser("smoke", help="Live Geany edit/save/dialog check in an isolated test instance")
    screenshot = commands.add_parser("screenshot", description="Debian: capture the desktop or a selected window. With --native: selected monitor only.")
    parser.native_screenshot_parser = screenshot
    screenshot.add_argument("--output", help="New PNG path; defaults to private Pictures/basaltwater artifact storage")
    screenshot.add_argument("--json", action="store_true")
    screenshot.add_argument("--compact", action="store_true", help="Return capture metadata without the status envelope; use status/doctor for diagnostics")
    screenshot.add_argument("--max-age-seconds", type=int, default=60,
                            help="Observation lifetime, 1–600 seconds (default: 60); longer ages suit stable, untimed scenes")
    screenshot.add_argument("--next-frame", action="store_true", help="Native: skip the initial PipeWire sample and await a subsequent sample (8-second frame budget)")
    screenshot.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "WIDTH", "HEIGHT"), help="Native: crop selected-monitor pixels and return image_origin for coordinate mapping")
    target = screenshot.add_mutually_exclusive_group()
    target.add_argument("--window", help="Capture a window ID from 'desktop windows'")
    target.add_argument("--active-window", action="store_true", help="Capture the active application without changing focus")
    mapping = commands.add_parser("map-point", help="Native: convert presented-image points to monitor pixels without sending input")
    mapping.add_argument("--geometry", nargs=2, type=int, metavar=("WIDTH", "HEIGHT"),
                         help="Full-monitor geometry from the screenshot metadata")
    mapping.add_argument("--view-geometry", required=True, nargs=2, type=int, metavar=("WIDTH", "HEIGHT"),
                         help="Dimensions of the entire PNG as presented, excluding viewer margins")
    mapping.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "WIDTH", "HEIGHT"),
                         help="Same monitor-pixel crop used for the screenshot; omit for a full capture")
    mapping.add_argument("--x", required=True, type=int, help="Image-local x in the presented view")
    mapping.add_argument("--y", required=True, type=int, help="Image-local y in the presented view")
    mapping.add_argument("--to-x", type=int, help="Optional drag endpoint x in the same view")
    mapping.add_argument("--to-y", type=int, help="Optional drag endpoint y in the same view")
    mapping.add_argument("--json", action="store_true")
    mapping.add_argument("--capture", help="Saved screenshot JSON receipt; checks its token, geometry and age against status without capturing")
    mapping.add_argument("--view-origin", nargs=2, type=int, metavar=("LEFT", "TOP"),
                         help="Presented image content origin inside a viewer; points include these margins (requires --capture)")
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
    waiting.add_argument("--save-output", action="store_true", help="Save full JSON privately and print a bounded preview")
    for waiter in (execute, opening, waiting):
        waiter.add_argument("--exclude-title", dest="exclude_titles", action="append", metavar="TEXT",
                            help="Ignore titles containing this literal substring; repeat up to 16 times")
        waiter.add_argument("--stable-seconds", type=float, default=0, metavar="SECONDS",
                            help="Require the same matching windows for 0–5 seconds (default: 0)")
    launch = commands.add_parser("launch-status")
    launch.add_argument("launch")
    launch.add_argument("--generation", required=True)
    sequence = commands.add_parser("sequence", help="Run up to 20 short JSON actions while agent control remains enabled")
    sequence.add_argument("path")
    sequence.add_argument("--generation", required=True)
    control = commands.add_parser("control")
    control.add_argument("operation", choices=("enabled", "human-override", "disabled"))
    control.add_argument("--human-approved", action="store_true", help="Confirm explicit human approval for this state change; a desktop task alone is insufficient")
    duration = control.add_mutually_exclusive_group()
    duration.add_argument("--seconds", type=int, default=900, help="Human override duration, 60–86400 seconds (default: 900)")
    duration.add_argument("--indefinite", action="store_true", help="Keep human override until explicitly changed")
    inspect = commands.add_parser("inspect", help="Read a bounded AT-SPI tree for one application")
    inspect.add_argument("--pid", required=True, type=int)
    inspect.add_argument("--name", help="Exact accessible name")
    inspect.add_argument("--role", help="Exact accessible role")
    inspect.add_argument("--root", help="Inspect only this observed element and its descendants")
    inspect.add_argument("--generation", help="Required with --root; use the observed session generation")
    inspect.add_argument("--save-output", action="store_true", help="Save full JSON privately and print a bounded preview")
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
    semantic_wait.add_argument("--save-output", action="store_true", help="Save full JSON privately and print a bounded preview")
    action = commands.add_parser("input", help="Apply a bounded input using screenshot generation/geometry")
    action.add_argument("--generation", required=True)
    action.add_argument("--observation", help="Bind input to the latest screenshot's single-use observation_id")
    action.add_argument("--geometry", required=True, nargs=2, type=int, metavar=("WIDTH", "HEIGHT"))
    action.add_argument("kind", choices=("key", "keycode", "text", "click", "move", "drag"))
    action.add_argument("--key")
    action.add_argument("--codes", nargs="+", type=int, help="Native physical chord: 1–8 distinct Linux evdev keyboard codes (1–255), e.g. 29 30 for Ctrl+A; no XKB +8 offset")
    action.add_argument("--text")
    action.add_argument("--delay-ms", type=int, help="Pace text characters by 1–100 ms (20-second budget); useful for Blender's console")
    action.add_argument("--hold-ms", type=int, help="Native key/button press duration, 0–5000 ms; e.g. 120 for game buttons")
    action.add_argument("--x", type=int)
    action.add_argument("--y", type=int)
    action.add_argument("--to-x", type=int, help="Native drag endpoint in full-monitor capture pixels")
    action.add_argument("--to-y", type=int)
    action.add_argument("--duration-ms", type=int, help="Native drag duration, 1–5000 ms (default: 300)")
    action.add_argument("--button", type=int, choices=range(1, 8), default=1,
                        help="Integer: 1 left, 2 middle, 3 right, 4/5 vertical wheel, 6/7 horizontal wheel; drag uses 1–3")
    action.add_argument("--observe", action="store_true", help="After completed input, settle and capture again; native skips the initial sample; never replay on capture failure")
    action.add_argument("--settle-ms", type=int, default=120, help="Settling delay for --observe, 0–5000 ms (default: 120)")
    action.add_argument("--output", help="New PNG path for --observe; defaults to private artifact storage")


def run_desktop_command(args: argparse.Namespace) -> int:
    if args.desktop_command == "develop":
        from desktop.development import run_development_command
        return run_development_command(args)
    backend = runtime
    if getattr(args, "native", False):
        from desktop import native_session
        backend = native_session
    try:
        command = args.desktop_command
        control_command = "basaltw desktop --native" if backend is not runtime else "basaltw desktop"
        if command == "preflight":
            from desktop.preflight import preflight
            result = preflight(args.repository, backend=backend, native=backend is not runtime,
                               managed_source=args.managed_source, instructions=args.instruction, output=args.output)
        elif command == "instructions":
            from desktop.preflight import instruction_page
            result = instruction_page(args.path, start=args.start, characters=args.characters, sha256=args.sha256)
        elif command == "evidence" and args.evidence_command in ("run", "closeout", "records", "corpus", "perception", "conditional"):
            from desktop.qa_cli import run_qa_command
            result = run_qa_command(args, backend=backend)
        elif command == "accessibility-record":
            from desktop.accessibility_recording import run_recording_command
            result = run_recording_command(args)
        elif command == "audio" or (command == "evidence" and args.evidence_command in ("crop", "excerpt", "export", "timeline")):
            if backend is runtime:
                raise ValueError("Native QA tools require --native")
            from desktop.qa_cli import run_qa_command
            result = run_qa_command(args)
        elif command == "map-point":
            if backend is runtime:
                raise ValueError("Selected-monitor coordinate mapping requires --native")
            from desktop.coordinates import map_capture_points, map_observation_points
            if args.capture:
                from lib.atomic_io import read_json_file
                validate_filesystem_path(args.capture, must_exist=True)
                capture = read_json_file(args.capture, max_bytes=65536)
                if not isinstance(capture, dict):
                    raise ValueError("Expected a screenshot JSON receipt")
                if args.geometry is not None or args.crop is not None:
                    raise ValueError("--capture supplies geometry/crop; omit --geometry and --crop")
                result = map_observation_points(capture, backend.status(), args.view_geometry, args.x, args.y,
                    view_origin=args.view_origin, to_x=args.to_x, to_y=args.to_y)
            else:
                if args.view_origin is not None:
                    raise ValueError("--view-origin requires --capture")
                result = map_capture_points(args.geometry, args.view_geometry, args.x, args.y,
                                            crop=args.crop, to_x=args.to_x, to_y=args.to_y)
        elif command == "evidence":
            if backend is runtime:
                raise ValueError("Manual desktop evidence requires --native")
            from desktop.evidence import finalize, verify
            operation = args.evidence_command
            if operation == "verify":
                result = verify(args.artifact, retention=args.retention)
            elif operation == "finalize":
                result = finalize(args.artifact, tasks=args.task, renderer=args.renderer)
            else:
                current = backend.status()
                payload = {"action": "evidence", "operation": operation, "generation": current.get("generation")}
                if operation == "start":
                    payload["project"] = str(Path(args.project).expanduser().resolve())
                result = backend.request(payload)
                if operation == "finish":
                    artifact = result["artifact"]
                    try:
                        result = finalize(artifact, tasks=args.task, renderer=args.renderer)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                        result = {**result, "error": f"Recording saved; finalization failed: {exc}", "artifact": artifact}
        elif command == "status":
            result = backend.status()
        elif command == "wake":
            if backend is runtime:
                raise ValueError("Display wake requires --native in the existing KDE Wayland session")
            result = backend.wake()
        elif command == "handoff" and backend is not runtime:
            backend.check_session()
            process = subprocess.Popen(
                ["/usr/bin/python3", str(Path(backend.__file__).with_name("native_handoff.py"))],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            result = {"pid": process.pid, "control_window_requested": True, "desktop_preserved": True}
        elif command == "start":
            if backend is runtime:
                if args.remember or args.session_seconds != 0:
                    raise ValueError("Persistent grants and session lifetime require --native")
                result = backend.start()
            else:
                result = backend.start(remember=args.remember, session_seconds=args.session_seconds)
        elif command == "revoke":
            if backend is runtime:
                raise ValueError("Portal grant revocation requires --native")
            result = backend.revoke()
        elif command == "renew":
            if backend is runtime:
                raise ValueError("Portal session renewal requires --native")
            backend.validate_session_seconds(args.seconds)
            current = backend.status()
            result = backend.request({"action": "renew", "generation": args.generation, "seconds": args.seconds,
                                      "control_revision": current["control_revision"]})
        elif command == "stop":
            if backend is runtime:
                raise RuntimeError("Stop closes a native portal session; use --native stop, or logout for a Debian desktop")
            result = backend.request({"action": "stop"})
        elif command == "control":
            if args.operation != "human-override" and (args.indefinite or args.seconds != 900):
                raise ValueError("Duration applies only to human-override")
            result = backend.set_control(args.operation, seconds=None if args.indefinite else args.seconds,
                                         human_approved=args.human_approved)
        elif command == "doctor":
            result = backend.doctor() if backend is not runtime else client.doctor()
        else:
            current = backend.status()
            if current["state"] != "running":
                state = current["state"]
                if state == "stopped":
                    raise RuntimeError(f"Desktop is stopped; run '{control_command} start' first")
                if state == "starting":
                    raise RuntimeError(f"Desktop is starting; run '{control_command} start' to wait for readiness")
                raise RuntimeError(f"Desktop is {state}; inspect '{control_command} status' before retrying")
            payload = {"action": command, "generation": current["generation"],
                       "control_revision": current["control_revision"]}
            wait_title = getattr(args, "wait_window", None)
            baseline = None
            exclude_titles = getattr(args, "exclude_titles", None)
            stable_seconds = getattr(args, "stable_seconds", 0)
            if command in ("exec", "open", "wait"):
                if command != "wait" and wait_title is None and (exclude_titles or stable_seconds):
                    raise ValueError("Window filters and stability require --wait-window")
                if command == "wait" or wait_title is not None:
                    client.validate_window_wait_options(timeout=args.timeout, exclude_titles=exclude_titles,
                                                        stable_seconds=stable_seconds)
            if wait_title is not None:
                if not wait_title or len(wait_title) > 512:
                    raise ValueError("Window title must be a nonempty string of at most 512 characters")
                if not 0 < args.timeout <= 120:
                    raise ValueError("Wait timeout must be greater than zero and at most 120 seconds")
                try:
                    inventory = backend.request({"action": "windows", "generation": current["generation"]})
                except RuntimeError as exc:
                    baseline = {"complete": False, "observed_window_ids": None, "detail": str(exc)[:500]}
                else:
                    if inventory.get("generation") != current["generation"]:
                        raise ValueError("Desktop session changed before application launch; inspect status")
                    unavailable = inventory.get("coverage") == "unavailable"
                    baseline = {"complete": inventory.get("truncated") is False and not unavailable,
                                "observed_window_ids": None if unavailable else [item["id"] for item in inventory["windows"]]}
                    if unavailable:
                        baseline["detail"] = inventory.get("detail", "Window inventory unavailable")
                latest = backend.status()
                if latest.get("state") != "running" or latest.get("generation") != current["generation"]:
                    raise ValueError("Desktop session changed before application launch; inspect status")
                if latest.get("control_revision") != current["control_revision"]:
                    raise ValueError("Desktop control changed before application launch; inspect status")
            if command == "smoke":
                if backend is not runtime:
                    raise RuntimeError("The Geany XRDP smoke check is Debian-only; use native application edit/save/reopen checks")
                from desktop.smoke import run_smoke_check
                result = run_smoke_check(current["generation"])
            elif command == "windows":
                result = backend.request(payload)
            elif command == "inspect":
                if args.root is not None and args.generation is None:
                    raise ValueError("Scoped inspection requires --generation from the observation")
                result = backend.request({**payload, "generation": args.generation or current["generation"],
                    **{name: getattr(args, name) for name in ("pid", "name", "role", "root")}})
            elif command == "wait-element":
                result = client.wait_for_element(args.generation, backend=backend,
                    **{name: getattr(args, name) for name in ("pid", "name", "role", "state", "text", "timeout", "root")})
            elif command == "launch-status":
                result = backend.request({**payload, "launch": args.launch, "generation": args.generation})
            elif command == "wait":
                result = client.wait_for_window(args.generation or current["generation"], backend=backend,
                    exclude_titles=exclude_titles, stable_seconds=stable_seconds,
                    **{name: getattr(args, name) for name in ("window", "title", "pid", "condition", "timeout")})
            elif command == "sequence":
                result = client.run_sequence(args.path, args.generation, backend=backend)
            elif command == "screenshot":
                from desktop.observations import validate_capture_max_age
                validate_capture_max_age(args.max_age_seconds)
                if args.max_age_seconds != 60:
                    payload["max_age_seconds"] = args.max_age_seconds
                if args.crop is not None:
                    payload["crop"] = args.crop
                    client.validate_capture_crop(payload, backend=backend)
                if args.next_frame and backend is runtime:
                    raise ValueError("Subsequent-frame capture requires --native")
                payload["output"] = str(Path(args.output).absolute()) if args.output else client.artifact_path()
                payload["window"] = args.window
                payload["active_window"] = args.active_window
                if args.next_frame:
                    payload["next_frame"] = True
                result = client.send_action(payload, backend=backend)
                if args.next_frame and result.get("initial_frame_skipped") is not True:
                    result["error"] = "Backend did not confirm subsequent-frame capture; restart the native helper"
                if args.compact:
                    from desktop.observations import compact_capture
                    result = compact_capture(result)
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
                    if args.observation is not None:
                        if "observation-input" not in current.get("input_capabilities", []):
                            raise ValueError("Helper does not support observation-bound input; update and restart it at a safe checkpoint")
                        payload["observation_id"] = args.observation
                    if not 0 <= args.settle_ms <= 5000:
                        raise ValueError("Observation settling delay must be 0–5000 ms")
                    if not args.observe and (args.output is not None or args.settle_ms != 120):
                        raise ValueError("Observation output and settling delay require --observe")
                    observation_output = None
                    if args.observe:
                        observation_output = str(Path(args.output).absolute()) if args.output else client.artifact_path()
                        validate_filesystem_path(observation_output, check_writable=True)
                        observation_path = Path(observation_output)
                        if observation_path.suffix.lower() != ".png" or observation_path.exists() or observation_path.is_symlink():
                            raise ValueError("Observation requires a new PNG path")
                        if not observation_path.parent.is_dir():
                            raise ValueError("Observation output requires an existing parent directory")
                    payload.update({name: getattr(args, name) for name in ("generation", "geometry", "kind", "key", "text", "x", "y", "button")})
                    payload["codes"] = getattr(args, "codes", None)
                    client.validate_keycodes(payload)
                    payload["delay_ms"] = (10 if backend is not runtime and args.kind == "text" and args.delay_ms is None else args.delay_ms)
                    client.validate_text_delay(payload)
                    if args.hold_ms is not None:
                        payload["hold_ms"] = args.hold_ms
                    client.validate_hold(payload)
                    payload.update({name: getattr(args, name) for name in ("to_x", "to_y", "duration_ms")})
                    client.validate_drag(payload)
                    if args.hold_ms is not None and backend is runtime:
                        raise ValueError("Hold duration requires --native")
                result = client.send_action(payload, backend=backend)
                if command == "input" and args.observe and "error" not in result:
                    result = client.observe_after_input(result, payload, observation_output,
                                                        settle_ms=args.settle_ms, backend=backend)
                if wait_title:
                    result["window_baseline"] = baseline
                    try:
                        observed = client.wait_for_window(current["generation"], title=wait_title, backend=backend,
                                                          timeout=args.timeout, launch=result["launch"],
                                                          exclude_titles=exclude_titles, stable_seconds=stable_seconds)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                        result["error"] = str(exc)  # Preserve launch identity for inspection after timeout.
                    else:
                        previous = set(baseline["observed_window_ids"]) if baseline["complete"] else None
                        result.update(windows=observed["windows"], launch_status=observed["launch_status"],
                                      existing_window_ids=([item["id"] for item in observed["windows"] if item["id"] in previous]
                                                           if previous is not None else None),
                                      window_association="title substring; inspect PID/class before acting")
        exit_code = int("error" in result or result.get("healthy") is False or result.get("state") == "failed"
                        or (result.get("state") == "stopped" and bool(result.get("last_failure"))))
        if getattr(args, "save_output", False):
            from desktop.observations import save_observation
            result = save_observation(result)
        print(json.dumps(result, indent=2))
        return exit_code
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        result = getattr(exc, "receipt", {"error": str(exc)})
        if args.desktop_command == "input" and "input_delivery" not in result:
            from desktop.receipts import input_failure
            result = input_failure(exc, {"generation": args.generation}, stage="before-input")
        print(json.dumps(result))
        return 1

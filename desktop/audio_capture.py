"""Opt-in application-only PipeWire recording with explicit additive links."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time

from desktop import control_state, native_session
from desktop.qa_artifacts import MAX_MEDIA_BYTES, clock, file_digest, new_bundle, process_identity, stamp
from lib.atomic_io import write_json_atomic
from lib.streamed_process import run_captured


def _environment() -> dict:
    # Do not inherit PIPEWIRE_REMOTE or a project-controlled config/search path.
    return {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(Path.home()),
            "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}


def graph() -> list[dict]:
    result = run_captured(["/usr/bin/pw-dump"], timeout=2, max_bytes=4 * 1024 * 1024, env=_environment())
    if result.returncode:
        raise RuntimeError("PipeWire graph unavailable")
    data = json.loads(result.stdout)
    if not isinstance(data, list) or len(data) > 8192 or any(not isinstance(item, dict) for item in data):
        raise ValueError("Invalid or oversized PipeWire graph")
    return data


def _props(item: dict) -> dict:
    info = item.get("info")
    return info.get("props", {}) if isinstance(info, dict) and isinstance(info.get("props"), dict) else {}


def select_node(items: list[dict], pid: int, serial: int | None) -> dict:
    selected = [item for item in items if item.get("type") == "PipeWire:Interface:Node"
                and node_pid(items, item) == pid
                and _props(item).get("media.class") == "Stream/Output/Audio"
                and (serial is None or str(_props(item).get("object.serial")) == str(serial))]
    if len(selected) != 1:
        raise ValueError("Select exactly one application playback node using --pid and --node-serial")
    node = selected[0]
    if type(node.get("id")) is not int or not str(_props(node).get("object.serial", "")).isdigit():
        raise ValueError("Playback node identity is unavailable")
    return node


def node_pid(items: list[dict], node: dict) -> int | None:
    """Use protocol-set client identity, never a client-supplied application label.

    PulseAudio's protocol PID is the bridge, so bridged applications deliberately
    remain unsupported rather than trusting their application.process.id label.
    """
    identifier = _props(node).get("client.id")
    clients = [item for item in items if item.get("type") == "PipeWire:Interface:Client"
               and str(item.get("id")) == str(identifier)]
    if len(clients) != 1:
        return None
    props = _props(clients[0])
    pid = props.get("pipewire.sec.pid")
    if str(props.get("pipewire.sec.uid")) != str(os.getuid()) or not str(pid).isdigit():
        return None
    return int(pid)


def sources(pid: int) -> dict:
    native_session.check_session()
    identity = process_identity(pid)
    items = graph()
    nodes = [{"node_serial": _props(item).get("object.serial"), "node_id": item.get("id"),
              "channels": _props(item).get("audio.channels")}
             for item in items if item.get("type") == "PipeWire:Interface:Node"
             and node_pid(items, item) == pid
             and _props(item).get("media.class") == "Stream/Output/Audio"]
    if identity != process_identity(pid):
        raise RuntimeError("Application changed during discovery")
    return {"process": identity, "playback_nodes": nodes, "qualified": False,
            "scope": "Protocol-verified native PipeWire stereo streams only; PulseAudio bridges are unsupported"}


def ports(items: list[dict], node: int, direction: str) -> dict[str, int]:
    result = {}
    for item in items:
        props = _props(item)
        if (item.get("type") == "PipeWire:Interface:Port" and str(props.get("node.id")) == str(node)
                and props.get("port.direction") == direction):
            channel = props.get("audio.channel")
            if channel not in ("FL", "FR") or channel in result or type(item.get("id")) is not int:
                raise ValueError("Only unambiguous stereo FL/FR application ports are supported")
            result[channel] = item["id"]
    if set(result) != {"FL", "FR"}:
        raise ValueError("Selected node must expose stereo FL/FR ports")
    return result


def links(items: list[dict]) -> set[tuple[int, int]]:
    result = set()
    for item in items:
        if item.get("type") != "PipeWire:Interface:Link":
            continue
        info = item.get("info", {})
        if not isinstance(info, dict):
            raise ValueError("PipeWire link info is malformed")
        pair = (info.get("output-port-id"), info.get("input-port-id"))
        if any(type(value) is not int for value in pair):
            raise ValueError("PipeWire link has no verifiable endpoints")
        result.add(pair)
    return result


def verify_links(items: list[dict], source: dict[str, int], target: dict[str, int],
                 baseline: set[tuple[int, int]]) -> None:
    observed = links(items)
    expected = {(source[channel], target[channel]) for channel in ("FL", "FR")}
    received = {pair for pair in observed if pair[1] in target.values()}
    if received != expected:
        raise RuntimeError("Recorder links differ from the selected application; capture is unqualified")
    if not baseline.issubset(observed):
        raise RuntimeError("Application playback links changed during capture; inspect audio routing")


def capture(pid: int, serial: int, seconds: int, output: str | None = None) -> dict:
    if type(seconds) is not int or not 1 <= seconds <= 120 or type(serial) is not int or serial <= 0:
        raise ValueError("Capture requires a positive node serial and 1–120 seconds")
    native_session.check_session()
    permission = control_state.status()
    control_state.require_enabled(permission)
    identity, initial = process_identity(pid), graph()
    node = select_node(initial, pid, serial)
    source = ports(initial, node["id"], "out")
    baseline = {pair for pair in links(initial) if pair[0] in source.values()}
    if not baseline:
        raise ValueError("Application has no observed playback links; audibility preservation is unverified")
    folder, token = new_bundle(output), "basaltwater-audio-" + secrets.token_hex(12)
    data = {"schema_version": 1, "kind": "application-audio", "process": identity,
            "node_serial": serial, "clock": clock(), "started": stamp(), "finished": None,
            "format": {"sample_rate": 48000, "channels": 2, "sample_format": "f32", "container": "wav"},
            "requested_seconds": seconds, "qualified": False, "cleanup_confirmed": False,
            "source_ports": source, "existing_playback_links": sorted(baseline),
            "graph_checks": 0, "link_ready": None, "audio_origin": None,
            "limitations": ["Graph snapshots verify selected links, not sample-accurate onset or uninterrupted routing.",
                            "Start/link-ready/finish bound capture timing; sample zero timing is unknown.",
                            "Playback link preservation does not establish physical audibility."]}
    receipt = folder / "manifest.json"
    write_json_atomic(str(receipt), data, mode=0o600)
    process = None
    target = None
    recorder_node = None
    error = None
    try:
        process = subprocess.Popen(["/usr/bin/python3", str(Path(__file__).with_name("audio_capture_worker.py")),
                                    str(seconds), token, str(folder / "audio.wav")], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, start_new_session=True, env=_environment(), umask=0o077)
        data["recorder_pid"] = process.pid
        write_json_atomic(str(receipt), data, mode=0o600)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            control_state.require_enabled(permission)
            if process.poll() is not None:
                raise RuntimeError("Recorder exited before links were ready")
            items = graph()
            owned = [item for item in items if item.get("type") == "PipeWire:Interface:Node"
                     and _props(item).get("node.name") == token
                     and node_pid(items, item) == process.pid]
            if len(owned) == 1:
                try:
                    target = ports(items, owned[0]["id"], "in")
                except ValueError:
                    time.sleep(.05)
                    continue  # Node announcement can precede its port announcements.
                if any(pair[1] in target.values() for pair in links(items)):
                    raise RuntimeError("Recorder was unexpectedly connected before explicit selection")
                recorder_node = (owned[0]["id"], _props(owned[0]).get("object.serial"))
                data["recorder_node"] = {"id": recorder_node[0], "serial": recorder_node[1], "ports": target}
                break
            time.sleep(.05)
        if target is None:
            raise RuntimeError("Recorder node did not appear within five seconds")
        if identity != process_identity(pid) or select_node(items, pid, serial)["id"] != node["id"]:
            raise RuntimeError("Application identity changed before linking")
        for channel in ("FL", "FR"):
            control_state.require_enabled(permission)
            result = run_captured(["/usr/bin/pw-link", "-w", str(source[channel]), str(target[channel])],
                                  timeout=2, max_bytes=4096, env=_environment())
            if result.returncode:
                raise RuntimeError("Cannot create application-only recording link")
        deadline = time.monotonic() + seconds + 5
        data["link_ready"] = stamp()
        while True:
            control_state.require_enabled(permission)
            if identity != process_identity(pid):
                raise RuntimeError("Application PID was replaced during recording")
            items = graph()
            if select_node(items, pid, serial)["id"] != node["id"] or ports(items, node["id"], "out") != source:
                raise RuntimeError("Application stream changed during recording")
            # pw-record may remove its own node as soon as its sample budget ends.
            exited = process.poll()
            if exited is None:
                observed_recorder = [item for item in items if item.get("type") == "PipeWire:Interface:Node"
                                     and (item.get("id"), _props(item).get("object.serial")) == recorder_node
                                     and node_pid(items, item) == process.pid and _props(item).get("node.name") == token]
                if not observed_recorder:
                    try:
                        exited = process.wait(timeout=.25)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError("Owned recorder disappeared without confirmed exit") from exc
                elif len(observed_recorder) != 1 or ports(items, recorder_node[0], "in") != target:
                    raise RuntimeError("Owned recorder identity or channel ports changed")
                else:
                    verify_links(items, source, target, baseline)
                    data["graph_checks"] += 1
            data["recorder_returncode"] = exited
            if exited is not None:
                if exited not in (0, 1):
                    raise RuntimeError(f"Audio recorder failed (exit {exited})")
                if not baseline.issubset(links(items)):
                    raise RuntimeError("Playback links changed at closeout")
                break
            if (folder / "audio.wav").stat().st_size > MAX_MEDIA_BYTES or time.monotonic() >= deadline:
                raise RuntimeError("Audio recording exceeded its byte or time budget")
            time.sleep(.1)
        from desktop.audio_analysis import audio_metadata
        metadata = audio_metadata(folder / "audio.wav")
        if (abs(metadata["duration_seconds"] - seconds) > 1 / 48000 or data["graph_checks"] == 0
                or metadata["sample_rate"] != 48000 or metadata["channels"] != 2 or metadata["codec"] != "pcm_f32le"):
            raise RuntimeError("Audio duration or selected-link verification is incomplete")
        data["audio"] = {"artifact": "audio.wav", "sha256": file_digest(folder / "audio.wav"), "observed_format": metadata}
        # pw-cat's sample-limit branch quits without setting its drained flag,
        # hence exit 1 even on completed recording. Keep the raw code and accept
        # only an exact bounded WAV, verified links, and empty diagnostics below.
        data["exit_interpretation"] = "sample-limit-and-format-verified" if data["recorder_returncode"] == 1 else "zero-exit"
        data["qualified"] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        error = str(exc)[:1000]
    except KeyboardInterrupt:
        error = "Audio capture interrupted; retained media is unqualified"
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                error = error or "Owned recorder exit is unconfirmed"
            data["recorder_returncode"] = process.returncode if type(process.returncode) is int else None
            if process.stderr is not None:
                os.set_blocking(process.stderr.fileno(), False)
                try:
                    diagnostic = os.read(process.stderr.fileno(), 8192)
                except BlockingIOError:
                    diagnostic = b""
                finally:
                    process.stderr.close()
                with (folder / "recorder.log").open("xb") as log:
                    os.fchmod(log.fileno(), 0o600)
                    log.write(diagnostic)
                if diagnostic and data.get("recorder_returncode") == 1:
                    error = error or "Recorder exited 1 with diagnostics; sample-limit completion is unconfirmed"
            # Destruction of the owned node removes only links incident to it.
            try:
                remaining = graph()
                data["cleanup_confirmed"] = not any(_props(item).get("node.name") == token for item in remaining)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                data["cleanup_confirmed"] = False
        data["finished"] = stamp()
        if error:
            data["error"] = error
        if not data["cleanup_confirmed"]:
            data["error"] = (error + "; " if error else "") + "Owned recorder removal is unconfirmed; inspect before retrying"
        data["qualified"] = data["qualified"] and "error" not in data
        write_json_atomic(str(receipt), data, mode=0o600)
    return {"artifact": str(receipt), "qualified": data["qualified"], "cleanup_confirmed": data["cleanup_confirmed"],
            **({"error": data["error"]} if "error" in data else {})}

"""Human permission and backend controls for the KDE portal session."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop import control_state, native_session


def describe(status: dict) -> str:
    """Describe whether the portal session currently permits agent input."""
    permission = (control_state.describe(status) if status.get("agent_control") in control_state.MODES
                  else "Agent permission unknown")
    return permission + "\n" + describe_backend(status)


def describe_backend(status: dict) -> str:
    """Keep backend availability separate from durable agent permission."""
    if "error" in status:
        return "Native desktop control unavailable: " + str(status["error"])
    if status.get("stopped") or status.get("state") == "stopped":
        return "Automation stopped; KDE and applications are preserved"
    if status.get("state") == "awaiting-consent":
        seconds = status.get("consent_expires_in")
        remaining = f" ({seconds} seconds remaining)" if type(seconds) is int else ""
        return "Waiting for KDE response" + remaining + ": " + status.get("detail", "Check whether KDE requests approval")
    if status.get("state") == "initializing":
        return "Initializing native control: " + status.get("portal_stage", "connecting")
    if status.get("state") == "failed":
        return "Native desktop control failed: " + status.get("detail", "Inspect desktop status")
    if status.get("state") != "running":
        return "Native desktop control unavailable: " + status.get("detail", "Inspect desktop status")
    if status.get("expires_in") is not None and status["expires_in"] <= 0:
        return "Automation expired; start and inspect KDE permission status"
    return "Native desktop backend running"


def main():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    window = Gtk.Window(title="Basaltwater native desktop control")
    window.set_border_width(16)
    window.set_resizable(False)
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    window.add(layout)
    heading = Gtk.Label(xalign=0)
    heading.set_markup("<b>Agent permission</b>")
    layout.pack_start(heading, False, False, 0)
    label = Gtk.Label(label="Checking agent permission…", xalign=0)
    label.set_line_wrap(True)
    label.set_max_width_chars(64)
    layout.pack_start(label, False, False, 0)
    explanation = Gtk.Label(label="Your choice stays saved when this window closes or the backend stops.\nA timed override enables agents when its timer ends.", xalign=0)
    explanation.set_line_wrap(True)
    explanation.set_max_width_chars(64)
    layout.pack_start(explanation, False, False, 0)
    layout.pack_start(Gtk.Label(label="Take control duration", xalign=0), False, False, 0)
    duration = Gtk.ComboBoxText()
    for value, title in (("900", "15 minutes"), ("300", "5 minutes"), ("3600", "1 hour"), ("indefinite", "Until I enable agents")):
        duration.append(value, title)
    duration.set_active_id("900")
    duration.set_tooltip_text("Take control duration")
    layout.pack_start(duration, False, False, 0)
    row = Gtk.Box(spacing=8)
    layout.pack_start(row, False, False, 0)
    busy = threading.Event()
    pending: deque[tuple[str, int | None]] = deque()
    backend_label = Gtk.Label(label="Checking backend…", xalign=0)
    backend_label.set_line_wrap(True)
    backend_label.set_max_width_chars(64)

    def show(result):
        label.set_text(control_state.describe(result) if result.get("agent_control") in control_state.MODES
                       else "Agent permission unknown")
        backend_label.set_text(describe_backend(result))
        busy.clear()
        if pending:
            request(*pending.popleft())
        return False

    def request(action, seconds=900):
        if busy.is_set():
            if action != "status":
                pending.append((action, seconds))
                backend_label.set_text("Control request pending…")
            return
        busy.set()
        if action != "status":
            backend_label.set_text("Updating desktop control…")

        def work():
            try:
                if action == "status":
                    result = native_session.status()
                elif action == "revoke":
                    result = native_session.revoke()
                elif action in {"enabled", "human-override", "disabled"}:
                    result = native_session.set_control(action, seconds=seconds, human_approved=True)
                else:
                    result = native_session.request({"action": action})
                if "agent_control" not in result:
                    result = {**control_state.status(), **result}
            except (OSError, ValueError, RuntimeError) as exc:
                result = {"error": str(exc)}
            GLib.idle_add(show, result)

        threading.Thread(target=work, daemon=True).start()

    for title, action in (("Take control", "human-override"), ("Enable agents", "enabled"), ("Disable agents", "disabled")):
        button = Gtk.Button(label=title)
        if action == "human-override":
            button.get_style_context().add_class("suggested-action")
        button.connect("clicked", lambda _button, operation=action: request(
            operation, None if duration.get_active_id() == "indefinite" else int(duration.get_active_id())))
        row.pack_start(button, False, False, 0)
    backend_row = Gtk.Box(spacing=8)
    layout.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
    layout.pack_start(Gtk.Label(label="Native desktop backend", xalign=0), False, False, 0)
    layout.pack_start(backend_label, False, False, 0)
    layout.pack_start(backend_row, False, False, 0)
    for title, action in (("Stop backend", "stop"), ("Revoke saved access", "revoke")):
        button = Gtk.Button(label=title)
        button.connect("clicked", lambda _button, operation=action: request(operation))
        backend_row.pack_start(button, False, False, 0)
    layout.pack_start(Gtk.Label(label="Stopping or revoking access keeps your saved agent permission.", xalign=0), False, False, 0)
    window.connect("destroy", lambda *_: Gtk.main_quit())
    window.show_all()
    GLib.timeout_add_seconds(1, lambda: (request("status"), True)[1])
    request("status")
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

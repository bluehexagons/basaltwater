"""Human pause/resume/stop controls for a task's KDE portal session."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop import native_session


def describe(status: dict) -> str:
    """Describe whether the portal session currently permits agent input."""
    if "error" in status:
        return "Native desktop control unavailable: " + str(status["error"])
    if status.get("stopped") or status.get("state") == "stopped":
        return "Automation stopped; KDE and applications are preserved"
    if status.get("state") == "awaiting-consent":
        return "Waiting for KDE response: " + status.get("detail", "Check whether KDE requests approval")
    if status.get("state") == "initializing":
        return "Initializing native control: " + status.get("portal_stage", "connecting")
    if status.get("state") == "failed":
        return "Native desktop control failed: " + status.get("detail", "Inspect desktop status")
    if status.get("state") != "running":
        return "Native desktop control unavailable: " + status.get("detail", "Inspect desktop status")
    if status.get("expires_in", 0) <= 0:
        return "Automation expired; start a new session with KDE consent"
    if status.get("paused"):
        return "Agent input paused — you have control"
    return "Agent input enabled (" + str(status["expires_in"]) + " seconds remaining)"


def main():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    window = Gtk.Window(title="Basaltwater native desktop control")
    window.set_border_width(16)
    window.set_resizable(False)
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    window.add(layout)
    label = Gtk.Label(label="Checking native automation…")
    layout.pack_start(label, False, False, 0)
    layout.pack_start(Gtk.Label(label="Pause blocks agent input. Your keyboard and mouse keep working."), False, False, 0)
    row = Gtk.Box(spacing=8)
    layout.pack_start(row, False, False, 0)
    busy = threading.Event()
    pending: deque[str] = deque()

    def show(result):
        label.set_text(describe(result))
        busy.clear()
        if pending:
            request(pending.popleft())
        return False

    def request(action):
        if busy.is_set():
            if action != "status":
                pending.append(action)
                label.set_text("Control request pending: " + action)
            return
        busy.set()
        if action != "status":
            label.set_text("Updating desktop control: " + action)

        def work():
            try:
                result = native_session.status() if action == "status" else native_session.request({"action": action})
            except (OSError, ValueError, RuntimeError) as exc:
                result = {"error": str(exc)}
            GLib.idle_add(show, result)

        threading.Thread(target=work, daemon=True).start()

    for title, action in (("Pause agents", "pause"), ("Resume agents", "resume"), ("Stop automation", "stop")):
        button = Gtk.Button(label=title)
        button.connect("clicked", lambda _button, operation=action: request(operation))
        row.pack_start(button, False, False, 0)
    window.connect("destroy", lambda *_: Gtk.main_quit())
    window.show_all()
    GLib.timeout_add_seconds(1, lambda: (request("status"), True)[1])
    request("status")
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Small human-facing control window; closing it never resumes agent input."""

from __future__ import annotations

from pathlib import Path
import queue
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop import control_state, session_runtime as runtime


def describe(status: dict) -> str:
    if "error" in status:
        return "Agent permission unknown: " + str(status["error"])
    if status.get("agent_control") not in control_state.MODES:
        return "Agent permission unknown"
    return control_state.describe(status)


def main() -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("Desktop handoff needs python3-tk; rerun desktop setup", file=sys.stderr)
        return 1
    runtime.configuration()
    root = tk.Tk()
    root.title("Shared desktop control")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.pack()
    message = tk.StringVar(value="Checking desktop control…")
    ttk.Label(frame, text="Agent permission").pack(anchor="w")
    ttk.Label(frame, textvariable=message, wraplength=380).pack(anchor="w", pady=(0, 12))
    ttk.Label(frame, text="Your choice stays saved when this window closes or the backend stops. A timed override enables agents when its timer ends.", wraplength=380).pack(anchor="w")
    ttk.Label(frame, text="Take control duration").pack(anchor="w", pady=(12, 0))
    duration = tk.StringVar(value="15 minutes")
    durations = {"15 minutes": 900, "5 minutes": 300, "1 hour": 3600, "Until I enable agents": None}
    ttk.Combobox(frame, textvariable=duration, values=list(durations), state="readonly").pack(anchor="w")
    operations: queue.Queue[tuple[str, int | None]] = queue.Queue(maxsize=1)
    responses: queue.Queue[dict] = queue.Queue()
    stopping = threading.Event()
    health = tk.StringVar(value="Checking backend…")

    def submit(action: str) -> None:
        try:
            operations.put_nowait((action, durations[duration.get()]))
            health.set("Updating desktop control…")
        except queue.Full:
            health.set("A control request is pending…")

    buttons = ttk.Frame(frame)
    buttons.pack(anchor="w", pady=(12, 0))
    for title, action in (("Take control", "human-override"), ("Enable agents", "enabled"), ("Disable agents", "disabled")):
        ttk.Button(buttons, text=title, command=lambda mode=action: submit(mode)).pack(side="left", padx=(0, 8))
    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=12)
    ttk.Label(frame, textvariable=health, wraplength=380).pack(anchor="w")

    def worker() -> None:
        while not stopping.is_set():
            try:
                action, seconds = operations.get(timeout=1)
            except queue.Empty:
                action, seconds = "status", 900
            try:
                responses.put(runtime.status() if action == "status" else runtime.set_control(
                    action, seconds=seconds, human_approved=True))
            except (OSError, ValueError, RuntimeError) as exc:
                responses.put({"error": str(exc)})

    def refresh() -> None:
        try:
            while True:
                result = responses.get_nowait()
                message.set(describe(result))
                health.set("Desktop backend: " + str(result.get("state", "unavailable")) if "error" not in result
                           else str(result["error"]))
        except queue.Empty:
            pass
        root.after(100, refresh)

    def close() -> None:
        stopping.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    threading.Thread(target=worker, daemon=True).start()
    refresh()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

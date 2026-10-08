"""User-systemd child runner with bounded private logs and durable exit evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

# A transient service launches this file by absolute path from any project.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop.development import LOG_LIMIT, _honor_human_pause, load_task
from lib.atomic_io import write_json_atomic


def run_task(directory: Path) -> int:
    task = load_task(directory)
    result = {"returncode": 1, "log_truncated": False}
    process = None
    stop_at = None
    terminate_sent = False
    kill_sent = False

    def stopping(*_):
        nonlocal stop_at
        if stop_at is None:
            stop_at = time.monotonic()

    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    try:
        _honor_human_pause()  # The owner may have paused after the client queued the service.
        descriptor = os.open(directory / "output.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as log:
            process = subprocess.Popen(task["argv"], cwd=task["project"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            write_json_atomic(str(directory / "started.json"), {"launch_pid": process.pid}, mode=0o600)
            remaining = LOG_LIMIT
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    if stop_at is not None:
                        if process.poll() is None:
                            if time.monotonic() - stop_at > 5 and not kill_sent:
                                process.kill()
                                kill_sent = True
                            elif not terminate_sent and not kill_sent:
                                process.terminate()
                                terminate_sent = True
                        if time.monotonic() - stop_at > 6:
                            break  # systemd stops any remaining task descendants.
                    for key, _ in selector.select(timeout=.1):
                        body = os.read(key.fd, 65536)
                        if not body:
                            selector.unregister(key.fileobj)
                            continue
                        log.write(body[:remaining])
                        if len(body) > remaining:
                            result["log_truncated"] = True
                        remaining = max(0, remaining - len(body))
            process.stdout.close()
            result["returncode"] = process.wait(timeout=2 if stop_at is not None else None)
            log.flush()
            os.fsync(log.fileno())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        result["error"] = "Application launch or output collection failed; inspect task logs"
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(str(directory / "result.json"), result, mode=0o600)
    # A project test/app failure belongs to the task record, not the host's
    # failed-service inventory. Failure to persist evidence still raises.
    return 0


if __name__ == "__main__":
    raise SystemExit(run_task(Path(sys.argv[1])))

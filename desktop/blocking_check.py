"""Check the installed desktop browser's content filtering on loopback."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from desktop.browser_session import FLATPAK_BROWSER_IDS


_PAGE = b"""<!doctype html><title>Local content-blocking check</title>
<script>
function loadScript(path) {
  return new Promise(resolve => {
    const script = document.createElement('script');
    script.onload = () => resolve('loaded');
    script.onerror = () => resolve('blocked');
    script.src = path;
    document.head.append(script);
  });
}
async function probe() {
  const id = String(++window.attempt);
  const control = await loadScript('control.js?' + id);
  const ad = await loadScript('ads/custom_ads.js?' + id);
  await fetch('result/' + id + '/' + control + '/' + ad);
  setTimeout(probe, 2000);
}
window.attempt = 0;
setTimeout(probe, 1000);
</script>"""


def run_check(browser: str, *, flatpak: bool, timeout: int) -> dict:
    if not sys.platform.startswith("linux"):
        raise ValueError("The installed-browser filtering check requires Linux")
    if browser not in {"chromium", "firefox", "librewolf"}:
        raise ValueError("Choose Chromium, Firefox or LibreWolf")
    if not 1 <= timeout <= 300:
        raise ValueError("Choose a check timeout between 1 and 300 seconds")
    if os.geteuid() == 0:
        raise ValueError("Run the browser check as the desktop user")
    parent = None
    if flatpak:
        if browser not in FLATPAK_BROWSER_IDS:
            raise ValueError("Choose Firefox or LibreWolf for a system Flatpak check")
        app = FLATPAK_BROWSER_IDS[browser]
        parent = Path.home() / ".var/app" / app / "cache"
        parent.mkdir(parents=True, exist_ok=True)
        launcher = ["flatpak", "run", "--system", app]
    else:
        candidates = ("firefox-esr", "firefox") if browser == "firefox" else (browser,)
        executable = next((path for name in candidates if (path := shutil.which(name))), None)
        if not executable:
            raise ValueError(f"Desktop browser is unavailable: {browser}")
        launcher = [executable]
    prefix = "/" + secrets.token_hex(16) + "/"
    observed = {"control": set(), "ad": set()}
    outcome = {"schema_version": 1, "ok": False, "browser": browser, "flatpak": flatpak,
               "issue": "no_result", "control_loaded": False, "ad_blocked": None, "attempts": 0}

    class Handler(BaseHTTPRequestHandler):
        timeout = 3

        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path)
            route = path.path.removeprefix(prefix)
            body, mime = b"", "text/plain"
            if self.headers.get("Host") != host or not path.path.startswith(prefix):
                self.send_error(404)
                return
            if not route:
                body, mime = _PAGE, "text/html"
            elif route in ("control.js", "ads/custom_ads.js") and path.query.isdecimal():
                observed["control" if route == "control.js" else "ad"].add(path.query)
                body, mime = b"/* local test script */", "text/javascript"
            elif route.startswith("result/"):
                parts = route.split("/")
                if (len(parts) == 4 and parts[1] in observed["control"] and parts[2] == "loaded"
                        and parts[3] in ("loaded", "blocked")):
                    blocked = parts[3] == "blocked" and parts[1] not in observed["ad"]
                    outcome.update(ok=blocked, issue=None if blocked else "ad_loaded",
                                   control_loaded=True, ad_blocked=blocked, attempts=outcome["attempts"] + 1)
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory(dir=parent) as profile:
        server.timeout = 0.5
        host = f"127.0.0.1:{server.server_port}"
        url = f"http://{host}{prefix}"
        flags = (["--headless=new", "--no-first-run", f"--user-data-dir={profile}"] if browser == "chromium"
                 else ["--headless", "--no-remote", "--profile", profile])
        process = subprocess.Popen([*launcher, *flags, url], start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not outcome["ok"]:
                if process.poll() is not None:
                    outcome["issue"] = "browser_exited"
                    break
                server.handle_request()
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                process.wait()
    return outcome

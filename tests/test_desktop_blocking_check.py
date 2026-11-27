"""Check the actual request and callback evidence expected from a desktop browser."""

from __future__ import annotations

from pathlib import Path
import threading
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen

from desktop.blocking_check import run_check


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop browser check")
class BrowserFilterTests(unittest.TestCase):
    def probe(self, routes):
        threads = []
        profiles = []
        failures = []

        def browser(argv, **kwargs):
            self.assertTrue(kwargs["start_new_session"])
            profiles.append(Path(argv[argv.index("--profile") + 1]))
            def visit():
                try:
                    for route in ("", *routes):
                        with urlopen(argv[-1] + route, timeout=2) as response:
                            response.read()
                except Exception as exc:
                    failures.append(exc)
            threads.append(threading.Thread(target=visit))
            threads[-1].start()
            return Mock(pid=1234, poll=Mock(return_value=None))

        with (patch("desktop.blocking_check.os.geteuid", return_value=1000, create=True),
              patch("desktop.blocking_check.shutil.which", return_value="/usr/bin/firefox-esr"),
              patch("desktop.blocking_check.subprocess.Popen", side_effect=browser),
              patch("desktop.blocking_check.os.killpg", create=True)):
            result = run_check("firefox", flatpak=False, timeout=2)
        for thread in threads:
            thread.join(3)
        self.assertFalse(failures)
        self.assertFalse(profiles[0].exists())
        return result

    def test_retries_first_launch_and_correlates_the_control_with_the_block(self):
        result = self.probe(["control.js?1", "ads/custom_ads.js?1", "result/1/loaded/loaded",
                             "control.js?2", "result/2/loaded/blocked"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)

    def test_browser_error_after_receiving_ad_cannot_report_protection(self):
        result = self.probe(["control.js?1", "ads/custom_ads.js?1", "result/1/loaded/blocked"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["issue"], "ad_loaded")

    def test_callback_without_control_cannot_report_protection(self):
        result = self.probe(["result/1/loaded/blocked"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["control_loaded"])

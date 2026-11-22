"""Conversion captures serving configuration and preserves data identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import build_conversion as conversion
from tests.test_build_target_setup import enrollment
from tests.test_build_target_maintenance import root_view


class ConversionTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.root.chmod(0o700)
        self.enterContext(root_view())
        self.snapshot = self.root / "conversion.json"
        self.nginx = self.root / "nginx"
        available = self.nginx / "sites-available"
        available.mkdir(parents=True)
        (available / "shop").write_text("root /srv/example;\n")
        (available / "shop").chmod(0o644)
        enabled = self.nginx / "sites-enabled"
        enabled.mkdir()
        (enabled / "shop").symlink_to("../sites-available/shop")
        self.units = self.root / "units"
        self.units.mkdir()
        self.application_root = self.root / "www" / "shop"
        self.application_root.mkdir(parents=True)
        self.data = self.root / "www" / ".basaltwater_shared" / "shop" / "web" / "data"
        self.data.mkdir(parents=True)
        (self.data / "database").write_bytes(b"private persistent data")
        for path in self.root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        self.enrollment = enrollment()
        self.enrollment["policy"]["applications"]["shop"]["deployment_root"] = str(self.application_root)
        self.enterContext(patch.object(conversion, "SNAPSHOT", str(self.snapshot)))
        self.enterContext(patch.object(conversion, "NGINX", str(self.nginx)))
        self.enterContext(patch.object(conversion, "UNITS", str(self.units)))

    def installed(self):
        binding = self.enrollment["policy"]["applications"]["shop"]
        return {"applications": {"shop": dict(root=str(self.application_root), bindings=dict(
            components=[dict(name=name, **component) for name, component in binding["components"].items()]))}}

    def test_snapshot_precedes_mutation_and_finishes_with_same_data(self):
        conversion.capture_conversion(self.enrollment)
        snapshot = json.loads(self.snapshot.read_bytes())
        self.assertEqual(snapshot["state"], "captured")
        self.assertEqual(len(snapshot["files"]), 2)
        self.assertEqual(self.snapshot.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RuntimeError, "Unfinished"):
            conversion.capture_conversion(self.enrollment)
        installed = SimpleNamespace(_load=lambda **_: self.installed())
        conversion.finish_conversion(installed, [dict(application="shop")])
        self.assertEqual(json.loads(self.snapshot.read_bytes())["state"], "completed")
        self.assertEqual((self.data / "database").read_bytes(), b"private persistent data")
        before = self.snapshot.read_bytes()
        conversion.capture_conversion(self.enrollment)
        self.assertEqual(before, self.snapshot.read_bytes())

    def test_changed_data_directory_or_routes_refuse_success(self):
        conversion.capture_conversion(self.enrollment)
        state = self.installed()
        state["applications"]["shop"]["bindings"]["components"][0]["path"] = "/changed"
        with self.assertRaisesRegex(ValueError, "routes"):
            conversion.finish_conversion(SimpleNamespace(_load=lambda **_: state), [dict(application="shop")])
        renamed = self.data.with_name("old-data")
        self.data.rename(renamed)
        self.data.mkdir()
        with self.assertRaisesRegex(ValueError, "persistent"):
            conversion.finish_conversion(SimpleNamespace(_load=lambda **_: self.installed()), [dict(application="shop")])
        self.assertEqual(json.loads(self.snapshot.read_bytes())["state"], "captured")

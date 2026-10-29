"""Operator draft contracts, using pinned fixtures and no network or builds."""

from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from lib.build_setup_sources import PreparedSource
from lib.deployment import DeploymentOrchestrator
from lib.manifest_serving import runtime_from_component
from lib.project_manifest import parse_manifest
from scripts import prepare_build_policies as drafts


WIRE = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"k" * 32
KEY = "ssh-ed25519 " + base64.b64encode(WIRE).decode()


def prepared(destination="example.com", *, tools=("go",)):
    manifest = parse_manifest({"version": 1, "components": [
        {"name": "site", "type": "static", "domain": "{{domain}}", "output": "dist"},
        {"name": "api", "type": "service", "domain": "{{domain}}", "path": "/api",
         "exec": "{{release_dir}}/bin/api --db {{data_dir}}/app.db", "artifacts": ["bin/api"],
         "build": "go build -o bin/api .", "port": "auto",
         "working_dir": "{{data_dir}}", "runtime_env": {"PORT": "{{port}}"},
         "sqlite_backup": "{{data_dir}}/app.db", "backup_retention": 7},
    ]})
    return PreparedSource(destination, manifest,
                          {"revision": "a" * 40, "snapshot_sha256": "b" * 64,
                           "manifest_sha256": "c" * 64}, "/abs/source.snapshot", tools)


class BuildPolicyDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        workspace = patch.object(drafts, "get_workspace_dir", return_value=str(self.workspace))
        workspace.start()
        self.addCleanup(workspace.stop)
        self.key = self.root / "identity.pub"
        self.key.write_text(KEY + " operator@example\n")
        self.key.chmod(0o644)

    def draft(self, items=None, **kwargs):
        return drafts.draft_enrollments(
            [prepared()] if items is None else items, builder_key=KEY, target_key=KEY, **kwargs)

    def test_full_runtime_grant_and_defaults(self):
        item = prepared()
        builder, target = self.draft([item])
        project = next(iter(builder["policy"]["principals"]["deploy"]["projects"].values()))
        app = next(iter(target["policy"]["applications"].values()))
        self.assertEqual(project["runtimes"], ["go"])
        self.assertEqual(project["limits"], drafts.DRAFT_LIMITS)
        self.assertEqual(app["components"]["api"]["runtime"],
                         runtime_from_component(item.manifest.components[1]))
        self.assertEqual(set(app["components"]), {"site", "api"})
        self.assertEqual(builder["max_bytes"], 64 * 1024**3)
        self.assertEqual(target["max_bytes"], 8 * 1024**3)
        self.assertEqual(builder["max_records"], 128)

    def test_foodguide_fixed_path_and_ordinary_root_identity_data_parity(self):
        item = prepared("food.example.com/foodguide")
        item.manifest.components[0].path = "/foodguide"
        _, target = self.draft([item])
        app = next(iter(target["policy"]["applications"].values()))
        self.assertEqual(app["components"]["site"]["path"], "/foodguide")
        self.assertEqual(app["components"]["api"]["path"], "/api")
        local = DeploymentOrchestrator()
        ordinary = local.get_deployment_path("food.example.com", "/foodguide", "https://example.com/repo")
        self.assertEqual(app["deployment_root"], ordinary)
        self.assertEqual(ordinary, "/var/www/food_example_com__foodguide")
        component = item.manifest.components[1]
        self.assertEqual(local._service_identity(app["deployment_root"], component),
                         local._service_identity(ordinary, component))
        self.assertEqual(local._component_data_dir(app["deployment_root"], component),
                         "/var/www/.basaltwater_shared/food_example_com__foodguide/api/data")

    def test_explicit_runtimes_take_precedence_and_inferred_tools_are_preserved(self):
        for tools in ((), ("node",), ("python", "go")):
            item = prepared(tools=tools)
            item.manifest.build_runtimes = list(tools)
            builder, _ = self.draft([item])
            grant = next(iter(builder["policy"]["principals"]["deploy"]["projects"].values()))
            self.assertEqual(grant["runtimes"], sorted(tools))
        item = prepared()
        item.manifest.build_runtimes = []
        with self.assertRaisesRegex(ValueError, "differs"):
            self.draft([item])

    def test_names_are_stable_bounded_and_disambiguate_lossy_spelling(self):
        destinations = ["a-b.example", "a.b.example", "example.com/" + "long/" * 30]
        names = [drafts.application_slug(value) for value in destinations]
        self.assertEqual(len(set(names)), 3)
        for name in names:
            self.assertLessEqual(len(name), 64)
            self.assertRegex(name, r"^[a-z0-9][a-z0-9-]*$")
        self.assertEqual(drafts.application_slug("EXAMPLE.COM./"), drafts.application_slug("example.com"))
        items = [prepared("one.example"), prepared("two.example")]
        self.assertEqual(self.draft(items), self.draft(list(reversed(items))))

    def test_duplicates_slug_root_identity_and_route_collisions_refused(self):
        for items in ([prepared(), prepared()],
                      [prepared("a.b.example"), prepared("a_b.example")]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.draft(items)
        with patch.object(drafts, "application_slug", return_value="collision"):
            with self.assertRaisesRegex(ValueError, "collision"):
                self.draft([prepared("one.example"), prepared("two.example")])
        item = prepared("other.example")
        for component in item.manifest.components:
            component.domain = "example.com"
        with self.assertRaisesRegex(ValueError, "route .* shared"):
            self.draft([prepared(), item])

    def test_unsupported_manifest_and_invalid_limits_principal_key_fail(self):
        item = prepared()
        item.manifest.components[1].env_file = "/etc/secret"
        with self.assertRaises(ValueError):
            self.draft([item])
        for kwargs in ({"limits": dict(drafts.DRAFT_LIMITS, memory_bytes=1)},
                       {"principal": "Invalid"}, {"builder_max_records": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.draft(**kwargs)
        with self.assertRaises(ValueError):
            drafts.draft_enrollments([prepared()], builder_key="private material", target_key=KEY)

    def test_exclusive_private_files_and_nooverwrite_of_either_name(self):
        builder, target = self.draft()
        output = self.root / "drafts"
        drafts.write_drafts(str(output), builder, target)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        for name, document in ((drafts.BUILDER_FILENAME, builder), (drafts.TARGET_FILENAME, target)):
            path = output / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), document)
        original = (output / drafts.BUILDER_FILENAME).read_bytes()
        with self.assertRaises(FileExistsError):
            drafts.write_drafts(str(output), builder, target)
        self.assertEqual((output / drafts.BUILDER_FILENAME).read_bytes(), original)
        (output / drafts.BUILDER_FILENAME).unlink()
        with self.assertRaises(FileExistsError):
            drafts.write_drafts(str(output), builder, target)
        self.assertFalse((output / drafts.BUILDER_FILENAME).exists())

    def test_output_permissions_symlinks_and_validation_before_creation(self):
        builder, target = self.draft()
        output = self.root / "drafts"
        with self.assertRaises(ValueError):
            drafts.write_drafts(str(output), builder, dict(target, max_records=0))
        self.assertFalse(output.exists())
        output.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "0700"):
            drafts.write_drafts(str(output), builder, target)
        output.chmod(0o700)
        link = self.root / "link"
        link.symlink_to(output)
        with self.assertRaises(ValueError):
            drafts.write_drafts(str(link), builder, target)

    def arguments(self):
        return ["--deploy", "example.com", "https://example.com/repo", "--builder-key", str(self.key),
                "--target-key", str(self.key), "--output-dir", str(self.root / "drafts")]

    def test_cli_reuses_private_source_preparation_and_public_companion_only(self):
        private = self.root / "identity"
        private.write_text("PRIVATE CONTENT MUST NEVER BE READ")
        def source(specs, destination):
            self.assertEqual(specs, [["example.com", "https://example.com/repo"]])
            self.assertEqual(Path(destination).parent, self.workspace)
            self.assertEqual(stat.S_IMODE(os.stat(destination).st_mode), 0o700)
            return [replace(prepared(), source_path=destination + "/snapshot")]
        with patch.object(drafts, "prepare_setup_sources", side_effect=source) as fetch:
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(drafts.main(self.arguments()), 0)
        fetch.assert_called_once()
        self.assertFalse(Path(fetch.call_args.args[1]).exists())
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertIn("review required", output.getvalue())
        for path in (self.root / "drafts").iterdir():
            self.assertNotIn("PRIVATE", path.read_text())
            self.assertNotIn("operator@example", path.read_text())
            self.assertEqual(json.loads(path.read_text())["keys"], {"deploy": KEY})

    def test_cli_key_errors_and_source_failure_produce_no_drafts(self):
        for contents in ("-----BEGIN OPENSSH PRIVATE KEY-----\n", KEY + "\n" + KEY,
                         "command=evil " + KEY, "ssh-ed25519 invalid"):
            self.key.write_text(contents)
            with patch.object(drafts, "prepare_setup_sources") as fetch:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        drafts.main(self.arguments())
            fetch.assert_not_called()
            self.assertFalse((self.root / "drafts").exists())
        self.key.write_text(KEY)
        with patch.object(drafts, "prepare_setup_sources", side_effect=ValueError("source failed")) as fetch:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                drafts.main(self.arguments())
        temporary = Path(fetch.call_args.args[1])
        self.assertEqual(temporary.parent, self.workspace)
        self.assertFalse(temporary.exists())
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertFalse((self.root / "drafts").exists())


if __name__ == "__main__":
    unittest.main()

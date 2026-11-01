"""Shared enrollment generation and CLI contracts without network or builds."""

from __future__ import annotations

import argparse
import base64
import copy
from contextlib import ExitStack, redirect_stderr, redirect_stdout
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
from lib import build_enrollment, build_enrollment_cli
from lib.build_setup_sources import _plans
from lib.config import SetupConfig
from lib.deployment import DeploymentOrchestrator
from lib.manifest_serving import runtime_from_component
from lib.project_manifest import PublicUDP, parse_manifest
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
        return build_enrollment.generate_enrollments(
            [prepared()] if items is None else items, builder_key=KEY, target_key=KEY, **kwargs)

    def test_full_runtime_grant_and_defaults(self):
        item = prepared()
        builder, target = self.draft([item])
        project = next(iter(builder["policy"]["principals"]["deploy"]["projects"].values()))
        app = next(iter(target["policy"]["applications"].values()))
        self.assertEqual(project["runtimes"], ["go"])
        self.assertEqual(project["limits"], build_enrollment.DEFAULT_POLICY_LIMITS)
        self.assertEqual(app["components"]["api"]["runtime"],
                         runtime_from_component(item.manifest.components[1]))
        self.assertEqual(set(app["components"]), {"site", "api"})
        self.assertEqual(builder["max_bytes"], 64 * 1024**3)
        self.assertEqual(target["max_bytes"], 8 * 1024**3)
        self.assertEqual(builder["max_records"], 128)

    def test_docs_fixed_path_and_ordinary_root_identity_data_parity(self):
        item = prepared("docs.example.com/docs")
        item.manifest.components[0].path = "/docs"
        _, target = self.draft([item])
        app = next(iter(target["policy"]["applications"].values()))
        self.assertEqual(app["components"]["site"]["path"], "/docs")
        self.assertEqual(app["components"]["api"]["path"], "/api")
        local = DeploymentOrchestrator()
        ordinary = local.get_deployment_path("docs.example.com", "/docs", "https://example.com/repo")
        self.assertEqual(app["deployment_root"], ordinary)
        self.assertEqual(ordinary, "/var/www/docs_example_com__docs")
        component = item.manifest.components[1]
        self.assertEqual(local._service_identity(app["deployment_root"], component),
                         local._service_identity(ordinary, component))
        self.assertEqual(local._component_data_dir(app["deployment_root"], component),
                         "/var/www/.basaltwater_shared/docs_example_com__docs/api/data")

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
        names = [build_enrollment.application_slug(value) for value in destinations]
        self.assertEqual(len(set(names)), 3)
        for name in names:
            self.assertLessEqual(len(name), 64)
            self.assertRegex(name, r"^[a-z0-9][a-z0-9-]*$")
        self.assertEqual(build_enrollment.application_slug("EXAMPLE.COM./"), build_enrollment.application_slug("example.com"))
        items = [prepared("one.example"), prepared("two.example")]
        self.assertEqual(self.draft(items), self.draft(list(reversed(items))))

    def test_duplicates_slug_root_identity_and_route_collisions_refused(self):
        for items in ([prepared(), prepared()],
                      [prepared("a.b.example"), prepared("a_b.example")]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.draft(items)
        with patch.object(build_enrollment, "application_slug", return_value="collision"):
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
        for kwargs in ({"limits": dict(build_enrollment.DEFAULT_POLICY_LIMITS, memory_bytes=1)},
                       {"principal": "Invalid"}, {"builder_max_records": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.draft(**kwargs)
        with self.assertRaises(ValueError):
            build_enrollment.generate_enrollments([prepared()], builder_key="private material", target_key=KEY)

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


class BuildEnrollmentCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.key = self.root / "controller-key"
        self.key.write_text("opaque fixture; never a real private key", encoding="utf-8")
        self.key.chmod(0o600)
        companion = Path(str(self.key) + ".pub")
        companion.write_text(KEY + " operator@example\n", encoding="utf-8")
        companion.chmod(0o644)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
        stack.enter_context(patch("lib.system_utils.get_local_timezone", return_value="UTC"))
        self.fetch = stack.enter_context(patch.object(
            build_enrollment_cli, "prepare_setup_sources", side_effect=lambda specs, _root:
            [prepared(destination) for destination, _url, _ref in _plans(specs)],
        ))
        for name in ("subprocess.Popen", "subprocess.run", "socket.socket", "socket.getaddrinfo"):
            stack.enter_context(patch(name, side_effect=AssertionError("Unexpected system action: " + name)))

    def config(self, *, builder=False, **kwargs):
        return SetupConfig(
            host="build.example" if builder else "web.example", username="agent",
            system_type="server_lite" if builder else "server_web", machine_type="vm",
            ssh_key=str(self.key), is_build_server=builder,
            **({} if builder else {"build_on": "builder", "ssh_access_sources": ["192.0.2.10/32"],
                                   "deploy_specs": [["example.com", "https://example.com/site.git"]]}),
            **kwargs,
        )

    def apply(self, config, **kwargs):
        args = argparse.Namespace(**kwargs)
        build_enrollment_cli.prepare_cli_build_enrollments(config, args, workspace=str(self.workspace))

    def test_builder_updates_preserve_unrelated_grants_principals_and_adjusted_limits(self):
        config = self.config(builder=True)
        specs = [["example.com", "https://example.com/site.git"]]
        self.apply(config, build_project=specs, build_max_records=256)
        first = copy.deepcopy(config.build_host_enrollment)
        grant = next(iter(config.build_host_enrollment["policy"]["principals"]["deploy"]["projects"].values()))
        grant["limits"]["cpu_percent"] = 100
        from tests.test_build_selection import public_key

        config.build_host_enrollment["keys"]["other"] = public_key(2)
        config.build_host_enrollment["policy"]["principals"]["other"] = {
            "projects": {"retained": copy.deepcopy(grant)},
        }
        other = copy.deepcopy(config.build_host_enrollment["policy"]["principals"]["other"])
        self.apply(config, build_project=[["second.example", "https://example.com/other.git"]])
        projects = config.build_host_enrollment["policy"]["principals"]["deploy"]["projects"]
        self.assertEqual(len(projects), 2)
        self.assertEqual(config.build_host_enrollment["policy"]["principals"]["other"], other)
        self.assertEqual(config.build_host_enrollment["max_records"], 256)
        slug = next(iter(first["policy"]["principals"]["deploy"]["projects"]))
        self.apply(config, build_project=specs, build_limit=[["memory_bytes", str(2 * 1024**3)]])
        limits = config.build_host_enrollment["policy"]["principals"]["deploy"]["projects"][slug]["limits"]
        self.assertEqual(limits["memory_bytes"], 2 * 1024**3)
        self.assertEqual(limits["cpu_percent"], 100)
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_builder_projects_for_separate_targets_can_reuse_routes_ports_and_roots(self):
        for collision in ("route", "udp", "port", "root"):
            destinations = (["example.com/a/b", "example.com/a_b"] if collision == "root" else
                            ["stage.example", "production.example"])
            items = [prepared(destination) for destination in destinations]
            for item in items:
                if collision == "route":
                    item.manifest.components[0].domain = "fixed.example"
                elif collision == "udp":
                    item.manifest.components[1].public_udp = [PublicUDP(3478)]
                elif collision == "port":
                    item.manifest.components[1].port = 8080
                elif collision == "root":
                    path = item.destination.removeprefix("example.com")
                    item.manifest.components[0].path = path
                    item.manifest.components[1].path = path + "/api"
            specs = [[destination, "https://example.com/site.git"] for destination in destinations]
            self.fetch.side_effect = lambda _specs, _root: copy.deepcopy(items)
            with self.subTest(collision=collision):
                builder = self.config(builder=True)
                self.apply(builder, build_project=specs)
                projects = builder.build_host_enrollment["policy"]["principals"]["deploy"]["projects"]
                self.assertEqual(len(projects), 2)
                target = self.config()
                target.deploy_specs = specs
                with self.assertRaises(ValueError):
                    self.apply(target)
                self.assertIsNone(target.build_target_enrollment)
                self.assertEqual(list(self.workspace.iterdir()), [])
        self.fetch.side_effect = lambda _specs, _root: [prepared(), prepared()]
        with self.assertRaisesRegex(ValueError, "collision"):
            self.apply(self.config(builder=True), build_project=[["example.com", "https://example.com/site.git"]])
        item = prepared()
        item.manifest.components[1].env_file = "/etc/secret"
        self.fetch.side_effect = lambda _specs, _root: [item]
        with self.assertRaises(ValueError):
            self.apply(self.config(builder=True), build_project=[["example.com", "https://example.com/site.git"]])

    def test_builder_rejects_legacy_executor_before_source_preparation(self):
        config = self.config(builder=True, enable_cicd=True)
        with self.assertRaisesRegex(ValueError, "--cicd"):
            self.apply(config, build_project=[["example.com", "https://example.com/site.git"]])
        self.fetch.assert_not_called()
        self.assertIsNone(config.build_host_enrollment)

    def test_target_updates_include_saved_siblings_preserve_limits_and_refuse_root_moves(self):
        config = self.config()
        self.apply(config)
        first = copy.deepcopy(config.build_target_enrollment)
        slug = next(iter(first["policy"]["applications"]))
        from tests.test_build_selection import public_key

        config.build_target_enrollment["keys"]["other"] = public_key(2)
        config.build_target_enrollment["policy"]["applications"][slug]["principals"].append("other")
        config.build_target_enrollment["policy"]["applications"][slug]["limits"]["artifact_bytes"] = 64 * 1024**2
        config.deploy_specs.append(["second.example", "https://example.com/other.git"])
        self.apply(config, build_target_auto=True, build_max_bytes=16 * 1024**3)
        applications = config.build_target_enrollment["policy"]["applications"]
        self.assertEqual(len(applications), 2)
        self.assertEqual(applications[slug]["deployment_root"], first["policy"]["applications"][slug]["deployment_root"])
        self.assertEqual(applications[slug]["limits"]["artifact_bytes"], 64 * 1024**2)
        self.assertEqual(applications[slug]["principals"], ["deploy", "other"])
        self.assertEqual(config.build_target_enrollment["max_bytes"], 16 * 1024**3)
        applications[slug]["deployment_root"] = "/srv/custom/root"
        before = copy.deepcopy(config.build_target_enrollment)
        with self.assertRaisesRegex(ValueError, "cannot change existing roots"):
            self.apply(config, build_target_auto=True)
        self.assertEqual(config.build_target_enrollment, before)
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_existing_enrollment_is_reused_without_source_or_key_access(self):
        config = self.config()
        self.apply(config)
        before = copy.deepcopy(config.build_target_enrollment)
        self.fetch.reset_mock()
        self.key.unlink()
        self.apply(config)
        self.fetch.assert_not_called()
        self.assertEqual(config.build_target_enrollment, before)

    def test_explicit_target_generation_reenables_saved_removal_only_after_success(self):
        config = self.config(disable_build_target=True)
        self.fetch.side_effect = ValueError("source failed")
        with self.assertRaisesRegex(ValueError, "source failed"):
            self.apply(config, build_target_auto=True)
        self.assertTrue(config.disable_build_target)
        self.assertIsNone(config.build_target_enrollment)
        self.fetch.side_effect = lambda specs, _root: [prepared(destination) for destination, _url, _ref in _plans(specs)]
        self.apply(config, build_target_auto=True)
        self.assertFalse(config.disable_build_target)
        self.assertIsNotNone(config.build_target_enrollment)
        self.fetch.reset_mock()
        with self.assertRaisesRegex(ValueError, "Choose --build-target-auto or --no-build-target"):
            self.apply(config, build_target_auto=True, disable_build_target=True)
        self.fetch.assert_not_called()

    def test_bad_flags_and_missing_private_identity_fail_before_fetch(self):
        for options in (
            {"build_limit": [["unknown", "1"]]},
            {"build_limit": [["cpu_percent", "0"]]},
            {"build_limit": [["cpu_percent", "100"], ["cpu_percent", "200"]]},
            {"build_limit": [["memory_bytes", "1"]]},
            {"build_max_records": 0},
            {"build_target_policy": "/unused/policy"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.apply(self.config(), **options)
            self.fetch.assert_not_called()
        builder = self.config(builder=True)
        builder.is_build_server = False
        with self.assertRaisesRegex(ValueError, "requires an enabled --build-server"):
            self.apply(builder, build_project=[["example.com", "https://example.com/site.git"]])
        self.fetch.assert_not_called()
        self.key.unlink()
        with self.assertRaisesRegex(ValueError, "Build policy preparation failed"):
            self.apply(self.config())
        self.fetch.assert_not_called()

    def test_source_failure_cleans_temporary_inputs_without_publishing_enrollment(self):
        config = self.config()
        self.fetch.side_effect = ValueError("source failed")
        with self.assertRaisesRegex(ValueError, "source failed"):
            self.apply(config)
        self.assertIsNone(config.build_target_enrollment)
        self.assertEqual(list(self.workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

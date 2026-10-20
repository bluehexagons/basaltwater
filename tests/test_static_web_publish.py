"""Tests for generic static-site publication on the internal HTTPS host."""

from __future__ import annotations

import argparse
import contextlib
import io
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.service_tools import static_web_publish
from common.service_tools import basaltwater_web
from lib import static_publication


def _args(project: str, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "json": False,
        "build_timeout": 3600,
        "no_build": True,
        "no_install": False,
        "open": False,
        "output": "dist",
        "project_option": project,
        "project_positional": None,
        "site": "demo",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class TestStaticWebPublish(unittest.TestCase):
    def setUp(self) -> None:
        source = patch("lib.static_publication.source_state", return_value={"commit": "a" * 40, "dirty": False})
        source.start()
        self.addCleanup(source.stop)
        self.environment = {"PATH": "/selected/node/bin:/usr/bin", "PROJECT_SETTING": "retained"}
        self.select_node = self.enterContext(patch.object(
            static_web_publish, "select_node",
            return_value=Mock(environment=Mock(return_value=self.environment)),
        ))

    def test_publishes_existing_output_atomically_and_updates_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project = os.path.join(temporary_dir, "project")
            output = os.path.join(project, "dist")
            sites_root = os.path.join(temporary_dir, "sites")
            user_root = os.path.join(sites_root, "agent")
            os.makedirs(output)
            os.makedirs(user_root)
            with open(os.path.join(project, "package.json"), "w", encoding="utf-8") as file_obj:
                json.dump({"name": "demo-site"}, file_obj)
            with open(os.path.join(output, "index.html"), "w", encoding="utf-8") as file_obj:
                file_obj.write("new site")
            old_site = os.path.join(user_root, "demo")
            os.mkdir(old_site)
            with open(os.path.join(old_site, "index.html"), "w", encoding="utf-8") as file_obj:
                file_obj.write("old site")
            url_file = os.path.join(temporary_dir, "base-url")
            with open(url_file, "w", encoding="utf-8") as file_obj:
                file_obj.write("https://sites.example:8443\n")
            account = SimpleNamespace(pw_name="agent", pw_uid=os.getuid())

            with (
                patch.object(static_web_publish, "SITES_ROOT", sites_root),
                patch.object(static_web_publish, "BASE_URL_FILE", url_file),
                patch.object(static_web_publish, "_current_account", return_value=account),
            ):
                result = static_web_publish.publish(_args(project))

            self.assertTrue(result["ok"])
            self.select_node.assert_not_called()
            self.assertEqual(result["url"], "https://sites.example:8443/sites/agent/demo/")
            with open(os.path.join(old_site, "index.html"), encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "new site")
            self.assertTrue(os.path.isfile(os.path.join(old_site, ".basaltwater.json")))
            receipt_path = Path(old_site) / static_publication.RECEIPT_FILE
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
            receipt = static_publication.read_receipt(old_site)
            self.assertEqual(receipt["source"], {"commit": "a" * 40, "dirty": False})
            self.assertFalse(receipt["build_performed"])
            self.assertEqual(set(receipt["artifacts"]), {"index.html"})
            self.assertNotIn("source", result["metadata"])
            with open(os.path.join(user_root, "index.html"), encoding="utf-8") as file_obj:
                self.assertIn("demo-site", file_obj.read())

    def test_build_installs_missing_dependencies_and_runs_package_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project = os.path.join(temporary_dir, "project")
            output = os.path.join(project, "dist")
            os.makedirs(project)
            with open(os.path.join(project, "package.json"), "w", encoding="utf-8") as file_obj:
                json.dump({"name": "demo", "scripts": {"build": "vite build"}}, file_obj)
            with open(os.path.join(project, "package-lock.json"), "w", encoding="utf-8"):
                pass

            commands: list[list[str]] = []

            def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                commands.append(command)
                if command == ["npm", "run", "build"]:
                    os.mkdir(output)
                    with open(os.path.join(output, "index.html"), "w", encoding="utf-8"):
                        pass
                return SimpleNamespace(returncode=0)

            with patch.object(static_web_publish, "run", side_effect=run) as execute:
                static_web_publish._run_project_build(project, {"scripts": {"build": "vite"}}, install=True)

            self.assertEqual(commands, [["npm", "ci"], ["npm", "run", "build"]])
            self.select_node.assert_called_once_with(project)
            self.assertEqual([call.kwargs["env"] for call in execute.call_args_list],
                             [self.environment, self.environment])

    def test_rejects_symlinks_in_static_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output = os.path.join(temporary_dir, "dist")
            os.mkdir(output)
            with open(os.path.join(output, "index.html"), "w", encoding="utf-8"):
                pass
            os.symlink("index.html", os.path.join(output, "linked.html"))

            with self.assertRaisesRegex(ValueError, "symlink"):
                static_web_publish._validate_output_tree(output)

    def test_json_publication_keeps_build_logs_on_stderr(self) -> None:
        for failure in (None, "runtime", "install", "build"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / "project"
                output = project / "dist"
                output.mkdir(parents=True)
                (output / "index.html").write_text("new site", encoding="utf-8")
                (project / "package.json").write_text(
                    json.dumps({"scripts": {"build": "vite build"}}), encoding="utf-8",
                )
                sites_root = Path(directory) / "sites"
                destination = sites_root / "agent" / "demo"
                destination.mkdir(parents=True)
                (destination / "index.html").write_text("old site", encoding="utf-8")
                stdout, stderr = io.StringIO(), io.StringIO()
                self.select_node.side_effect = ValueError("Project Node unavailable") if failure == "runtime" else None

                def run(command: list[str], **kwargs: object) -> SimpleNamespace:
                    phase = "build" if command == ["npm", "run", "build"] else "install"
                    print(f"{phase} log", file=kwargs.get("stdout"))
                    return SimpleNamespace(returncode=1 if phase == failure else 0)

                with (
                    patch.object(static_web_publish, "SITES_ROOT", str(sites_root)),
                    patch.object(static_web_publish, "_base_url", return_value=None),
                    patch.object(static_web_publish, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                    patch.object(static_web_publish, "run", side_effect=run) as execute,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    status = static_web_publish.main(["demo", "--project", str(project), "--json"])
                result = json.loads(stdout.getvalue())
                self.assertEqual(result["ok"], failure is None)
                self.assertEqual(status, 0 if failure is None else 2 if failure == "runtime" else 1)
                if failure == "runtime":
                    execute.assert_not_called()
                    self.assertIn("Project Node unavailable", result["error"])
                else:
                    self.assertIn("install log", stderr.getvalue())
                if failure in (None, "build"):
                    self.assertIn("build log", stderr.getvalue())
                self.assertEqual(
                    (destination / "index.html").read_text(encoding="utf-8"),
                    "new site" if failure is None else "old site",
                )

    def test_scan_failure_preserves_publication_before_copying(self) -> None:
        for unreadable_root in (False, True):
            with self.subTest(root=unreadable_root), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / "project"
                output = project / "dist"
                child = output / "unreadable"
                child.mkdir(parents=True)
                (output / "index.html").write_text("new site", encoding="utf-8")
                sites = root / "sites"
                destination = sites / "agent" / "demo"
                destination.mkdir(parents=True)
                (destination / "index.html").write_text("old site", encoding="utf-8")
                real_scandir = os.scandir
                failed_path = str(output if unreadable_root else child)

                def scan(path):
                    if os.fspath(path) == failed_path:
                        raise PermissionError(13, "Permission denied", failed_path)
                    return real_scandir(path)

                with (
                    patch.object(static_web_publish, "SITES_ROOT", str(sites)),
                    patch.object(static_web_publish, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                    patch.object(static_web_publish.os, "scandir", side_effect=scan),
                    patch.object(static_web_publish.shutil, "copytree") as copy,
                    patch.object(static_web_publish, "_replace_site") as activate,
                ):
                    with self.assertRaisesRegex(RuntimeError, "Could not validate static output"):
                        static_web_publish.publish(_args(str(project)))
                    copy.assert_not_called()
                    activate.assert_not_called()
                self.assertEqual((destination / "index.html").read_text(), "old site")

    def test_remove_requires_confirmation_and_owned_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            sites_root = os.path.join(temporary_dir, "sites")
            user_root = os.path.join(sites_root, "agent")
            site = os.path.join(user_root, "demo")
            os.makedirs(site)
            account = SimpleNamespace(pw_name="agent", pw_uid=os.getuid())
            with patch.object(static_web_publish, "SITES_ROOT", sites_root):
                with self.assertRaisesRegex(ValueError, "requires --yes"):
                    static_web_publish.remove_site(account, "demo", False)
                static_web_publish.remove_site(account, "demo", True)

            self.assertFalse(os.path.exists(site))

    def test_remove_acquires_publication_lock_before_inspecting_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "agent"
            root.mkdir()
            site = root / "demo"
            account = SimpleNamespace(pw_name="agent", pw_uid=os.getuid())

            def acquire(descriptor: int, operation: int) -> None:
                self.assertEqual(operation, static_web_publish.fcntl.LOCK_EX)
                self.assertEqual(
                    os.fstat(descriptor).st_ino,
                    (root / ".basaltwater-demo.lock").stat().st_ino,
                )
                # Model a publisher completing while removal waits for its lock.
                site.mkdir()

            with (
                patch.object(static_web_publish, "SITES_ROOT", directory),
                patch.object(static_web_publish.fcntl, "flock", side_effect=acquire) as lock,
                patch.object(static_web_publish, "write_user_catalog") as catalog,
            ):
                static_web_publish.remove_site(account, "demo", True)
                lock.assert_called_once()
                catalog.assert_called_once_with(str(root), "agent")
            self.assertFalse(site.exists())

    def test_verification_detects_stale_local_and_served_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "dist"
            output.mkdir()
            (output / "index.html").write_text("published", encoding="utf-8")
            static_publication.write_receipt(str(output), directory, output="dist", published_at="now", built=True)
            record = static_publication.read_receipt(str(output))["artifacts"]["index.html"]
            with patch.object(static_publication, "_served_hash", return_value=record) as fetch:
                result = static_publication.verify_receipt(str(output), url="https://vm.test/sites/agent/demo/", project=directory)
            self.assertTrue(result["ok"])
            self.assertTrue(result["source_matches"])
            self.assertFalse(result["rendering_verified"])
            self.assertEqual(result["served_artifacts_checked"], 1)
            self.assertEqual(fetch.call_args.args[0], "https://vm.test/sites/agent/demo/index.html")
            with patch.object(static_publication, "_served_hash", return_value={"size": 1, "sha256": "b" * 64}):
                result = static_publication.verify_receipt(str(output), url="https://vm.test/sites/agent/demo/")
            self.assertFalse(result["ok"])
            self.assertEqual(result["issues"][0]["component"], "served")
            (output / "index.html").write_text("changed locally", encoding="utf-8")
            result = static_publication.verify_receipt(str(output), url=None)
            self.assertEqual(result["issue"], "artifact_mismatch")
            self.assertEqual(result["issues"][0]["component"], "publication")

    def test_local_build_inventory_and_https_failure_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            publication = Path(directory) / "published"
            local = Path(directory) / "local"
            publication.mkdir()
            local.mkdir()
            (publication / "index.html").write_text("published", encoding="utf-8")
            (local / "index.html").write_text("new build", encoding="utf-8")
            static_publication.write_receipt(str(publication), directory, output="dist", published_at="now", built=True)
            result = static_publication.verify_receipt(str(publication), url=None, local_output=str(local))
            self.assertEqual(result["issues"][0]["component"], "local_build")
            for error in (TimeoutError("timed out"), static_publication.http.client.IncompleteRead(b"partial")):
                with self.subTest(error=type(error).__name__), patch.object(static_publication, "_served_hash", side_effect=error):
                    result = static_publication.verify_receipt(str(publication), url="https://vm.test/site/")
                self.assertEqual(result["issue"], "served_artifact_unavailable")
                self.assertFalse(result["ok"])

    def test_served_hash_reads_bounded_bytes_with_tls_and_identity_encoding(self) -> None:
        response = io.BytesIO(b"site")
        response.status = 200
        response.headers = {}
        opener = Mock()
        opener.open.return_value = response
        with (
            patch.object(static_publication.urllib.request, "build_opener", return_value=opener),
            patch.object(static_publication.ssl, "create_default_context") as tls,
            patch.object(static_publication.time, "monotonic", return_value=10),
        ):
            record = static_publication._served_hash("https://vm.test/index.html", 4, 20)
        self.assertEqual(record, {"size": 4, "sha256": hashlib.sha256(b"site").hexdigest()})
        self.assertEqual(opener.open.call_args.args[0].get_header("Accept-encoding"), "identity")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)
        tls.assert_called_once_with()

    def test_receipt_failure_does_not_activate_an_unverified_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "project" / "dist"
            output.mkdir(parents=True)
            (output / "index.html").write_text("new", encoding="utf-8")
            sites = Path(directory) / "sites"
            old = sites / "agent" / "demo"
            old.mkdir(parents=True)
            (old / "index.html").write_text("old", encoding="utf-8")
            with (
                patch.object(static_web_publish, "SITES_ROOT", str(sites)),
                patch.object(static_web_publish, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                patch.object(static_web_publish, "write_receipt", side_effect=OSError("receipt failed")),
                patch.object(static_web_publish, "_base_url", return_value=None),
            ):
                with self.assertRaisesRegex(OSError, "receipt failed"):
                    static_web_publish.publish(_args(str(output.parent)))
            self.assertEqual((old / "index.html").read_text(), "old")

    def test_oversized_receipt_preserves_the_previous_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "project" / "dist"
            output.mkdir(parents=True)
            (output / "index.html").write_text("new", encoding="utf-8")
            sites = Path(directory) / "sites"
            old = sites / "agent" / "demo"
            old.mkdir(parents=True)
            (old / "index.html").write_text("old", encoding="utf-8")
            static_publication.write_receipt(str(old), directory, output="dist", published_at="before", built=False)
            previous = (old / static_publication.RECEIPT_FILE).read_bytes()
            with (
                patch.object(static_web_publish, "SITES_ROOT", str(sites)),
                patch.object(static_web_publish, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                patch.object(static_web_publish, "_base_url", return_value=None),
                patch.object(static_publication, "MAX_RECEIPT_BYTES", 100),
            ):
                with self.assertRaisesRegex(ValueError, "receipt exceeds"):
                    static_web_publish.publish(_args(str(output.parent)))
            self.assertEqual((old / "index.html").read_text(), "old")
            self.assertEqual((old / static_publication.RECEIPT_FILE).read_bytes(), previous)
            self.assertTrue(static_publication.verify_receipt(str(old), url=None)["ok"])

    def test_verification_distinguishes_source_changes_and_unknown_dirty_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "index.html").write_text("site", encoding="utf-8")
            static_publication.write_receipt(directory, directory, output="dist", published_at="now", built=False)
            for source, ok, matches, warnings in (
                ({"commit": "b" * 40, "dirty": False}, False, False, []),
                ({"commit": "a" * 40, "dirty": True}, True, None, ["dirty_source_provenance_unverified"]),
                ({"commit": None, "dirty": None}, True, None, ["source_revision_unavailable"]),
            ):
                with self.subTest(source=source), patch.object(static_publication, "source_state", return_value=source):
                    result = static_publication.verify_receipt(directory, url=None, project=directory)
                self.assertEqual((result["ok"], result["source_matches"], result["warnings"]), (ok, matches, warnings))

    def test_verification_refuses_unsafe_receipt_paths_and_public_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "index.html").write_text("site", encoding="utf-8")
            static_publication.write_receipt(directory, directory, output="dist", published_at="now", built=False)
            path = Path(directory) / static_publication.RECEIPT_FILE
            receipt = json.loads(path.read_text())
            for unsafe in ("..", "../secret", "/secret", "assets/../secret"):
                modified = {**receipt, "artifacts": {**receipt["artifacts"], unsafe: receipt["artifacts"]["index.html"]}}
                path.write_text(json.dumps(modified), encoding="utf-8")
                with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                    static_publication.read_receipt(directory)
            path.write_text(json.dumps(receipt), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private"):
                static_publication.read_receipt(directory)

    def test_verify_command_missing_receipt_is_json_and_never_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sites = Path(directory) / "agent" / "legacy"
            sites.mkdir(parents=True)
            (sites / "index.html").write_text("legacy site", encoding="utf-8")
            stdout = io.StringIO()
            with (
                patch.object(static_web_publish, "SITES_ROOT", directory),
                patch.object(static_web_publish, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                patch.object(static_web_publish, "publish") as publish,
                contextlib.redirect_stdout(stdout),
            ):
                code = basaltwater_web.main(["site", "verify", "legacy", "--local-only", "--json"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stdout.getvalue())["issue"], "receipt_missing")
            publish.assert_not_called()

    def test_site_lock_rejects_links_and_special_files_without_chmod(self) -> None:
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                outside = root / "outside"
                outside.write_text("preserve", encoding="utf-8")
                outside.chmod(0o644)
                lock_path = root / ".basaltwater-demo.lock"
                if kind == "symlink":
                    lock_path.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, lock_path)
                else:
                    os.mkfifo(lock_path)
                with self.assertRaises((OSError, RuntimeError)):
                    with static_web_publish._site_lock(directory, "demo"):
                        self.fail("Unsafe lock was accepted")
                self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")
                self.assertEqual(outside.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()

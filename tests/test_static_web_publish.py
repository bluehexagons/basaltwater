"""Tests for generic static-site publication on the internal HTTPS host."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.service_tools import static_web_publish


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
            self.assertEqual(result["url"], "https://sites.example:8443/sites/agent/demo/")
            with open(os.path.join(old_site, "index.html"), encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "new site")
            self.assertTrue(os.path.isfile(os.path.join(old_site, ".basaltwater.json")))
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

            with patch.object(static_web_publish, "run", side_effect=run):
                static_web_publish._run_project_build(project, {"scripts": {"build": "vite"}}, install=True)

            self.assertEqual(commands, [["npm", "ci"], ["npm", "run", "build"]])

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
        for failure in (None, "install", "build"):
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

                def run(command: list[str], **kwargs: object) -> SimpleNamespace:
                    phase = "build" if command == ["npm", "run", "build"] else "install"
                    print(f"{phase} log", file=kwargs.get("stdout"))
                    return SimpleNamespace(returncode=1 if phase == failure else 0)

                with (
                    patch.object(static_web_publish, "SITES_ROOT", str(sites_root)),
                    patch.object(static_web_publish, "_base_url", return_value=None),
                    patch.object(static_web_publish, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                    patch.object(static_web_publish, "run", side_effect=run),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    status = static_web_publish.main(["demo", "--project", str(project), "--json"])
                result = json.loads(stdout.getvalue())
                self.assertEqual(result["ok"], failure is None)
                self.assertEqual(status, 0 if failure is None else 1)
                self.assertIn("install log", stderr.getvalue())
                if failure != "install":
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

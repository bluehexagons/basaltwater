"""Account ownership, private writes, and credential form boundaries."""

from __future__ import annotations

from contextlib import ExitStack
from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import urllib.parse
from unittest.mock import Mock, patch

from common.service_tools import web_panel_credentials as view
from common.service_tools import web_panel_service as panel
from lib import agent_git_settings as settings


class GitSettingsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        previous_umask = os.umask(0o077)
        self.stack.callback(os.umask, previous_umask)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.manager = settings.AgentGitSettings(str(self.home), "operator")
        self.stack.enter_context(patch.object(self.manager, "available", return_value=True))
        self.stack.enter_context(patch.object(settings.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name))
        # CI runs as root; fixtures still exercise ownership checks for its actual UID.
        self.stack.enter_context(patch.object(settings.os, "geteuid", return_value=self.home.stat().st_uid))
        self.run = self.stack.enter_context(patch.object(settings.subprocess, "run"))
        self.run.side_effect = self.git
        self.commands = []

    def git(self, argv, **kwargs):
        self.commands.append(argv)
        path = Path(argv[argv.index("--file") + 1])
        if "--get" in argv:
            key = argv[-1]
            # Fake only fixed identity reads; never execute a host Git command.
            values = {"user.name": "Fixture Name", "user.email": "fixture@example.test"}
            return subprocess.CompletedProcess(argv, 0, values[key] + "\n", "")
        with path.open("a") as output:
            output.write("# edited " + argv[-2] + "\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def credential(self, text="github.com:\n    oauth_token: old-private-token\n"):
        self.manager.github_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.manager.github_file.write_text(text)
        self.manager.github_file.chmod(0o600)

    def test_identity_update_is_private_atomic_and_preserves_unrelated_config(self):
        self.manager.git_file.write_text('[core]\n\teditor = fixture-editor\n')
        self.manager.set_identity("Fixture Name", "fixture@example.test")
        self.assertIn("editor = fixture-editor", self.manager.git_file.read_text())
        self.assertEqual(self.manager.git_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual([argv[-2:] for argv in self.commands], [["user.name", "Fixture Name"], ["user.email", "fixture@example.test"]])
        self.assertFalse(Path(str(self.manager.git_file) + ".lock").exists())
        self.assertEqual(list(self.home.glob(".basaltwater-git-*")), [])

    def test_failed_second_identity_edit_keeps_original(self):
        original = '[user]\n\tname = Old\n'
        self.manager.git_file.write_text(original)
        self.run.side_effect = [subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 1, "", "private vendor error")]
        with self.assertRaisesRegex(RuntimeError, "existing settings were retained"):
            self.manager.set_identity("New", "new@example.test")
        self.assertEqual(self.manager.git_file.read_text(), original)
        self.assertFalse(Path(str(self.manager.git_file) + ".lock").exists())

    def test_invalid_fields_make_no_changes(self):
        for name, email in (("Name\nother", "name@example.test"), ("Name", "not an email"), ("", "name@example.test")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.manager.set_identity(name, email)
        self.run.assert_not_called()
        self.assertFalse(self.manager.git_file.exists())

    def test_new_token_preserves_other_hosts_and_never_enters_git_argv(self):
        other = "enterprise.example.test:\n    oauth_token: other-private-token\n"
        self.credential(other + "github.com:\n    user: old-account\n    oauth_token: old-private-token\n")
        self.manager.set_github_token("github_pat_fixture-secret")
        content = self.manager.github_file.read_text()
        self.assertIn(other, content)
        self.assertIn("github_pat_fixture-secret", content)
        self.assertNotIn("old-private-token", content)
        self.assertNotIn("old-account", content)
        self.assertEqual(content.count("github.com:"), 1)
        self.assertEqual(self.manager.github_file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("fixture-secret", str(self.run.call_args_list))
        self.assertIn(["credential.https://github.com.helper", "!/usr/bin/gh auth git-credential"], [argv[-2:] for argv in self.commands])
        snapshot = self.manager.snapshot()
        self.assertTrue(snapshot["github_present"])
        self.assertNotIn("private-token", str(snapshot))
        self.assertNotIn("fixture-secret", str(snapshot))

    def test_remove_preserves_other_host_credentials_and_does_not_run_tools(self):
        other = "enterprise.example.test:\n    oauth_token: other-private-token\n"
        self.credential("'github.com':\n    oauth_token: old-private-token\n" + other)
        self.manager.remove_github_token()
        self.assertEqual(self.manager.github_file.read_text(), other)
        self.run.assert_not_called()

    def test_missing_gh_or_failed_git_configuration_preserves_token(self):
        self.credential()
        with patch.object(settings.shutil, "which", return_value=None), self.assertRaises(RuntimeError):
            self.manager.set_github_token("new-secret")
        self.run.return_value = subprocess.CompletedProcess([], 1, "", "token=new-secret")
        self.run.side_effect = None
        with self.assertRaises(RuntimeError):
            self.manager.set_github_token("new-secret")
        self.assertIn("old-private-token", self.manager.github_file.read_text())
        self.assertNotIn("new-secret", self.manager.github_file.read_text())

    def test_merged_credentials_remain_within_the_read_limit(self):
        original = "enterprise.example.test:\n    oauth_token: other-private-token\n"
        self.credential(original)
        with patch.object(settings, "_MAX_BYTES", len(original.encode()) + 1), self.assertRaisesRegex(ValueError, "size limit"):
            self.manager.set_github_token("new-secret")
        self.assertEqual(self.manager.github_file.read_text(), original)
        self.run.assert_not_called()

    def test_config_changed_outside_git_lock_is_not_overwritten(self):
        self.manager.git_file.write_text("# original\n")
        def change(argv, **kwargs):
            self.manager.git_file.write_text("# concurrent\n")
            return self.git(argv, **kwargs)
        self.run.side_effect = change
        with self.assertRaisesRegex(RuntimeError, "changed during"):
            self.manager.set_identity("New", "new@example.test")
        self.assertEqual(self.manager.git_file.read_text(), "# concurrent\n")

    def test_symlink_config_and_parent_are_rejected_without_touching_target(self):
        outside = self.home / "outside"
        outside.write_text("unchanged")
        self.manager.git_file.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.manager.set_identity("Name", "name@example.test")
        self.assertEqual(outside.read_text(), "unchanged")
        self.manager.git_file.unlink()
        (self.home / ".config").symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.manager.set_github_token("new-secret")
        self.run.assert_not_called()

    def test_public_credential_and_existing_git_lock_fail_closed(self):
        self.credential()
        self.manager.github_file.chmod(0o644)
        snapshot = self.manager.snapshot()
        self.assertTrue(snapshot["github_error"])
        with self.assertRaises(RuntimeError):
            self.manager.set_github_token("new-secret")
        lock = Path(str(self.manager.git_file) + ".lock")
        lock.write_text("existing lock")
        with self.assertRaises(FileExistsError):
            self.manager.set_identity("Name", "name@example.test")
        self.assertEqual(lock.read_text(), "existing lock")

    def test_snapshot_reads_only_global_files_without_network_or_local_config(self):
        (self.home / ".config/git").mkdir(parents=True)
        (self.home / ".config/git/config").write_text("# XDG global\n")
        self.manager.git_file.write_text("# legacy global\n")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/other-secret-config", "GIT_CONFIG_COUNT": "1", "GIT_DIR": "/other-repository"}):
            snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["name"], "Fixture Name")
        for call in self.run.call_args_list:
            self.assertIn("--file", call.args[0])
            self.assertNotIn("--includes", call.args[0])
            self.assertEqual(call.kwargs["env"]["HOME"], str(self.home))
            self.assertNotIn("GIT_CONFIG_GLOBAL", call.kwargs["env"])
            self.assertNotIn("GIT_DIR", call.kwargs["env"])

    def test_duplicate_host_entries_rejected_without_editing(self):
        original = "github.com:\n  oauth_token: old\n\"github.com\":\n  oauth_token: other\n"
        self.credential(original)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.manager.set_github_token("new-secret")
        self.assertEqual(self.manager.github_file.read_text(), original)
        self.run.assert_not_called()

    def test_commented_header_is_replaced_and_inline_host_mapping_is_rejected(self):
        self.credential('"github.com": # account\n    oauth_token: old-private-token\n')
        self.manager.set_github_token("new-secret")
        self.assertEqual(self.manager.github_file.read_text().count("github.com"), 1)
        original = 'github.com: {oauth_token: old-private-token}\n'
        self.credential(original)
        self.run.reset_mock()
        with self.assertRaisesRegex(ValueError, "block host"):
            self.manager.set_github_token("new-secret")
        self.assertEqual(self.manager.github_file.read_text(), original)
        self.run.assert_not_called()


class AccountAvailabilityTests(unittest.TestCase):
    def test_matching_non_root_account_with_owned_home_is_available(self):
        with tempfile.TemporaryDirectory() as home:
            manager = settings.AgentGitSettings(home, "operator")
            account = SimpleNamespace(pw_uid=1000, pw_dir=home)
            with patch.object(settings.os, "geteuid", return_value=1000), patch.object(settings.pwd, "getpwnam", return_value=account), patch.object(settings.Path, "stat", return_value=SimpleNamespace(st_uid=1000, st_mode=0o40700)):
                self.assertTrue(manager.available())

    def test_root_other_user_and_wrong_home_are_unavailable(self):
        with tempfile.TemporaryDirectory() as home:
            manager = settings.AgentGitSettings(home, "operator")
            for uid, account_uid, account_home in ((0, 0, home), (1000, 1001, home), (1000, 1000, "/other/home")):
                with self.subTest(uid=uid, account_uid=account_uid), patch.object(settings.os, "geteuid", return_value=uid), patch.object(settings.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=account_uid, pw_dir=account_home)), patch.object(settings.subprocess, "run") as run:
                    self.assertFalse(manager.available())
                    self.assertFalse(manager.snapshot()["available"])
                    with self.assertRaises(RuntimeError):
                        manager.set_identity("Name", "name@example.test")
                    run.assert_not_called()


class CredentialViewTests(unittest.TestCase):
    def setUp(self):
        self.state = panel.WebPanelState({"host": "vm.example.test", "username": "operator", "system_type": "server_dev", "features": {}, "services": [], "access": [], "panel_url": "https://vm.example.test/"}, agent_home="/home/operator")
        self.snapshot = {"available": True, "name": "Fixture <Name>", "email": "fixture@example.test", "git_error": "", "github_error": "", "github_present": True}
        self.patch = patch.object(self.state.git_settings, "snapshot", return_value=self.snapshot)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def handler(self, path, values=None, *, proto="https"):
        handler = object.__new__(panel.WebPanelHandler)
        handler.state = self.state
        handler.path = path
        body = urllib.parse.urlencode(values or {}, doseq=True).encode()
        handler.rfile = BytesIO(body)
        handler.headers = {"Content-Length": str(len(body)), "X-Forwarded-Proto": proto}
        handler._send = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_page_has_identity_token_forms_and_no_token_prefill(self):
        page = view.render_credentials(self.state, panel._PAGE_STYLE, {})
        self.assertIn("Fixture &lt;Name&gt;", page)
        self.assertIn('name="token" type="password"', page)
        self.assertNotIn('name="token" value=', page)
        self.assertIn('href="/credentials" aria-current="page"', page)
        self.assertIn("provider acceptance", page)
        self.assertIn("authorize its code", page)
        self.assertNotIn("<script", page)

    def test_get_does_not_change_settings_or_contact_provider(self):
        handler = self.handler("/credentials")
        with patch.object(self.state.git_settings, "set_identity") as identity, patch.object(self.state.git_settings, "set_github_token") as github:
            handler.do_GET()
        identity.assert_not_called()
        github.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.OK)

    def test_identity_and_token_posts_redirect_without_secret(self):
        for action, values, method, result in (
            ("identity", {"name": "Name", "email": "name@example.test"}, "set_identity", "identity"),
            ("github", {"token": "github_pat_never-echo-me"}, "set_github_token", "github"),
            ("github-remove", {"confirmation": "remove"}, "remove_github_token", "removed"),
        ):
            with self.subTest(action=action), patch.object(self.state.git_settings, method) as write:
                handler = self.handler("/actions/credentials/" + action, {"csrf": self.state.csrf_token, **values})
                handler.do_POST()
                write.assert_called_once()
                handler.send_response.assert_called_once_with(panel.HTTPStatus.SEE_OTHER)
                handler.send_header.assert_any_call("Location", "/credentials?saved=" + result)
                self.assertNotIn("never-echo-me", str(handler.send_header.call_args_list))

    def test_duplicate_unknown_csrf_and_query_fields_never_mutate(self):
        valid = {"csrf": self.state.csrf_token, "token": "fixture-secret"}
        for values, query, expected in (({**valid, "csrf": "wrong"}, "", 403), ({**valid, "token": ["one", "two"]}, "", 400), ({**valid, "path": "/etc/secret"}, "", 400), (valid, "?token=secret", 400)):
            with self.subTest(values=values), patch.object(self.state.git_settings, "set_github_token") as write:
                handler = self.handler("/actions/credentials/github" + query, values)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0].value, expected)
                write.assert_not_called()

    def test_http_manifest_and_wrong_proxy_scheme_reject_tokens(self):
        for url, proto in (("http://vm.example.test/", "https"), ("https://vm.example.test/", "http"), (None, "https")):
            self.state.manifest["panel_url"] = url
            with self.subTest(url=url), patch.object(self.state.git_settings, "set_github_token") as write:
                handler = self.handler("/actions/credentials/github", {"csrf": self.state.csrf_token, "token": "fixture-secret"}, proto=proto)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.FORBIDDEN)
                write.assert_not_called()
        page = view.render_credentials(self.state, panel._PAGE_STYLE, {})
        self.assertIn('disabled aria-describedby="github-transport-help"', page)
        self.assertIn("Token changes require", page)

    def test_token_limits_and_removal_confirmation_prevent_writes(self):
        for action, values in (("github", {"token": "s" * 8193}), ("github-remove", {"confirmation": "yes"})):
            with self.subTest(action=action), patch.object(self.state.git_settings, "set_github_token") as token, patch.object(self.state.git_settings, "remove_github_token") as remove:
                handler = self.handler("/actions/credentials/" + action, {"csrf": self.state.csrf_token, **values})
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.UNPROCESSABLE_ENTITY)
                token.assert_not_called()
                remove.assert_not_called()
        handler = self.handler("/actions/credentials/github", {"csrf": self.state.csrf_token, "token": "s" * 16384})
        with patch.object(self.state.git_settings, "set_github_token") as token:
            handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        token.assert_not_called()

    def test_error_response_never_echoes_secret_or_vendor_output(self):
        with patch.object(self.state.git_settings, "set_github_token", side_effect=RuntimeError("private vendor token=fixture-secret")):
            handler = self.handler("/actions/credentials/github", {"csrf": self.state.csrf_token, "token": "fixture-secret"})
            handler.do_POST()
        status, page = handler._send.call_args.args[:2]
        self.assertEqual(status, panel.HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertNotIn("fixture-secret", page)
        self.assertNotIn("private vendor", page)
        self.assertIn("re-enter a token", page)

    def test_root_panel_displays_controller_guidance_without_edit_controls(self):
        self.snapshot["available"] = False
        page = view.render_credentials(self.state, panel._PAGE_STYLE, {})
        self.assertIn("dedicated service account", page)
        self.assertNotIn("Save commit identity", page)
        self.assertNotIn("Save or replace token", page)

    def test_query_allows_only_public_result_labels(self):
        for raw in ("token=private", "saved=github&saved=identity", "saved=private", "saved=github&path=/tmp", "x" * 65):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                view.parse_credentials_query(raw)
        self.assertEqual(view.parse_credentials_query("saved=identity"), {"saved": "identity"})

"""Account lifecycle approvals retain Unix identities and fail closed on drift."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib.atomic_io import write_json_atomic
from lib.operation_state import OperationStateStore
from lib.privilege_policy import operation_plan
from lib.samba_accounts import validate_account_snapshot, validate_lifecycle_snapshot
from smb import account_lifecycle as lifecycle, password_staging as staging
from smb import share_management
from common.service_tools import web_panel_samba as panel, web_panel_storage as storage
from tests import test_web_panel_samba as samba_tests
from tests.test_web_panel_samba import parameters, policy, row


def identity(username="alice", uid=1000):
    return SimpleNamespace(pw_name=username, pw_uid=uid, pw_gid=uid, pw_passwd="x",
                           pw_shell="/usr/sbin/nologin", pw_dir="/home/" + username)


class LifecycleContractTests(unittest.TestCase):
    def test_missing_python_bindings_are_a_fixed_error(self):
        with patch.dict(sys.modules, {"samba.samba3": None}), self.assertRaisesRegex(RuntimeError, "Python bindings"):
            lifecycle._bindings()

    def test_public_plans_distinguish_new_existing_and_samba_only_effects(self):
        for action, expected in (("create", "locked Unix password"), ("add-login", "existing local Unix account"),
                                 ("remove", "Delete only the Samba login")):
            with self.subTest(action=action):
                plan = operation_plan(policy(), 1000, "samba.account", {**parameters(), "action": action})
                self.assertEqual(plan["mode"], "approve")
                self.assertIn(expected, plan["effect"])
                self.assertNotIn("userdel", str(plan["argv"]))

    def test_optional_complete_identity_choices_are_validated_without_private_fields(self):
        value = {"available": True, "revision": "c" * 64,
                 "unix_accounts": [{"username": "bob", "uid": 1001}], "removable": ["alice"]}
        validate_account_snapshot({"available": True, "accounts": [row()], "lifecycle": value})
        for changed in ({"unix_accounts": [{"username": "alice", "uid": 1000}]},
                        {"unix_accounts": [{"username": "root", "uid": 0}]}, {"removable": ["unknown"]},
                        {"removable": ["alice", "alice"]}, {"password": "PRIVATE"},
                        {"unix_accounts": [{"username": "bob", "uid": True}]}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_lifecycle_snapshot({**value, **changed}, {"alice"})
        validate_account_snapshot({"available": True, "accounts": [row()]})
        validate_account_snapshot({"available": True, "accounts": [row()], "lifecycle": {"available": False}})


class LocalIdentityTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "passwd"
        self.enterContext(patch.object(lifecycle, "PASSWD_FILE", str(self.path)))
        self.stamp = self.enterContext(patch.object(lifecycle, "_stamp", return_value=(1,)))
        self.lookup = self.enterContext(patch.object(lifecycle.pwd, "getpwnam", return_value=identity()))

    def test_local_inventory_projects_only_names_and_uids_and_refuses_nss_mismatch(self):
        self.path.write_text("root:x:0:0:PRIVATE ROOT:/root:/bin/bash\nalice:x:1000:1000:PRIVATE:/home/alice:/bin/bash\n")
        self.assertEqual(lifecycle._local_accounts(), [{"username": "alice", "uid": 1000}])
        self.lookup.return_value = identity(uid=1001)
        with self.assertRaises(ValueError):
            lifecycle._local_accounts()
        self.lookup.assert_called_with("alice")

    def test_malformed_duplicate_changed_and_oversize_inventory_is_not_empty_success(self):
        for content in ("bad", "alice:x:1000:1000::/:/bin/bash\nalice:x:1001:1001::/:/bin/bash\n",
                        "alice:x:１０００:1000::/:/bin/bash", "x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(content)):
                self.path.write_text(content)
                with self.assertRaises(ValueError):
                    lifecycle._local_accounts()
        self.path.write_text("alice:x:1000:1000::/:/bin/bash")
        self.stamp.side_effect = [(1,), (2,)]
        with self.assertRaises(ValueError):
            lifecycle._local_accounts()


class LifecycleExportTests(unittest.TestCase):
    def setUp(self):
        self.saved = {"enable_samba": True, "samba_shares": [["write", "test", "/srv/data", "alice:PRIVATE"]]}
        @contextmanager
        def owner(**_kwargs):
            yield None, self.saved, (1,)
        self.enterContext(patch.object(share_management, "_owner", side_effect=owner))
        self.revision = self.enterContext(patch.object(lifecycle, "_creation_revision", return_value="c" * 64))
        self.local = self.enterContext(patch.object(lifecycle, "_local_accounts", return_value=[
            {"username": "alice", "uid": 1000}, {"username": "bob", "uid": 1001}]))
        self.rows = self.enterContext(patch.object(lifecycle, "_accounts", return_value=[row()]))

    def test_complete_choices_exclude_existing_samba_users_and_attached_logins(self):
        exported = lifecycle.collect_lifecycle_snapshot(self.saved, [row()])
        self.assertEqual(exported, {"available": True, "revision": "c" * 64,
            "unix_accounts": [{"username": "bob", "uid": 1001}], "removable": []})
        self.assertNotIn("PRIVATE", str(exported))
        self.saved["samba_shares"] = None
        self.assertEqual(lifecycle.collect_lifecycle_snapshot(self.saved, [row()])["removable"], ["alice"])

    def test_drift_or_unsupported_identity_data_is_unavailable_without_empty_success(self):
        self.revision.side_effect = ["c" * 64, "d" * 64]
        self.assertEqual(lifecycle.collect_lifecycle_snapshot(self.saved, [row()]), {"available": False})
        self.revision.side_effect = None
        self.local.side_effect = ValueError("PRIVATE")
        self.assertEqual(lifecycle.collect_lifecycle_snapshot(self.saved, [row()]), {"available": False})
        self.assertEqual(lifecycle.collect_lifecycle_snapshot({}, [row()]), {"available": False})


class LifecycleExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.marker = str(Path(directory) / "setup-operation.json")
        self.config = str(Path(directory) / "setup.json")
        self.saved = {"system_type": "server_samba", "enable_samba": True, "samba_shares": None,
                      "unrelated": "preserve", "share_credentials": [["alice", "PRIVATE-old"], ["bob", "PRIVATE-other"]]}
        write_json_atomic(self.config, self.saved)
        self.enterContext(patch.object(lifecycle, "SETUP_CONFIG_FILE", self.config))
        self.enterContext(patch("lib.atomic_io.os.fchown"))
        self.stamp = self.enterContext(patch.object(lifecycle, "_stamp", return_value=(1,)))
        self.identities = self.enterContext(patch.object(lifecycle, "_identity", return_value=identity()))
        self.local = self.enterContext(patch.object(lifecycle, "_local_accounts", return_value=[{"username": "alice", "uid": 1000}]))
        self.revision = self.enterContext(patch.object(lifecycle, "_creation_revision", return_value="a" * 64))
        self.rows = self.enterContext(patch.object(lifecycle, "_accounts", side_effect=[[row()], []]))
        self.consume = self.enterContext(patch.object(staging, "consume_password", return_value="PRIVATE-new"))
        self.run = self.enterContext(patch.object(lifecycle.subprocess, "run"))
        self.database = Mock()
        self.account = SimpleNamespace(username="alice", acct_ctrl=0x11)
        self.database.getsampwnam.return_value = self.account
        self.passdb = SimpleNamespace(error=type("PassdbError", (Exception,), {}),
                                      set_smb_config=Mock(), PDB=Mock(return_value=self.database))
        self.enterContext(patch.object(lifecycle, "_bindings", return_value=self.passdb))

    def change(self, action="remove", *, saved=None, rows=None, revision="a" * 64):
        with OperationStateStore(self.marker).ownership() as store, \
                patch.object(lifecycle, "load_setup_config", return_value=self.saved if saved is None else saved):
            lifecycle.change_lifecycle({**parameters(), "action": action, "revision": revision}, store,
                                       self.saved if saved is None else saved, [row()] if rows is None else rows)

    def test_removal_retains_unix_identity_and_only_clears_its_saved_password(self):
        self.change()
        self.database.delete_sam_account.assert_called_once_with(self.account)
        self.database.delete_user.assert_not_called()
        self.database.create_user.assert_not_called()
        self.run.assert_not_called()
        self.consume.assert_not_called()
        saved = json.loads(Path(self.config).read_text())
        self.assertEqual(saved, {**self.saved, "share_credentials": [["bob", "PRIVATE-other"]]})
        self.assertFalse(Path(self.marker).exists())
        result = Path(self.marker + ".last.json").read_text()
        self.assertNotIn("PRIVATE", result)
        self.assertIn('"outcome": "succeeded"', result)

    def test_referenced_stale_ineligible_and_unsupported_state_has_no_effect(self):
        for saved, rows, revision in (
            ({**self.saved, "samba_shares": [["write", "test", "/srv/data", "alice:PRIVATE,bob:PRIVATE"]]}, [row()], "a" * 64),
            (self.saved, [row()], "c" * 64), (self.saved, [{**row(), "manageable": False}], "a" * 64),
            ({**self.saved, "share_credentials": "PRIVATE"}, [row()], "a" * 64),
            ({**self.saved, "samba_shares": "invalid"}, [row()], "a" * 64),
        ):
            with self.subTest(saved=saved), self.assertRaises(ValueError):
                self.change(saved=saved, rows=rows, revision=revision)
        self.passdb.PDB.assert_not_called()
        self.run.assert_not_called()
        self.consume.assert_not_called()
        self.assertFalse(Path(self.marker).exists())

    def test_new_identity_is_locked_nologin_and_password_never_crosses_argv_or_marker(self):
        self.identities.side_effect = [None, identity(), identity(), identity()]
        self.local.return_value = []
        self.rows.side_effect = [[], [row()]]
        self.change("create", rows=[])
        argv = self.run.call_args.args[0]
        self.assertEqual(argv[0], "/usr/sbin/useradd")
        self.assertIn("--no-create-home", argv)
        self.assertEqual(argv[argv.index("--password") + 1], "!")
        self.assertEqual(argv[argv.index("--shell") + 1], "/usr/sbin/nologin")
        self.assertEqual([argv[index + 1] for index, argument in enumerate(argv) if argument == "--key"],
                         ["UID_MIN=1000", "UID_MAX=65533"])
        self.assertNotIn("PRIVATE", str(self.run.call_args))
        self.consume.assert_called_once()
        self.database.create_user.assert_called_once_with("alice", 0x10)
        self.assertEqual((self.account.plaintext_passwd, self.account.acct_ctrl), ("PRIVATE-new", 0x10))
        self.database.update_sam_account.assert_called_once_with(self.account)
        self.assertEqual(json.loads(Path(self.config).read_text()), self.saved)
        self.assertNotIn("PRIVATE", Path(self.marker + ".last.json").read_text())

    def test_adding_login_uses_existing_identity_without_unix_commands(self):
        self.rows.side_effect = [[], [row()]]
        self.change("add-login", rows=[])
        self.run.assert_not_called()
        self.database.create_user.assert_called_once_with("alice", 0x10)
        self.assertFalse(Path(self.marker).exists())

    def test_unexpected_identity_collision_or_changed_review_never_consumes_secret(self):
        for action, rows, revision in (("create", [], "a" * 64), ("add-login", [row()], "a" * 64),
                                        ("add-login", [], "c" * 64)):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.change(action, rows=rows, revision=revision)
        self.identities.return_value = None
        with self.assertRaises(ValueError):
            self.change("add-login", rows=[])
        self.consume.assert_not_called()
        self.run.assert_not_called()
        self.passdb.PDB.assert_not_called()

    def test_drift_before_effects_refuses_and_drift_after_effects_retains_guard(self):
        self.rows.side_effect = [[{**row(), "revision": "c" * 64}]]
        with self.assertRaises(ValueError):
            self.change()
        self.assertFalse(Path(self.marker).exists())
        self.database.delete_sam_account.assert_not_called()
        self.rows.side_effect = [[row()], []]
        self.stamp.side_effect = [(1,), (1,), (1,), (1,), (2,)]
        with self.assertRaisesRegex(ValueError, "Saved configuration changed"):
            self.change()
        marker = json.loads(Path(self.marker).read_text())
        self.assertEqual((marker["status"], marker["phase"]), ("recovery_required", "saving"))
        self.assertEqual(json.loads(Path(self.config).read_text()), self.saved)

    def test_interrupted_or_unverified_mutation_retains_public_recovery_evidence(self):
        self.rows.side_effect = [[row()], [row()]]
        with self.assertRaisesRegex(ValueError, "could not be verified"):
            self.change()
        marker = json.loads(Path(self.marker).read_text())
        self.assertEqual(marker["status"], "recovery_required")
        self.assertNotIn("PRIVATE", json.dumps(marker))

    def test_binding_diagnostics_are_sanitized_and_missing_bindings_precede_consumption(self):
        self.rows.side_effect = [[], [row()]]
        self.database.create_user.side_effect = self.passdb.error("PRIVATE diagnostics")
        with self.assertRaisesRegex(RuntimeError, "^Samba account lifecycle change failed$"):
            self.change("add-login", rows=[])
        self.assertNotIn("PRIVATE", Path(self.marker).read_text())
        self.consume.reset_mock()
        with patch.object(lifecycle, "_bindings", side_effect=RuntimeError("missing")), self.assertRaises(RuntimeError):
            self.change("add-login", rows=[])
        self.consume.assert_not_called()

    def test_unix_creation_timeout_keeps_guard_without_attempting_password_database(self):
        self.identities.return_value = None
        self.local.return_value = []
        self.rows.side_effect = [[]]
        self.run.side_effect = subprocess.TimeoutExpired("useradd", 15)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.change("create", rows=[])
        marker = json.loads(Path(self.marker).read_text())
        self.assertEqual((marker["status"], marker["phase"]), ("recovery_required", "creating_unix"))
        self.passdb.PDB.assert_not_called()
        self.database.create_user.assert_not_called()

class LifecyclePanelTests(unittest.TestCase):
    handler = samba_tests.SambaPanelTests.handler

    def setUp(self):
        samba_tests.SambaPanelTests.setUp(self)
        self.state.credential_transport_available = lambda: True
        self.inventory = {"available": True, "accounts": [row()], "lifecycle": {
            "available": True, "revision": "c" * 64, "unix_accounts": [{"username": "bob", "uid": 1001}],
            "removable": ["alice"]}}
        self.snapshot = self.enterContext(patch.object(storage, "load_storage_snapshot", return_value={
            "available": True, "generated_at": "2026-10-31T12:00:00+00:00", "samba": self.inventory, "jobs": [], "omitted_jobs": 0}))
        self.enterContext(patch("common.service_tools.web_panel_service.os.geteuid", return_value=1000))
        self.rpc = self.enterContext(patch.object(panel, "exchange", side_effect=lambda message: {
            "job_id": message["parameters"]["job_id"], "expires": panel.time.time() + 3600} if message["action"] == "samba-stage-password"
            else {"available": True, "requests": []}))

    def fields(self, action):
        return {"action": action, "username": {"create": "carol", "add-login": "bob", "remove": "alice"}[action],
                "revision": "a" * 64 if action == "remove" else "c" * 64}

    def test_lifecycle_review_refuses_wrong_identity_mode_and_attached_or_stale_account(self):
        for fields in ({**self.fields("create"), "username": "bob"},
                       {**self.fields("add-login"), "username": "carol"},
                       {**self.fields("add-login"), "revision": "d" * 64}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.manager.prepare(fields)
        self.inventory["lifecycle"]["removable"] = []
        with self.assertRaisesRegex(ValueError, "Detach"):
            self.manager.prepare(self.fields("remove"))
        self.inventory["lifecycle"] = {"available": False}
        with self.assertRaisesRegex(ValueError, "complete Samba"):
            self.manager.prepare(self.fields("create"))
        self.assertFalse(self.manager.tickets)
        self.rpc.assert_not_called()

    def test_creation_and_existing_identity_forms_require_https_and_stage_secrets_once(self):
        for action in ("create", "add-login"):
            fields = {"csrf": "csrf", **self.fields(action)}
            insecure = self.handler("/actions/samba/review", fields)
            insecure.do_POST()
            self.assertEqual(insecure._send.call_args.args[0], 403)
            secure = self.handler("/actions/samba/review", fields)
            secure.headers["X-Forwarded-Proto"] = "https"
            secure.do_POST()
            self.assertEqual(secure._send.call_args.args[0], 200)
            value = next(reversed(self.manager.tickets.values()))[0]
            ticket = next(reversed(self.manager.tickets))
            self.assertIn('type="password"', secure._send.call_args.args[1])
            self.manager.submit(ticket, value["username"], password="PRIVATE-password", password_repeat="PRIVATE-password")
            self.assertNotIn("PRIVATE", str(self.rpc.call_args.args[0]))
            with self.assertRaises(ValueError):
                self.manager.submit(ticket, value["username"], password="PRIVATE-password", password_repeat="PRIVATE-password")

    def test_samba_only_removal_needs_no_password_and_typo_retains_the_review(self):
        ticket, value = self.manager.prepare(self.fields("remove"))
        wrong = self.handler("/actions/samba/submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "wrong"})
        wrong.do_POST()
        self.assertEqual(wrong._send.call_args.args[0], 409)
        self.assertIn('name="ticket" value="' + ticket + '"', wrong._send.call_args.args[1])
        self.assertNotIn('type="password"', wrong._send.call_args.args[1])
        self.assertEqual(self.manager.reviewed(ticket), value)
        self.manager.submit(ticket, "alice")
        self.assertEqual(self.rpc.call_args.args[0]["parameters"]["action"], "remove")
        self.assertFalse(self.manager.tickets)

    def test_empty_inventory_keeps_creation_forms_and_removal_requires_complete_eligibility(self):
        html = panel.render_accounts(self.inventory, "csrf", controls=True, password_controls=True)
        self.assertIn("Review new Samba user", html)
        self.assertIn("bob · Unix UID 1001", html)
        self.assertIn("Review Samba login removal", html)
        self.assertNotIn("Review new Samba user", panel.render_accounts(self.inventory, "csrf", controls=True))
        self.assertNotIn("<form ", panel.render_accounts(self.inventory, "csrf", controls=False, password_controls=True))
        self.inventory["accounts"], self.inventory["lifecycle"]["removable"] = [], []
        html = panel.render_accounts(self.inventory, "csrf", controls=True, password_controls=True)
        self.assertIn("Review new Samba user", html)
        self.assertIn("No local Samba accounts", html)


class LifecycleSecretTests(samba_tests.PasswordStagingTests):
    """Run the full existing staging/cleanup/replay contract for both new modes."""

    def setUp(self):
        super().setUp()
        self.value["action"] = "create"


class ExistingIdentitySecretTests(LifecycleSecretTests):
    def setUp(self):
        super().setUp()
        self.value["action"] = "add-login"

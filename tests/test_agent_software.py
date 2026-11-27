"""Approved Debian additions survive controller disconnection and retain the reviewed scope."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.service_tools.privilege_approval import render_request
from common.service_tools.privilege_broker import Broker
from lib.agent_software import add_software_parser, run_software, synchronize
from lib.cache import get_cache_path_for_host, load_setup_command, save_setup_command
from lib.config import SetupConfig
from lib.privilege_execution import ExecutionResult
from lib.software_requests import apt_plan, save_selection, selection


class SoftwareApprovalTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, BASALTWATER_WORKSPACE=str(self.root)))
        stack.enter_context(patch("lib.concurrency._lock_root", return_value=str(self.root)))
        self.target = "vm.example"
        save_setup_command(SetupConfig(host=self.target, system_type="agent_vm", username="agent", tags=["work"]))
        self.policy = {"version": 1, "machine": "a" * 32, "origin": "https://vm.example:9444",
                       "requester_uid": 1000, "ttl_seconds": 86400, "services": {}, "reboot": "approve"}
        self.runner = Mock(return_value=ExecutionResult("succeeded", exit_code=0))
        self.broker = self.open()
        self.addCleanup(lambda: self.broker.db.close())
        info = {"profile": "agent_vm", "username": "agent", "digest": "f"*64, "package_digest": "c"*64,
                "changes": 1, "packages": [{"package": "jupyterlab", "version": "1", "sources": ["debian"]}],
                "requested": ["jupyterlab"]}
        self.review = stack.enter_context(patch("lib.software_requests.reviewed_install",
                                   return_value=(info, ["/usr/bin/true"], "Install reviewed packages")))
        self.params = dict(kind="flag", name="jupyterlab", scope="saved")

    def open(self):
        return Broker(str(self.root / "requests.db"), lambda: self.policy, self.runner)

    def request(self):
        return self.broker.request(1000, "software.install", self.params, "Load the notebook")

    def approve(self, item):
        self.broker.decide(item["id"], item["digest"], True, "operator")
        self.assertEqual(self.broker.execute_next(), item["plan"]["parameters"]["scope"] == "temporary")

    def rpc(self, msg):
        return self.broker.dispatch(msg, 0)

    def test_saved_flag_requires_controller_and_survives_interrupted_acknowledgement(self):
        request = self.request()
        self.assertIn("Inspect the complete transaction", render_request(request, "token"))
        self.assertFalse(self.rpc({"action": "software-queue"})["requests"])
        self.approve(request)
        self.assertEqual(self.broker.status(request["id"])["state"], "awaiting_controller")
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_not_called()
        def disconnected(msg):
            if msg["action"] == "software-saved":
                raise ConnectionError("lost controller connection")
            return self.rpc(msg)
        with self.assertRaises(ConnectionError):
            synchronize(self.target, None, rpc=disconnected)
        self.assertTrue(load_setup_command(self.target).install_jupyterlab)
        self.broker.db.close()
        self.broker = self.open()
        self.assertEqual(self.broker.status(request["id"])["state"], "saving")
        self.assertEqual(synchronize(self.target, None, rpc=self.rpc), [request["id"]])
        self.assertTrue(self.broker.execute_next())
        final = self.broker.status(request["id"])
        self.assertTrue(final["saved_for_rebuild"] and final["applied"])
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_called_once()
        self.assertEqual(load_setup_command(self.target).tags, ["work"])

    def test_cannot_ack_without_controller_claim_and_exact_identity(self):
        item = self.request()
        ack = {"action": "software-saved", "id": item["id"], "digest": item["digest"]}
        for uid in (0, 1000):
            with self.assertRaises((PermissionError, ValueError)):
                self.broker.dispatch(ack, uid)
        self.approve(item)
        with self.assertRaises(PermissionError):
            self.broker.dispatch({**ack, "action": "software-claim"}, 1000)
        with self.assertRaises(ValueError):
            self.rpc({**ack, "action": "software-claim", "digest": "0"*64})
        with self.assertRaises(ValueError):
            self.rpc(ack)
        self.broker.cancel(item["id"], 1000)
        self.assertEqual(synchronize(self.target, None, rpc=self.rpc), [])
        self.assertFalse(load_setup_command(self.target).install_jupyterlab)

    def test_requester_can_recover_an_id_without_listing_another_accounts_work(self):
        first, second = self.request(), self.request()
        with self.broker.db:
            self.broker.db.execute("UPDATE requests SET uid=1001 WHERE id=?", (second["id"],))
        rows = self.broker.dispatch({"action": "owned-requests"}, 1000)["requests"]
        self.assertEqual([row["id"] for row in rows], [first["id"]])
        self.assertEqual(rows[0]["target"], "jupyterlab")
        self.assertNotIn("plan", rows[0])
        self.assertNotIn("reason", rows[0])
        with self.assertRaises(PermissionError):
            self.broker.dispatch({"action": "owned-requests"}, 1001)

    def test_cancelled_queue_item_does_not_block_later_sync(self):
        cancelled = self.request()
        self.approve(cancelled)
        self.params = dict(kind="apt", name="hello", scope="saved")
        current = self.request()
        self.approve(current)

        def rpc(message):
            result = self.rpc(message)
            if message["action"] == "software-queue":
                self.broker.cancel(cancelled["id"], 1000)
            return result

        self.assertEqual(synchronize(self.target, None, rpc=rpc), [current["id"]])
        self.assertEqual(self.broker.status(cancelled["id"])["state"], "cancelled")
        self.assertFalse(load_setup_command(self.target).install_jupyterlab)
        self.assertEqual(load_setup_command(self.target).apt_packages, ["hello"])

    def test_repeated_save_ack_returns_receipt_without_replaying_installation(self):
        item = self.request()
        self.approve(item)
        synchronize(self.target, None, rpc=self.rpc)
        self.assertTrue(self.broker.execute_next())
        ack = self.rpc({"action": "software-saved", "id": item["id"], "digest": item["digest"]})
        self.assertTrue(ack["saved_for_rebuild"])
        self.assertEqual(ack["state"], "succeeded")
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_called_once()

    def test_package_drift_before_or_after_save_is_visible(self):
        item = self.request()
        self.approve(item)
        self.review.side_effect = ValueError("package unavailable")
        self.assertEqual(synchronize(self.target, None, rpc=self.rpc), [])
        self.assertEqual(self.broker.status(item["id"])["state"], "invalidated")
        self.review.side_effect = None
        item = self.request()
        self.approve(item)
        synchronize(self.target, None, rpc=self.rpc)
        self.review.side_effect = ValueError("package unavailable")
        self.broker.execute_next()
        outcome = self.broker.status(item["id"])
        self.assertEqual(outcome["state"], "invalidated")
        self.assertTrue(outcome["saved_for_rebuild"])
        self.assertFalse(outcome["applied"])
        self.runner.assert_not_called()

    def test_temporary_install_never_enters_rebuild_configuration(self):
        self.params["scope"] = "temporary"
        self.params["runtime_seconds"] = 7200
        item = self.request()
        self.assertEqual(item["plan"]["runtime_seconds"], 7200)
        self.approve(item)
        outcome = self.broker.status(item["id"])
        self.assertTrue(outcome["applied"])
        self.assertFalse(outcome["saved_for_rebuild"])
        self.assertEqual(synchronize(self.target, None, rpc=self.rpc), [])
        self.assertFalse(load_setup_command(self.target).install_jupyterlab)

    def test_controller_record_merges_latest_selection_and_checks_username(self):
        from lib.cache import record_software_selection
        params = dict(kind="apt", name="hello", scope="saved")
        for name in ("hello", "shellcheck", "hello"):
            record_software_selection(self.target, "agent_vm", "agent", {**params, "name": name})
        record = json.loads(Path(get_cache_path_for_host(self.target)).read_text())
        self.assertEqual(record["args"]["apt_packages"], ["hello", "shellcheck"])
        self.assertEqual(record["tags"], ["work"])
        with self.assertRaises(ValueError):
            record_software_selection(self.target, "agent_vm", "different-user", params)

    def test_completed_setup_preserves_approval_saved_while_it_was_running(self):
        from lib.cache import record_software_selection
        earlier = load_setup_command(self.target)
        with patch("lib.cache.time.time", return_value=150):
            record_software_selection(self.target, "agent_vm", "agent", self.params)
        save_setup_command(earlier, 100, 200, True)
        self.assertTrue(load_setup_command(self.target).install_jupyterlab)
        save_setup_command(earlier, 300, 400, True)
        self.assertFalse(load_setup_command(self.target).install_jupyterlab)

    def test_excludes_other_flag_types_and_package_selectors(self):
        for kind, name in [("flag", "sudo"), ("flag", "web-panel"), ("apt", "-x"), ("apt", "x*"),
                           ("apt", "../x.deb"), ("apt", "a=1.0"), ("apt", "pkg:amd64")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                selection({**self.params, "kind": kind, "name": name})
        with self.assertRaises(ValueError):
            save_selection({"system_type": "agent_cachyos"}, self.params)
        self.policy["software"] = "deny"
        with self.assertRaises(PermissionError):
            self.request()
        self.review.assert_not_called()

    def test_cpp_package_replays_with_a_literal_native_architecture_selector(self):
        from common import common_steps
        config = SetupConfig.from_dict(self.target, "agent_vm", {"username": "agent", "apt_packages": ["g++"]})
        self.assertEqual(selection(dict(kind="apt", name="g++", scope="saved"))[1], ("g++",))
        with (patch.object(common_steps, "is_package_installed", side_effect=[False, True]),
              patch.object(common_steps, "run", return_value=SimpleNamespace(stdout="amd64\n")) as run):
            common_steps.install_apt_packages(config)
        self.assertIn("'?and(?exact-name(g++),?architecture(amd64))'", run.call_args.args[0])


class SoftwareCLITests(unittest.TestCase):
    def call(self, *argv):
        parser = argparse.ArgumentParser()
        add_software_parser(parser.add_subparsers())
        with redirect_stdout(io.StringIO()) as output:
            code = run_software(parser.parse_args(["software", *argv]))
        return code, json.loads(output.getvalue())

    @patch("lib.agent_software.exchange", return_value={"id": "a"*32, "state": "pending"})
    def test_flags_default_to_rebuild_and_preserve_both_time_budgets(self, exchange):
        code, result = self.call("request", "--flag", "jupyterlab", "--reason", "Notebook",
                                 "--runtime", "2h", "--review-window", "3d")
        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "pending")
        message = exchange.call_args.args[0]
        self.assertEqual(message["parameters"], dict(kind="flag", name="jupyterlab", scope="saved", runtime_seconds=7200))
        self.assertEqual(message["review_seconds"], 259200)

    @patch("lib.agent_software.exchange")
    def test_invalid_selector_and_already_installed_need_no_request(self, exchange):
        code, invalid = self.call("request", "--apt", "./package.deb", "--reason", "Tool")
        self.assertEqual((code, invalid["effect"]), (2, "none"))
        with patch("lib.agent_software.subprocess.run", return_value=SimpleNamespace(
                stdout='{"changes":0,"requested":["hello"]}')):
            code, result = self.call("request", "--apt", "hello", "--temporary", "--reason", "Task")
        self.assertEqual((code, result["state"]), (0, "already_installed"))
        exchange.assert_not_called()


class AptPlanTests(unittest.TestCase):
    def setUp(self):
        self.audit = self.enterContext(patch("lib.software_requests.subprocess.run",
                                             return_value=SimpleNamespace(returncode=0, stdout=b"")))
        origin = SimpleNamespace(site="deb.example", archive="stable", trusted=True, origin="Debian")
        self.version = SimpleNamespace(version="1.0", origins=[origin], size=42, sha256="f"*64)
        self.pkg = Mock(name="package", fullname="hello:amd64", installed=None, candidate=self.version,
                        is_installed=False, marked_delete=False, marked_downgrade=False)
        self.pkg.name = "hello"
        self.pkg._pkg.selected_state = 0
        cache = Mock(broken_count=0, dpkg_journal_dirty=False, required_space=12)
        cache.__contains__ = Mock(return_value=True)
        cache.__getitem__ = Mock(return_value=self.pkg)
        cache.get_changes.return_value = [self.pkg]
        self.enterContext(patch.dict("sys.modules", apt=SimpleNamespace(Cache=lambda **kw: cache),
                                     apt_pkg=SimpleNamespace(SELSTATE_HOLD=2, CURSTATE_INSTALLED=6)))
        self.params = dict(kind="apt", name="hello", scope="saved")

    def test_digest_covers_archive_contents(self):
        _, first, rows = apt_plan(self.params)
        self.assertEqual(first["changes"], 1)
        self.assertEqual(rows[0]["sources"], ["deb.example stable"])
        self.version.sha256 = "b"*64
        self.assertNotEqual(apt_plan(self.params)[1]["digest"], first["digest"])

    def test_refuses_untrusted_origin_removal_and_held_changes(self):
        self.version.origins[0].trusted = False
        with self.assertRaises(ValueError):
            apt_plan(self.params)

        self.version.origins[0].trusted = True
        self.pkg.marked_delete = True
        with self.assertRaises(ValueError):
            apt_plan(self.params)
        self.pkg.marked_delete = False
        self.pkg._pkg.selected_state = 2
        with self.assertRaises(ValueError):
            apt_plan(self.params)

    def test_refuses_to_configure_packages_from_an_unfinished_transaction(self):
        self.audit.return_value.stdout = b"unconfigured package"
        with self.assertRaisesRegex(ValueError, "dpkg audit"):
            apt_plan(self.params)

"""Cluster joins with mocked Proxmox services, SSH, and temporary registry storage."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from basaltwater import create_basaltwater_parser
from lib.cache import _cache_path_for_host
from lib import proxmox_cluster_join as join
from lib.proxmox_cli import run_proxmox_command
from lib.proxmox_hosts import ProxmoxHost, ProxmoxHostFacts, add_proxmox_host, find_proxmox_host
from lib.remote_utils import CommandTimeoutError


TICKET = "PVE:root@pam:1234ABCD::c3ludGhldGljLXRlc3QtdGlja2V0"
FINGERPRINT = ":".join(["A1"] * 32)


def _state(name: str, ip: str, *, clustered: bool = False) -> dict:
    node = {"type": "node", "name": name, "local": 1, "online": 1, "ip": ip}
    cluster = {"type": "cluster", "name": "lab", "quorate": 1}
    return {
        "name": name, "version": "pve-manager/9.0.3/abcdef", "configured": clustered,
        "ca": "a" * 64 if clustered else "b" * 64, "status": [cluster, node] if clustered else [node],
        "guests": [], "tasks": [],
        "join_info": {"nodelist": [{"name": name, "pve_fp": FINGERPRINT, "ring0_addr": ip}]}
        if clustered else None,
    }


class TestClusterJoin(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.target = ProxmoxHost("new", "192.0.2.24", ssh_key="/keys/new", description="new node",
                                  tags=["lab"], default_storage="local-lvm", facts=ProxmoxHostFacts(node_name="new"))
        self.member = ProxmoxHost("existing", "192.0.2.10", ssh_key="/keys/cluster")
        add_proxmox_host(self.target, self.directory)
        add_proxmox_host(self.member, self.directory)
        self.execute = self.enterContext(patch("lib.proxmox_cluster_join._execute", side_effect=self._execute))
        self.prompt = self.enterContext(patch("builtins.input", return_value="yes"))
        self._reset_state()

    def _reset_state(self) -> None:
        self.execute.reset_mock()
        self.execute.side_effect = self._execute
        self.target_state = _state("new", self.target.address)
        self.member_state = _state("existing", self.member.address, clustered=True)
        self.native_joins = 0
        self.ticket_issues = 0
        self.request = None

    def _finish_join(self) -> None:
        self.target_state = _state("new", self.target.address, clustered=True)
        self.target_state["status"].append({**self.member_state["status"][1], "local": 0})
        self.member_state["status"].append({**self.target_state["status"][1], "local": 0})

    def _execute(self, host: ProxmoxHost, command: list[str], **kwargs) -> str:
        self.assertEqual(kwargs.get("workspace"), self.directory)
        self.assertNotIn(TICKET, str(command))
        if command[:2] == ["perl", "-e"]:
            self.ticket_issues += 1
            self.assertEqual(host, self.member)
            self.assertEqual(command[-1], self.member_state["ca"])
            self.assertTrue(kwargs["private"])
            return TICKET + "\n"
        if command[-1] == join._JOIN_SCRIPT:
            self.native_joins += 1
            self.request = json.loads(kwargs["input_data"])
            self.assertEqual(self.request["ticket"], TICKET)
            self.assertEqual(self.request["command"], ["pvecm", "add", self.member.address,
                                                      "--fingerprint", FINGERPRINT, "--link0", self.target.address])
            self.assertNotIn("--force", self.request["command"])
            self.assertEqual(kwargs["timeout"], 900)
            self._finish_join()
            return "native join succeeded\n"
        self.assertEqual(command, ["python3", "-c", join._INSPECT_SCRIPT])
        if kwargs.get("fresh"):
            self.assertEqual(host.ssh_key, self.member.ssh_key)
        return json.dumps(self.target_state if host.address == self.target.address else self.member_state)

    def _run(self, **kwargs) -> int:
        with redirect_stdout(io.StringIO()) as output:
            result = join.run_cluster_join(self.target, self.member, workspace=self.directory, **kwargs)
        self.assertNotIn(TICKET, output.getvalue())
        return result

    def test_native_join_uses_ticket_only_in_stdin_and_saves_verified_cluster_identity(self) -> None:
        self.assertEqual(self._run(), 0)
        self.assertEqual((self.ticket_issues, self.native_joins), (1, 1))
        self.prompt.assert_called_once()
        saved = find_proxmox_host("new", self.directory)
        self.assertEqual(saved.ssh_key, self.member.ssh_key)
        self.assertIsNone(saved.facts)
        self.assertEqual((saved.description, saved.tags, saved.default_storage),
                         (self.target.description, self.target.tags, self.target.default_storage))
        for path in Path(self.directory).rglob("*"):
            if path.is_file():
                self.assertNotIn(TICKET, path.read_text())

    def test_dry_run_inspects_both_nodes_without_ticket_confirmation_or_writes(self) -> None:
        previous = (Path(self.directory) / "proxmox_hosts.json").read_bytes()
        self.assertEqual(self._run(dry_run=True), 0)
        self.assertEqual((self.ticket_issues, self.native_joins), (0, 0))
        self.assertEqual(self.execute.call_count, 2)
        self.prompt.assert_not_called()
        self.assertEqual((Path(self.directory) / "proxmox_hosts.json").read_bytes(), previous)

    def test_declined_confirmation_and_eof_do_not_issue_credentials(self) -> None:
        for response in ("no", EOFError()):
            with self.subTest(response=response):
                self._reset_state()
                self.prompt.side_effect = response if isinstance(response, Exception) else None
                self.prompt.return_value = response
                self.assertEqual(self._run(), 1)
                self.assertEqual((self.ticket_issues, self.native_joins), (0, 0))
                self.assertEqual(find_proxmox_host("new", self.directory).ssh_key, self.target.ssh_key)

    def test_already_joined_node_with_guests_is_verified_without_rejoining(self) -> None:
        self._finish_join()
        self.target_state["guests"] = [{"type": "qemu", "vmid": 100}]
        self.assertEqual(self._run(), 0)
        self.assertEqual((self.ticket_issues, self.native_joins), (0, 0))
        self.prompt.assert_not_called()

    def test_lost_standalone_ssh_authorization_after_previous_join_uses_cluster_identity(self) -> None:
        self._finish_join()
        def execute(host, command, **kwargs):
            if host.address == self.target.address and host.ssh_key == self.target.ssh_key:
                raise join.ProxmoxClusterJoinError("Permission denied")
            return self._execute(host, command, **kwargs)
        self.execute.side_effect = execute
        self.assertEqual(self._run(), 0)
        self.assertEqual((self.ticket_issues, self.native_joins), (0, 0))
        self.assertEqual(find_proxmox_host("new", self.directory).ssh_key, self.member.ssh_key)

    def test_guest_cluster_task_identity_and_quorum_failures_stop_before_ticket(self) -> None:
        changes = (
            ("target", "guests", [{"type": "lxc", "vmid": 101}]),
            ("target", "configured", True),
            ("target", "tasks", [{"type": "clusterjoin"}]),
            ("target", "version", "pve-manager/8.4.1/abcdef"),
            ("target", "status", [{"type": "node", "name": "new", "local": 1, "online": 0, "ip": self.target.address}]),
            ("member", "status", [{"type": "cluster", "name": "lab", "quorate": 0}]),
            ("member", "join_info", {"nodelist": [{"name": "existing", "pve_fp": "unverified"}]}),
            ("member", "join_info", None),
        )
        for who, field, value in changes:
            with self.subTest(who=who, field=field):
                self._reset_state()
                getattr(self, who + "_state")[field] = value
                with self.assertRaises(join.ProxmoxClusterJoinError):
                    self._run(yes=True)
                self.assertEqual((self.ticket_issues, self.native_joins), (0, 0))

    def test_duplicate_cluster_name_or_ip_requires_review_of_previous_join(self) -> None:
        for name, ip in (("new", self.target.address), ("other", self.target.address)):
            with self.subTest(name=name):
                self._reset_state()
                self.member_state["status"].append({"type": "node", "name": name, "online": 0, "ip": ip})
                with self.assertRaises(join.ProxmoxClusterJoinError):
                    self._run(yes=True)
                self.assertEqual(self.ticket_issues, 0)

    def test_same_cluster_name_with_different_ca_is_not_a_successful_rerun(self) -> None:
        self._finish_join()
        self.target_state["ca"] = "c" * 64
        with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "automatic rejoining"):
            self._run(yes=True)
        self.assertEqual(self.ticket_issues, 0)

    def test_node_configured_but_not_yet_visible_in_quorum_is_not_rejoined(self) -> None:
        self.member_state["join_info"]["nodelist"].append({"name": "new", "ring0_addr": self.target.address})
        with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "already configures"):
            self._run(yes=True)
        self.assertEqual(self.ticket_issues, 0)

    def test_active_native_join_is_not_reported_complete_when_cluster_is_already_visible(self) -> None:
        self._finish_join()
        self.target_state["tasks"] = [{"type": "clusterjoin"}]
        with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "already active"):
            self._run(yes=True)

    def test_identity_change_during_confirmation_stops_before_ticket(self) -> None:
        def confirm(_prompt):
            self.member_state["ca"] = "c" * 64
            return "yes"
        self.prompt.side_effect = confirm
        with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "identity changed"):
            self._run()
        self.assertEqual(self.ticket_issues, 0)

    def test_multiple_cluster_links_require_explicit_matching_addresses(self) -> None:
        self.member_state["join_info"]["nodelist"][0]["ring1_addr"] = "198.51.100.10"
        with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "every cluster network link"):
            self._run(dry_run=True)
        self.assertEqual(self._run(dry_run=True, links=["0=192.0.2.24", "1=198.51.100.24"]), 0)
        self.assertEqual(self.ticket_issues, 0)

    def test_invalid_inputs_stop_before_ssh(self) -> None:
        for kwargs in ({"timeout": 0}, {"timeout": True}, {"timeout": 7201},
                       {"join_address": "pve;rm"}, {"links": ["0=127.0.0.1"]},
                       {"links": ["8=192.0.2.24"]}, {"links": ["0=192.0.2.24", "0=192.0.2.25"]}):
            with self.subTest(kwargs=kwargs):
                self.execute.reset_mock()
                with self.assertRaises(ValueError):
                    self._run(**kwargs)
                self.execute.assert_not_called()
        with self.assertRaises(ValueError):
            join.run_cluster_join(replace(self.target, user="admin"), self.member, yes=True)
        self.execute.assert_not_called()

    def test_failed_native_join_is_never_retried_or_saved(self) -> None:
        def execute(host, command, **kwargs):
            if command[-1] == join._JOIN_SCRIPT:
                raise join.ProxmoxClusterJoinError("lost SSH connection")
            return self._execute(host, command, **kwargs)
        self.execute.side_effect = execute
        with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "partly completed"):
            self._run(yes=True)
        self.assertEqual(len([call for call in self.execute.call_args_list if call.args[1][-1] == join._JOIN_SCRIPT]), 1)
        self.assertEqual(find_proxmox_host("new", self.directory).ssh_key, self.target.ssh_key)

    def test_failed_post_join_identity_verification_does_not_save_access(self) -> None:
        def execute(host, command, **kwargs):
            result = self._execute(host, command, **kwargs)
            if command[-1] == join._JOIN_SCRIPT:
                self.target_state["ca"] = "c" * 64
            return result
        self.execute.side_effect = execute
        with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "outcome could not be verified"):
            self._run(yes=True)
        self.assertEqual(find_proxmox_host("new", self.directory).ssh_key, self.target.ssh_key)

    def test_registry_updates_preserve_edits_made_while_the_native_join_runs(self) -> None:
        def execute(host, command, **kwargs):
            result = self._execute(host, command, **kwargs)
            if command[-1] == join._JOIN_SCRIPT:
                add_proxmox_host(replace(self.target, description="edited during join", tags=["production"]),
                                 self.directory, replace=True)
            return result
        self.execute.side_effect = execute
        self.assertEqual(self._run(yes=True), 0)
        saved = find_proxmox_host("new", self.directory)
        self.assertEqual((saved.description, saved.tags, saved.ssh_key),
                         ("edited during join", ["production"], self.member.ssh_key))

    def test_conflicting_ssh_registry_edit_is_preserved_after_join(self) -> None:
        def execute(host, command, **kwargs):
            result = self._execute(host, command, **kwargs)
            if command[-1] == join._JOIN_SCRIPT:
                add_proxmox_host(replace(self.target, ssh_key="/keys/changed"), self.directory, replace=True)
            return result
        self.execute.side_effect = execute
        with self.assertRaisesRegex(ValueError, "registration changed"):
            self._run(yes=True)
        self.assertEqual(find_proxmox_host("new", self.directory).ssh_key, "/keys/changed")

    def _save_setup_fixture(self, key: str | None) -> tuple[Path, dict]:
        path = Path(_cache_path_for_host(self.target.address, self.directory))
        data = {"host": self.target.address, "system_type": "server_proxmox", "name": "new",
                "args": {"username": "root", "ssh_key": key, "timezone": "UTC"},
                "last_success": True, "last_end_time": 1234567890}
        path.write_text(json.dumps(data))
        return path, data

    def test_saved_setup_uses_verified_cluster_key_and_retains_run_metadata(self) -> None:
        path, previous = self._save_setup_fixture(self.target.ssh_key)
        self.assertEqual(self._run(yes=True), 0)
        expected = {**previous, "args": {**previous["args"], "ssh_key": self.member.ssh_key}}
        self.assertEqual(json.loads(path.read_text()), expected)

    def test_saved_setup_connection_edit_during_join_is_preserved(self) -> None:
        path, _ = self._save_setup_fixture(None)
        def execute(host, command, **kwargs):
            result = self._execute(host, command, **kwargs)
            if command[-1] == join._JOIN_SCRIPT:
                self._save_setup_fixture("/keys/changed")
            return result
        self.execute.side_effect = execute
        with self.assertRaisesRegex(ValueError, "setup SSH identity changed"):
            self._run(yes=True)
        self.assertEqual(json.loads(path.read_text())["args"]["ssh_key"], "/keys/changed")

    def test_invalid_or_incomplete_ticket_is_not_passed_to_new_node(self) -> None:
        for ticket in ("PVE:!tfa!challenge", "not a ticket", TICKET + "\nextra"):
            with self.subTest(ticket=ticket):
                self._reset_state()
                self.execute.side_effect = lambda host, command, **kwargs: (
                    ticket if command[0] == "perl" else self._execute(host, command, **kwargs))
                with self.assertRaisesRegex(join.ProxmoxClusterJoinError, "valid root API ticket"):
                    self._run(yes=True)
                self.assertEqual(self.native_joins, 0)

    def test_cli_uses_registered_hosts_and_selected_workspace(self) -> None:
        parser, _, _ = create_basaltwater_parser()
        args = parser.parse_args(["proxmox", "--workspace", self.directory, "join-cluster", "new", "--via", "existing", "--dry-run"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run_proxmox_command(args), 0)
        self.assertEqual(self.execute.call_count, 2)
        args.host = "not-registered"
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_proxmox_command(args), 1)
        self.assertIn("No registered Proxmox host", output.getvalue())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["proxmox", "join-cluster", "new"])


class TestClusterJoinTransport(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("lib.ssh_utils.tempfile.gettempdir", return_value=self.directory))
        self.host = ProxmoxHost("pve", "192.0.2.10", ssh_key="/keys/root")
        self.command = self.enterContext(patch("lib.proxmox_cluster_join.run", return_value=subprocess.CompletedProcess([], 0, "OK", "")))

    def test_ticket_payload_never_enters_ssh_argv_and_trust_is_workspace_scoped(self) -> None:
        for workspace in (self.directory, self.directory + "/other"):
            join._execute(self.host, ["python3", "-c", join._JOIN_SCRIPT],
                          input_data=json.dumps({"ticket": TICKET}), workspace=workspace, timeout=900)
        first, second = self.command.call_args_list
        for call in (first, second):
            self.assertNotIn(TICKET, str(call.args))
            self.assertIn(TICKET, call.kwargs["input_data"])
            self.assertIn("StrictHostKeyChecking=yes", call.args[0])
            self.assertIn("-T", call.args[0])
            self.assertIn("ForwardAgent=no", call.args[0])
            self.assertIn("ClearAllForwardings=yes", call.args[0])
            self.assertEqual(call.kwargs["timeout"], 900)
        self.assertNotEqual([item for item in first.args[0] if item.startswith("ControlPath=")],
                            [item for item in second.args[0] if item.startswith("ControlPath=")])
        self.assertIn(f"UserKnownHostsFile={self.directory}/known_hosts", first.args[0])
        self.assertIn(f"UserKnownHostsFile={self.directory}/other/known_hosts", second.args[0])

    def test_fresh_verification_does_not_reuse_pre_join_authentication(self) -> None:
        join._execute(self.host, ["true"], fresh=True, workspace=self.directory)
        self.assertIn("ControlPath=none", self.command.call_args.args[0])

    def test_ticket_is_redacted_from_errors_and_private_output_is_suppressed(self) -> None:
        self.command.return_value = subprocess.CompletedProcess([], 1, TICKET, "error " + TICKET)
        for private in (False, True):
            with self.assertRaises(join.ProxmoxClusterJoinError) as raised:
                join._execute(self.host, ["perl", "-e", join._TICKET_SCRIPT], private=private, workspace=self.directory)
            self.assertNotIn(TICKET, str(raised.exception))
        self.command.side_effect = CommandTimeoutError("ssh", 900, TICKET)
        with self.assertRaises(join.ProxmoxClusterJoinError) as raised:
            join._execute(self.host, ["true"], private=True, workspace=self.directory)
        self.assertNotIn(TICKET, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)


class TestRemoteNativeJoinScript(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.ca = Path(self.directory) / "pve-root-ca.pem"
        self.ca.write_text("standalone public CA")
        self.state = _state("new", "192.0.2.24")
        self.request = {"node": "new", "major": 9,
                        "target_ca": hashlib.sha256(self.ca.read_bytes()).hexdigest(),
                        "cluster_ca": hashlib.sha256(b"cluster public CA").hexdigest(),
                        "command": ["pvecm", "add", "192.0.2.10", "--fingerprint", FINGERPRINT], "ticket": TICKET}
        self.script = join._JOIN_SCRIPT.replace("/etc/pve", self.directory).replace("/run/lock", self.directory)
        self.enterContext(patch("socket.gethostname", return_value="new"))
        self.native = self.enterContext(patch("subprocess.run", side_effect=self._native))
        self.lock = self.enterContext(patch("fcntl.flock"))
        self.exit_code = 0
        self.correct_ca = True

    def _native(self, command, **kwargs):
        if command[0] == "pvecm":
            self.lock.assert_called_once()
            self.assertEqual(command, self.request["command"])
            self.assertEqual(kwargs["input"], TICKET + "\n")
            self.assertNotIn(TICKET, str(command))
            self.assertEqual(kwargs["cwd"], "/")
            if not self.exit_code:
                self.state = _state("new", "192.0.2.24", clustered=True)
                self.ca.write_text("cluster public CA" if self.correct_ca else "wrong public CA")
                (Path(self.directory) / "corosync.conf").write_text("native cluster configuration")
            return subprocess.CompletedProcess(command, self.exit_code, "", "")
        if command[0] == "pveversion":
            output = self.state["version"]
        else:
            self.assertEqual(command[:2], ["pvesh", "get"])
            field = {"/cluster/status": "status", "/cluster/resources": "guests", "/nodes/new/tasks": "tasks",
                     "/cluster/config/join": "join_info"}[command[2]]
            output = json.dumps(self.state[field])
            if field == "tasks":
                self.assertIn("--typefilter", command)
                self.assertIn("clusterjoin", command)
        return subprocess.CompletedProcess(command, 0, output, "")

    def _run_script(self) -> str:
        with patch("sys.stdin", io.StringIO(json.dumps(self.request))), redirect_stdout(io.StringIO()) as output:
            exec(compile(self.script, "<remote_cluster_join>", "exec"), {})
        self.assertNotIn(TICKET, output.getvalue())
        return output.getvalue()

    def test_remote_script_passes_ticket_via_stdin_then_checks_native_cluster_ca(self) -> None:
        self.assertIn("cluster CA verified", self._run_script())
        self.assertEqual(len([call for call in self.native.call_args_list if call.args[0][0] == "pvecm"]), 1)

    def test_recently_completed_native_task_does_not_block_a_new_join(self) -> None:
        self.state["tasks"] = [{"type": "clusterjoin", "endtime": 1234567890, "status": "OK"}]
        self.assertIn("cluster CA verified", self._run_script())

    def test_task_without_verified_completion_stops_before_native_join(self) -> None:
        self.state["tasks"] = [{"type": "clusterjoin", "status": "RUNNING"}]
        with self.assertRaisesRegex(SystemExit, "state changed"):
            self._run_script()
        self.assertFalse(any(call.args[0][0] == "pvecm" for call in self.native.call_args_list))

    def test_busy_setup_lock_and_changed_guest_state_stop_before_native_join(self) -> None:
        self.lock.side_effect = BlockingIOError()
        with self.assertRaisesRegex(SystemExit, "node lock"):
            self._run_script()
        self.native.assert_not_called()
        self.lock.side_effect = None
        self.state["guests"] = [{"type": "qemu", "vmid": 100}]
        with self.assertRaisesRegex(SystemExit, "state changed"):
            self._run_script()
        self.assertFalse(any(call.args[0][0] == "pvecm" for call in self.native.call_args_list))

    def test_native_failure_and_wrong_post_join_ca_cannot_report_success(self) -> None:
        self.exit_code = 1
        with self.assertRaises(SystemExit) as raised:
            self._run_script()
        self.assertEqual(raised.exception.code, 1)
        self.exit_code = 0
        self.correct_ca = False
        self.lock.reset_mock()
        with self.assertRaisesRegex(SystemExit, "identity or quorum"):
            self._run_script()

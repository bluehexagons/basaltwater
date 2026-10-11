"""Mocked native repository API coverage for fresh and subscribed Proxmox nodes."""

from __future__ import annotations

import copy
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from lib.proxmox_repositories import configure_unsubscribed_proxmox_repositories


def _repository(product: str, *, enterprise: bool = True, enabled: bool = True) -> dict:
    return {
        "Types": ["deb"],
        "URIs": [f"https://enterprise.proxmox.com/debian/{product}" if enterprise
                 else f"http://download.proxmox.com/debian/{product}"],
        "Suites": ["trixie"],
        "Components": ["pve-enterprise" if enterprise and product == "pve"
                       else "enterprise" if enterprise else "pve-no-subscription"
                       if product == "pve" else "no-subscription"],
        "Options": [{"Key": "Signed-By", "Values": ["/usr/share/keyrings/proxmox-archive-keyring.gpg"]}],
        "Enabled": enabled,
    }


class TestProxmoxRepositorySelection(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch("lib.proxmox_repositories.is_dry_run", return_value=False))
        self.hostname = self.enterContext(patch("lib.proxmox_repositories.socket.gethostname", return_value="pve.example"))
        self.command = self.enterContext(patch("lib.proxmox_repositories.run", side_effect=self._api))
        self._reset_state()

    def _reset_state(self) -> None:
        self.command.reset_mock()
        self.command.side_effect = self._api
        self.subscription = {"status": "notfound", "message": "There is no subscription key"}
        self.state = {
            "errors": [], "digest": "0" * 64,
            "files": [
                {"path": "/etc/apt/sources.list.d/pve-enterprise.sources", "file-type": "sources",
                 "repositories": [_repository("pve")]},
                {"path": "/etc/apt/sources.list.d/ceph.sources", "file-type": "sources",
                 "repositories": [_repository("ceph-squid")]},
                {"path": "/etc/apt/sources.list.d/unrelated.sources", "file-type": "sources",
                 "repositories": [
                     {"Types": ["deb"], "URIs": ["https://deb.debian.org/debian"],
                      "Suites": ["trixie"], "Components": ["main"], "Enabled": True},
                     _repository("ceph-tentacle", enabled=False)]},
            ],
            "standard-repos": [{"handle": "no-subscription"}, {"handle": "ceph-squid-no-subscription"}],
        }
        self.unrelated = copy.deepcopy(self.state["files"][-1])
        self.changes = 0

    def _api(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.assertEqual(args[0], "pvesh")
        self.assertEqual(kwargs, {"check": False, "capture_output": True, "timeout": 60})
        self.assertTrue(args[2].startswith("/nodes/pve/"))
        if args[1] == "get":
            self.assertEqual(args[-2:], ["--output-format", "json"])
            value = self.subscription if args[2].endswith("/subscription") else self.state
            return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
        self.assertEqual(args[-2:], ["--digest", self.state["digest"]])
        if args[1] == "set":
            handle = args[args.index("--handle") + 1]
            product = "pve" if handle == "no-subscription" else handle.removesuffix("-no-subscription")
            existing = next((repo for source in self.state["files"] for repo in source["repositories"]
                             if repo["URIs"] == [f"http://download.proxmox.com/debian/{product}"]), None)
            if existing is not None:
                existing["Enabled"] = True
            else:
                self.state["files"].append({"path": f"/etc/apt/sources.list.d/{product}-public.sources",
                                           "file-type": "sources", "repositories": [_repository(product, enterprise=False)]})
            next(entry for entry in self.state["standard-repos"] if entry["handle"] == handle)["status"] = True
        elif args[1] == "create":
            self.assertEqual(args[args.index("--enabled") + 1], "0")
            path = args[args.index("--path") + 1]
            index = int(args[args.index("--index") + 1])
            next(source for source in self.state["files"] if source["path"] == path)["repositories"][index]["Enabled"] = False
        else:
            self.fail(f"Unexpected mutation: {args}")
        self.changes += 1
        self.state["digest"] = f"{self.changes:064x}"
        return subprocess.CompletedProcess(args, 0, "", "")

    def _mutations(self) -> list[list[str]]:
        return [call.args[0] for call in self.command.call_args_list if call.args[0][1] != "get"]

    def test_fresh_installer_sources_use_matching_public_channels_with_fresh_digests(self) -> None:
        for file_type in ("list", "sources"):
            with self.subTest(file_type=file_type):
                self._reset_state()
                for source in self.state["files"][:2]:
                    source["file-type"] = file_type
                    source["path"] = source["path"].removesuffix(".sources") + f".{file_type}"
                    # Native Proxmox JSON can serialize booleans as integers.
                    source["repositories"][0]["Enabled"] = 1
                self.assertTrue(configure_unsubscribed_proxmox_repositories())
                mutations = self._mutations()
                self.assertEqual([args[1] for args in mutations], ["set", "set", "create", "create"])
                self.assertEqual({args[4] for args in mutations[:2]}, {"no-subscription", "ceph-squid-no-subscription"})
                self.assertEqual([args[-1] for args in mutations], [f"{index:064x}" for index in range(4)])
                self.assertTrue(all(not source["repositories"][0]["Enabled"] for source in self.state["files"][:2]))
                self.assertEqual(self.state["files"][2], self.unrelated)
                public = [source["repositories"][0] for source in self.state["files"][3:]]
                self.assertEqual({repo["URIs"][0] for repo in public},
                                 {"http://download.proxmox.com/debian/pve", "http://download.proxmox.com/debian/ceph-squid"})
                self.assertTrue(all(repo["Options"][0]["Key"] == "Signed-By" for repo in public))
                self.command.reset_mock()
                self.assertFalse(configure_unsubscribed_proxmox_repositories())
                self.assertEqual(self._mutations(), [])

    def test_subscribed_expired_invalid_unknown_and_inconsistent_key_states_preserve_channels(self) -> None:
        subscriptions = [{"status": status} for status in
                         ("active", "expired", "invalid", "suspended", "new", "unknown", None)]
        subscriptions += [{"status": "active", "key": "secret-subscription-key"},
                          {"status": "notfound", "key": "secret-subscription-key"}]
        for subscription in subscriptions:
            with self.subTest(subscription=subscription):
                self.subscription = subscription
                self.command.reset_mock()
                with redirect_stdout(io.StringIO()) as output:
                    self.assertFalse(configure_unsubscribed_proxmox_repositories())
                self.assertEqual(self._mutations(), [])
                self.assertEqual(self.command.call_count, 1)
                self.assertNotIn("secret-subscription-key", output.getvalue())

    def test_existing_public_channels_are_reused_and_disabled_ones_reenabled(self) -> None:
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self._reset_state()
                self.state["files"].append({"path": "/etc/apt/sources.list.d/proxmox.sources",
                                           "repositories": [_repository("pve", enterprise=False, enabled=enabled)]})
                self.state["standard-repos"][0]["status"] = enabled
                configure_unsubscribed_proxmox_repositories()
                public_pve = [repo for source in self.state["files"] for repo in source["repositories"]
                              if repo["URIs"] == ["http://download.proxmox.com/debian/pve"]]
                self.assertEqual(len(public_pve), 1)
                self.assertTrue(public_pve[0]["Enabled"])
                self.assertEqual(len(self._mutations()), 3 if enabled else 4)

    def test_no_ceph_repository_is_added_when_only_pve_enterprise_is_enabled(self) -> None:
        self.state["files"][1]["repositories"][0]["Enabled"] = False
        configure_unsubscribed_proxmox_repositories()
        self.assertEqual(len(self._mutations()), 2)
        self.assertFalse(any("ceph-squid-no-subscription" in args for args in self._mutations()))

    def test_multiple_enterprise_entries_in_one_file_use_their_native_indices(self) -> None:
        self.state["files"][0]["repositories"].insert(0, copy.deepcopy(self.unrelated["repositories"][0]))
        self.state["files"][0]["repositories"].append(_repository("ceph-squid"))
        self.state["files"].pop(1)
        configure_unsubscribed_proxmox_repositories()
        changes = [args for args in self._mutations() if args[1] == "create"]
        self.assertEqual([args[args.index("--index") + 1] for args in changes], ["1", "2"])
        self.assertTrue(self.state["files"][0]["repositories"][0]["Enabled"])

    def test_custom_sources_unknown_ceph_and_unsafe_paths_stop_before_any_writes(self) -> None:
        cases = (
            ("URIs", ["https://enterprise.proxmox.com/debian/pve", "https://mirror.example/debian/pve"]),
            ("Suites", ["bookworm"]), ("Components", ["pve-enterprise", "other"]),
            ("Types", ["deb", "deb-src"]),
            ("URIs", ["https://enterprise.proxmox.com/debian/ceph-unknown"]),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self._reset_state()
                self.state["files"][0]["repositories"][0][field] = value
                with self.assertRaises(RuntimeError):
                    configure_unsubscribed_proxmox_repositories()
                self.assertEqual(self._mutations(), [])
        self._reset_state()
        self.state["files"][1]["repositories"][0] = _repository("ceph-unknown")
        with self.assertRaisesRegex(RuntimeError, "does not offer repository ceph-unknown-no-subscription"):
            configure_unsubscribed_proxmox_repositories()
        self.assertEqual(self._mutations(), [])
        for path in ("/tmp/pve.sources", "/etc/apt/sources.list.d/../pve.sources", "--bad", "/etc/apt/sources.list.d/pve.sources\n"):
            with self.subTest(path=path):
                self._reset_state()
                self.state["files"][0]["path"] = path
                with self.assertRaises((RuntimeError, ValueError)):
                    configure_unsubscribed_proxmox_repositories()
                self.assertEqual(self._mutations(), [])

    def test_missing_standard_handle_and_parse_errors_stop_before_changes(self) -> None:
        for field, value in (("standard-repos", [{"handle": "no-subscription"}]),
                             ("errors", [{"path": "/etc/apt/sources.list", "error": "invalid syntax"}]),
                             ("digest", "missing"), ("files", None)):
            with self.subTest(field=field):
                self._reset_state()
                self.state[field] = value
                with self.assertRaises(RuntimeError):
                    configure_unsubscribed_proxmox_repositories()
                self.assertEqual(self._mutations(), [])

    def test_failed_native_writes_at_each_stage_can_be_retried_without_duplicates(self) -> None:
        for successful_writes in range(4):
            with self.subTest(successful_writes=successful_writes):
                self._reset_state()
                self.command.side_effect = lambda args, **kwargs: (
                    subprocess.CompletedProcess(args, 1, "", "digest mismatch")
                    if args[1] != "get" and self.changes == successful_writes else self._api(args, **kwargs))
                with self.assertRaisesRegex(RuntimeError, "selection failed"):
                    configure_unsubscribed_proxmox_repositories()
                if successful_writes < 2:
                    self.assertTrue(all(source["repositories"][0]["Enabled"] for source in self.state["files"][:2]))
                self.command.side_effect = self._api
                self.assertTrue(configure_unsubscribed_proxmox_repositories())
                self.assertEqual(len(self.state["files"]), 5)
                self.assertEqual(self.state["files"][2], self.unrelated)

    def test_native_write_that_does_not_enable_replacement_never_disables_enterprise(self) -> None:
        api = self.command.side_effect
        self.command.side_effect = lambda args, **kwargs: (
            subprocess.CompletedProcess(args, 0, "", "") if args[1] == "set" else api(args, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "did not become enabled"):
            configure_unsubscribed_proxmox_repositories()
        self.assertFalse(any(args[1] == "create" for args in self._mutations()))

    def test_native_write_that_does_not_disable_enterprise_stops_setup(self) -> None:
        self.command.side_effect = lambda args, **kwargs: (
            subprocess.CompletedProcess(args, 0, "", "") if args[1] == "create" else self._api(args, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "did not become disabled"):
            configure_unsubscribed_proxmox_repositories()
        self.assertEqual(len([args for args in self._mutations() if args[1] == "create"]), 1)

    def test_invalid_or_failed_subscription_responses_never_change_repositories(self) -> None:
        for result in (subprocess.CompletedProcess([], 0, "[]", ""),
                       subprocess.CompletedProcess([], 0, "not json", ""),
                       subprocess.CompletedProcess([], 1, "secret-subscription-key", "failed")):
            with self.subTest(result=result.returncode, output=result.stdout):
                self.command.reset_mock()
                self.command.side_effect = None
                self.command.return_value = result
                with self.assertRaises(RuntimeError) as raised:
                    configure_unsubscribed_proxmox_repositories()
                self.assertNotIn("secret-subscription-key", str(raised.exception))
                self.assertEqual(self.command.call_count, 1)
                self.assertEqual(self._mutations(), [])

    def test_dry_run_and_invalid_hostname_do_not_call_proxmox_api(self) -> None:
        with patch("lib.proxmox_repositories.is_dry_run", return_value=True):
            self.assertFalse(configure_unsubscribed_proxmox_repositories())
        self.hostname.return_value = "pve/other"
        with self.assertRaisesRegex(ValueError, "node name"):
            configure_unsubscribed_proxmox_repositories()
        self.command.assert_not_called()

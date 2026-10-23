"""Regression tests for conservative Proxmox snippets defaults."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from common.proxmox_storage_steps import configure_proxmox_snippets_storage
from lib.config import SetupConfig


def _result(value: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, json.dumps(value), "")


def _local(**changes: object) -> dict[str, object]:
    return {
        "storage": "local", "type": "dir", "content": "backup,import,iso,vztmpl",
        "digest": "a" * 40, **changes,
    }


def _status(**changes: object) -> dict[str, object]:
    return {**_local(), "enabled": 1, "active": 1, **changes}


@patch("common.proxmox_storage_steps.is_dry_run", return_value=False)
@patch("common.proxmox_storage_steps.run")
class TestSnippetsStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SetupConfig(system_type="server_proxmox", host="pve1", username="agent")

    def _results(self, configured, available):
        return [subprocess.CompletedProcess([], 0, "pve1\n", ""),
                _result(configured), _result(available)]

    def test_adds_only_snippets_retains_content_and_is_idempotent(self, run, _dry):
        content = "backup,import,iso,snippets,vztmpl"
        updated = _local(content=content)
        active = _status(content=content)
        run.side_effect = self._results([_local()], [_status()]) + [
            _result(None), _result([updated]), _result([active]),
        ] + self._results([updated], [active])

        configure_proxmox_snippets_storage(self.config)
        configure_proxmox_snippets_storage(self.config)

        writes = [call.args[0] for call in run.call_args_list if call.args[0][0] == "pvesm"]
        self.assertEqual(writes, [[
            "pvesm", "set", "local", "--content", content, "--digest", "a" * 40,
        ]])

    def test_preserves_existing_active_offline_and_disabled_snippets_pools(self, run, _dry):
        for state in ("active", "offline", "disabled", "omitted"):
            with self.subTest(state=state), patch("builtins.print") as output:
                custom = {"storage": "custom", "content": "snippets", "nodes": "pve1,pve2"}
                available = [_status()]
                if state != "omitted":
                    available.append({**custom, "enabled": int(state != "disabled"),
                                      "active": int(state == "active")})
                run.reset_mock()
                run.side_effect = self._results([_local(), custom], available)
                configure_proxmox_snippets_storage(self.config)
                self.assertEqual(run.call_count, 3)
                message = output.call_args.args[0]
                self.assertIn("custom", message)
                self.assertEqual("restore it" in message, state != "active")

    def test_other_nodes_snippets_pool_does_not_suppress_local_default(self, run, _dry):
        custom = {"storage": "custom", "content": "snippets", "nodes": "pve2"}
        content = "backup,import,iso,snippets,vztmpl"
        run.side_effect = self._results([_local(), custom], [_status()]) + [
            _result(None), _result([_local(content=content), custom]),
            _result([_status(content=content)]),
        ]
        configure_proxmox_snippets_storage(self.config)
        self.assertEqual(run.call_args_list[3].args[0][0:3], ["pvesm", "set", "local"])

    def test_implicit_content_is_retained_from_node_status(self, run, _dry):
        config = _local()
        del config["content"]
        content = "backup,import,iso,snippets,vztmpl"
        run.side_effect = self._results([config], [_status()]) + [
            _result(None), _result([_local(content=content)]), _result([_status(content=content)]),
        ]
        configure_proxmox_snippets_storage(self.config)
        self.assertEqual(run.call_args_list[3].args[0][4], content)

    def test_skips_missing_disabled_inactive_and_non_directory_local(self, run, _dry):
        for configured, available in (
            ([], []), ([_local()], []),
            ([_local()], [_status(active=0)]),
            ([_local()], [_status(enabled=0)]),
            ([_local(type="nfs")], [_status(type="nfs")]),
        ):
            with self.subTest(available=available), patch("builtins.print") as output:
                run.reset_mock()
                run.side_effect = self._results(configured, available)
                configure_proxmox_snippets_storage(self.config)
                self.assertEqual(run.call_count, 3)
                self.assertIn("enable snippets content", output.call_args.args[0])

    def test_malformed_inventory_and_digest_never_write(self, run, _dry):
        for configured, available in (
            ({}, [_status()]), ([None], [_status()]),
            ([_local(), _local()], [_status()]),
            ([_local(content="iso\nsnippets")], [_status()]),
            ([_local(content="iso,,snippets")], [_status()]),
            ([_local(storage="bad/name")], [_status()]),
            ([_local(nodes=["pve1"])], [_status()]),
            ([_local(nodes="pve1,")], [_status()]),
            ([_local(digest=None)], [_status()]),
            ([_local(digest="x" * 40)], [_status()]),
            ([_local()], [_status(active="1")]),
            ([_local()], [_status(enabled=2)]),
        ):
            with self.subTest(configured=configured, available=available):
                run.reset_mock()
                run.side_effect = self._results(configured, available)
                with self.assertRaises(RuntimeError):
                    configure_proxmox_snippets_storage(self.config)
                self.assertFalse(any(call.args[0][0] == "pvesm" for call in run.call_args_list))

    def test_failed_read_or_concurrent_change_propagates_without_retry(self, run, _dry):
        failure = subprocess.CalledProcessError(1, "pvesm", stderr="digest mismatch")
        for results in (
            self._results([], [])[:1] + [failure],
            self._results([_local()], [_status()]) + [failure],
        ):
            with self.subTest(results=results):
                run.reset_mock()
                run.side_effect = results
                with self.assertRaises(subprocess.CalledProcessError):
                    configure_proxmox_snippets_storage(self.config)
                self.assertEqual(run.call_count, len(results))

    def test_failed_verification_is_reported(self, run, _dry):
        content = "backup,import,iso,snippets,vztmpl"
        for config, status in (
            (_local(), _status(content=content)),
            (_local(content="snippets"), _status(content=content)),
            (_local(content=content), _status(content=content, active=0)),
            (_local(content=content), _status()),
        ):
            with self.subTest(config=config, status=status):
                run.side_effect = self._results([_local()], [_status()]) + [
                    _result(None), _result([config]), _result([status]),
                ]
                with self.assertRaisesRegex(RuntimeError, "verification failed"):
                    configure_proxmox_snippets_storage(self.config)

    def test_dry_run_does_not_probe_or_write(self, run, dry):
        dry.return_value = True
        configure_proxmox_snippets_storage(self.config)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

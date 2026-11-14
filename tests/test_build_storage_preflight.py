"""Physical admission is independent from logical storage ceilings."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib.build_selection import SSHEndpoint
from lib.build_storage_preflight import check_observations, preflight_delivery_storage, probe, RESERVE


class StoragePreflightTests(unittest.TestCase):
    def row(self, path, needed, entries, device=1, free=10**10):
        return dict(path=path, required_bytes=needed, required_inodes=entries,
                    device=device, free_bytes=free, free_inodes=100000,
                    inodes_supported=True, mount="/" if device == 1 else "/tmp")

    def test_same_filesystem_sums_overlapping_allocations_once_with_reserve(self):
        requests = [["/var/lib/store", 100, 10], ["/var/www", 200, 20]]
        result = check_observations([self.row(*item) for item in requests], requests)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["required_bytes"], 300 + RESERVE)
        self.assertEqual(result[0]["required_inodes"], 10030)

    def test_small_tmpfs_has_independent_budget(self):
        requests = [["/var/www", 100, 10], ["/tmp", 128 * 1024**2, 1000]]
        rows = [self.row(*requests[0]), self.row(*requests[1], device=2, free=233 * 1024**2)]
        result = check_observations(rows, requests)
        self.assertEqual(result[1]["shortfall_bytes"], 0)
        rows[1]["free_bytes"] = 100 * 1024**2
        self.assertGreater(check_observations(rows, requests)[1]["shortfall_bytes"], 0)

    def test_inode_shortfall_and_unsupported_inode_reporting(self):
        requests = [["/var/www", 0, 20]]
        row = self.row(*requests[0])
        row["free_inodes"] = 10
        self.assertEqual(check_observations([row], requests)[0]["shortfall_inodes"], 10010)
        row["inodes_supported"] = False
        self.assertEqual(check_observations([row], requests)[0]["shortfall_inodes"], 0)

    def test_malformed_or_incomplete_probe_refuses(self):
        requests = [["/var/www", 0, 20]]
        for rows in ([], [dict(self.row(*requests[0]), device=True)], [dict(self.row(*requests[0]), path="/other")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                check_observations(rows, requests)

    def test_all_applications_and_separate_role_roots_enter_peak_budget(self):
        limits = dict(artifact_bytes=1024, artifact_entries=3, log_bytes=200, scratch_bytes=5000)
        plan = SimpleNamespace(request=dict(limits=limits), source_path="/private/source", deployment=dict(application="web"))
        endpoint = SimpleNamespace(host="target.example")
        selected = SimpleNamespace(endpoint=SimpleNamespace(host="builder.example"))
        config = SimpleNamespace(build_target_enrollment=dict(policy=dict(applications=dict(
            web=dict(deployment_root="/srv/apps/web")))))
        with patch("lib.build_storage_preflight.probe", return_value=[]) as observe, \
             patch("lib.build_storage_preflight.os.stat", return_value=SimpleNamespace(st_size=700)):
            preflight_delivery_storage(config, selected, endpoint, [plan, plan])
        target, builder = observe.call_args_list
        self.assertEqual(target.kwargs["role"], "target")
        self.assertEqual(builder.kwargs["role"], "host")
        requests = {row[0]: row[1:] for row in builder.args[1]}
        self.assertEqual(requests["/var/lib/basaltwater/build-host/inputs"][0], 1400)
        self.assertEqual(requests["/var/lib/basaltwater/build-host/scratch"][0], 5000)
        self.assertEqual(requests["/var/lib/basaltwater/build-host/logs"][0], 400)
        self.assertIn("/tmp", requests)
        target_requests = target.args[1]
        copies = [row for row in target_requests if row[0] == "/srv/apps"]
        self.assertEqual(sum(row[1] for row in copies), 2 * (2 * 1024 + 11 * 16384))

    def test_probe_is_pinned_read_only_ssh_and_refuses_shortfall(self):
        endpoint = SSHEndpoint("target.example", "unused", "/private/key", "/private/pins", "deploy")
        requests = [["/var/www", 1024, 20]]
        with patch("lib.build_storage_preflight.subprocess.run", return_value=SimpleNamespace(
                stdout=json.dumps([self.row(*requests[0], free=1)]).encode())) as run:
            with self.assertRaisesRegex(ValueError, "target delivery"):
                probe(endpoint, requests, phase="target delivery")
            command = run.call_args.args[0]
            self.assertIn("IdentityAgent=none", command)
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn("root@target.example", command)
            self.assertIn("-B", command[-1])

"""Tests for generic network inventory persistence and validation."""

from __future__ import annotations

import multiprocessing
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.network_inventory import (
    NetworkHost,
    NetworkProfile,
    NetworkSubnet,
    add_network_host,
    load_network_profiles,
    save_network_profile,
    upsert_network_profile,
    validate_network_subnet,
)


def _save_network_profile_worker(
    workspace: str,
    profile_name: str,
    start_event,
) -> None:
    """Create one profile after all worker processes are ready."""

    start_event.wait()
    save_network_profile(NetworkProfile(name=profile_name), workspace)


class TestNetworkInventory(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_saves_and_loads_profile(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            management_sources=["192.168.1.0/24"],
            control_plane=["10.0.0.10"],
            guest_networks=["10.20.0.0/24"],
            subnets=[
                NetworkSubnet(
                    name="servers",
                    cidr="10.20.0.0/24",
                    zone="guests",
                    vlan_id=20,
                )
            ],
        )

        upsert_network_profile(profile, self.workspace)

        loaded = load_network_profiles(self.workspace)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "homelab")
        self.assertEqual(loaded[0].subnets[0].vlan_id, 20)

    def test_rejects_invalid_cidr(self) -> None:
        profile = NetworkProfile(name="bad", management_sources=["not-a-cidr"])

        with self.assertRaises(ValueError):
            upsert_network_profile(profile, self.workspace)

    def test_rejects_invalid_vlan(self) -> None:
        profile = NetworkProfile(
            name="bad",
            subnets=[
                NetworkSubnet(
                    name="servers",
                    cidr="10.20.0.0/24",
                    zone="guests",
                    vlan_id=4095,
                )
            ],
        )

        with self.assertRaises(ValueError):
            upsert_network_profile(profile, self.workspace)

    def test_add_host_to_profile(self) -> None:
        upsert_network_profile(NetworkProfile(name="homelab"), self.workspace)

        profile = add_network_host(
            "homelab",
            NetworkHost(
                name="pve1",
                address="10.0.0.10",
                provider="proxmox",
                roles=["control-plane"],
            ),
            self.workspace,
        )

        self.assertEqual(profile.hosts[0].name, "pve1")
        self.assertEqual(profile.hosts[0].provider, "proxmox")

    def test_duplicate_host_requires_replace(self) -> None:
        upsert_network_profile(NetworkProfile(name="homelab"), self.workspace)
        host = NetworkHost(name="pve1", address="10.0.0.10")
        add_network_host("homelab", host, self.workspace)

        with self.assertRaises(ValueError):
            add_network_host("homelab", host, self.workspace)

    def test_save_network_profile_replaces_existing_profile(self) -> None:
        upsert_network_profile(NetworkProfile(name="homelab"), self.workspace)

        save_network_profile(
            NetworkProfile(
                name="homelab",
                management_sources=["192.168.1.0/24"],
            ),
            self.workspace,
        )

        loaded = load_network_profiles(self.workspace)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].management_sources, ["192.168.1.0/24"])

    def test_save_network_profile_skips_unchanged_write(self) -> None:
        profile = NetworkProfile(name="homelab", management_sources=["192.168.1.0/24"])
        save_network_profile(profile, self.workspace)

        with patch(
            "lib.network_inventory._save_network_profiles_unlocked",
        ) as mock_save_profiles:
            loaded = save_network_profile(profile, self.workspace)

        self.assertEqual(loaded.management_sources, ["192.168.1.0/24"])
        mock_save_profiles.assert_not_called()

    def test_concurrent_profile_updates_do_not_lose_records(self) -> None:
        context = multiprocessing.get_context("fork")
        start_event = context.Event()
        processes = [
            context.Process(
                target=_save_network_profile_worker,
                args=(self.workspace, f"profile-{index}", start_event),
            )
            for index in range(6)
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)

        self.assertEqual(
            {profile.name for profile in load_network_profiles(self.workspace)},
            {f"profile-{index}" for index in range(6)},
        )

    def test_subnet_from_dict_defers_vlan_validation(self) -> None:
        subnet = NetworkSubnet.from_dict(
            {
                "name": "servers",
                "cidr": "10.20.0.0/24",
                "vlan_id": "not-a-vlan",
            }
        )

        with self.assertRaises(ValueError):
            validate_network_subnet(subnet)


if __name__ == "__main__":
    unittest.main()

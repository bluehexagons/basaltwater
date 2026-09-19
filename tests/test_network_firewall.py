"""Tests for read-only network firewall planning."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.network_firewall import (
    build_proxmox_control_plane_lockdown_plan,
    format_proxmox_firewall_plan,
    format_rendered_proxmox_plan,
    render_proxmox_firewall_plan,
)
from lib.network_inventory import NetworkProfile


class TestProxmoxFirewallPlan(unittest.TestCase):
    def test_builds_control_plane_lockdown_plan(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            management_sources=["192.168.1.0/24"],
            control_plane=["10.0.0.10", "10.0.0.11"],
            guest_networks=["10.20.0.0/24"],
        )

        plan = build_proxmox_control_plane_lockdown_plan(profile)

        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(plan.errors, [])
        self.assertEqual(plan.address_sets[0].name, "basaltwater-management")
        self.assertEqual(plan.address_sets[0].entries, ["192.168.1.0/24"])
        self.assertEqual(plan.rules[-1].action, "DROP")
        self.assertEqual(plan.rules[-1].source, "+basaltwater-guests")
        self.assertEqual(plan.rules[-1].destination, "+basaltwater-control-plane")

    def test_missing_management_source_is_error(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            control_plane=["10.0.0.10"],
            guest_networks=["10.20.0.0/24"],
        )

        plan = build_proxmox_control_plane_lockdown_plan(profile)

        self.assertFalse(plan.safe_to_apply)
        self.assertIn("management source", plan.errors[0])
        self.assertEqual(plan.rules, [])

    def test_missing_guest_networks_is_warning(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            management_sources=["192.168.1.0/24"],
            control_plane=["10.0.0.10"],
        )

        plan = build_proxmox_control_plane_lockdown_plan(profile)

        self.assertTrue(plan.safe_to_apply)
        self.assertIn("No guest networks", plan.warnings[0])

    def test_format_plan_includes_rules(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            management_sources=["192.168.1.0/24"],
            control_plane=["10.0.0.10"],
            guest_networks=["10.20.0.0/24"],
        )
        plan = build_proxmox_control_plane_lockdown_plan(profile)

        rendered = format_proxmox_firewall_plan(plan)

        self.assertIn("Proxmox firewall plan: homelab", rendered)
        self.assertIn("basaltwater-management", rendered)
        self.assertIn("DROP", rendered)

    def test_renders_proxmox_artifacts(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            management_sources=["192.168.1.0/24"],
            control_plane=["10.0.0.10"],
            guest_networks=["10.20.0.0/24"],
        )
        plan = build_proxmox_control_plane_lockdown_plan(profile)

        rendered = render_proxmox_firewall_plan(plan)

        self.assertTrue(rendered.safe_to_apply)
        self.assertEqual(rendered.artifacts[0].path, "/etc/pve/firewall/cluster.fw")
        self.assertIn("[IPSET basaltwater-management]", rendered.artifacts[0].content)
        self.assertIn("[group basaltwater-deny-control-plane]", rendered.artifacts[0].content)
        self.assertIn("GROUP basaltwater-cluster-management", rendered.artifacts[1].content)
        self.assertIn("GROUP basaltwater-deny-control-plane", rendered.artifacts[2].content)

    def test_unsafe_plan_does_not_render_artifacts(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            control_plane=["10.0.0.10"],
        )

        rendered = render_proxmox_firewall_plan(
            build_proxmox_control_plane_lockdown_plan(profile)
        )

        self.assertFalse(rendered.safe_to_apply)
        self.assertEqual(rendered.artifacts, [])

    def test_rendered_format_includes_artifacts(self) -> None:
        profile = NetworkProfile(
            name="homelab",
            management_sources=["192.168.1.0/24"],
            control_plane=["10.0.0.10"],
            guest_networks=["10.20.0.0/24"],
        )
        rendered = render_proxmox_firewall_plan(
            build_proxmox_control_plane_lockdown_plan(profile)
        )

        text = format_rendered_proxmox_plan(rendered)

        self.assertIn("Proxmox rendered plan: homelab", text)
        self.assertIn("/etc/pve/firewall/cluster.fw", text)
        self.assertIn("[OPTIONS]", text)


if __name__ == "__main__":
    unittest.main()

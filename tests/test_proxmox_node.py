"""Tests for lib/proxmox_node.py: container hostname, bridge detection, template resolution."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.proxmox_node import (
    ProvisionError,
    _build_container_hostname,
    _create_container,
    auto_detect_bridge,
    _get_bridge_prefix_length,
    _get_host_nameservers,
    _is_usable_nameserver,
    _parse_pveam_available,
    _resolve_public_key_path,
    _resolve_storage_pool,
    _resolve_template_name,
    _upload_pubkey_to_host,
    _template_sort_key,
    check_container_exists,
    _ssh_opts,
    _ssh_run,
)


class TestBuildContainerHostname(unittest.TestCase):
    def test_friendly_name(self):
        self.assertEqual(
            _build_container_hostname("10.0.0.50", "My Web Server"),
            "my-web-server"
        )

    def test_friendly_name_sanitized(self):
        self.assertEqual(
            _build_container_hostname("10.0.0.50", "Test_Server@2024!"),
            "test-server-2024"
        )

    def test_friendly_name_consecutive_hyphens(self):
        self.assertEqual(
            _build_container_hostname("10.0.0.50", "a--b---c"),
            "a-b-c"
        )

    def test_friendly_name_empty(self):
        self.assertEqual(
            _build_container_hostname("10.0.0.50", ""),
            "lxc-10-0-0-50"
        )

    def test_friendly_name_none(self):
        self.assertEqual(
            _build_container_hostname("10.0.0.50", None),
            "lxc-10-0-0-50"
        )

    def test_ip_derivation(self):
        self.assertEqual(
            _build_container_hostname("192.168.1.100", None),
            "lxc-192-168-1-100"
        )


class TestSshOpts(unittest.TestCase):
    @patch("lib.proxmox_guest.ssh_batch_mode", return_value=True)
    @patch(
        "lib.proxmox_guest.get_workspace_known_hosts_path",
        return_value="/tmp/workspace/known_hosts",
    )
    def test_no_key(self, _mock_known_hosts, _mock_batch_mode):
        opts = _ssh_opts()
        self.assertIn("UserKnownHostsFile=/tmp/workspace/known_hosts", opts)
        self.assertIn("StrictHostKeyChecking=yes", opts)
        self.assertIn("BatchMode=yes", opts)
        self.assertNotIn("-i", opts)

    @patch(
        "lib.proxmox_guest.get_workspace_known_hosts_path",
        return_value="/tmp/workspace/known_hosts",
    )
    def test_with_key(self, _mock_known_hosts):
        opts = _ssh_opts("/path/to/key")
        self.assertIn("UserKnownHostsFile=/tmp/workspace/known_hosts", opts)
        self.assertIn("-i", opts)
        self.assertIn("/path/to/key", opts)


class TestAutoDetectBridge(unittest.TestCase):
    @patch("lib.proxmox_guest._ssh_run")
    def test_detects_vmbr0(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="vmbr0\nvmbr1\n", returncode=0
        )
        result = auto_detect_bridge("10.0.0.1", "root", dry_run=False)
        self.assertEqual(result, "vmbr0")

    @patch("lib.proxmox_guest._ssh_run")
    def test_prefers_vmbr0(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="vmbr1\nvmbr0\n", returncode=0
        )
        result = auto_detect_bridge("10.0.0.1", "root", dry_run=False)
        self.assertEqual(result, "vmbr0")

    @patch("lib.proxmox_guest._ssh_run")
    def test_prefers_bridge_carrying_default_route(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout="vmbr0\nvmbr1\n", returncode=0),
            MagicMock(stdout="vmbr1\n", returncode=0),
        ]
        result = auto_detect_bridge("10.0.0.1", "root", dry_run=False)
        self.assertEqual(result, "vmbr1")

    @patch("lib.proxmox_guest._ssh_run")
    def test_discovers_named_sdn_bridge(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout="sdn-public\nvmbr0\n", returncode=0),
            MagicMock(stdout="sdn-public\n", returncode=0),
        ]

        result = auto_detect_bridge("10.0.0.1", "root", dry_run=False)

        self.assertEqual(result, "sdn-public")

    @patch("lib.proxmox_guest._ssh_run")
    def test_honors_explicit_preferred_bridge(self, mock_run):
        mock_run.return_value = MagicMock(stdout="vmbr0\nvmbr1\n", returncode=0)
        result = auto_detect_bridge(
            "10.0.0.1", "root", dry_run=False, preferred_bridge="vmbr1"
        )
        self.assertEqual(result, "vmbr1")

    @patch("lib.proxmox_guest._ssh_run")
    def test_rejects_default_route_on_non_bridge(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout="vmbr0\n", returncode=0),
            MagicMock(stdout="eno1\n", returncode=0),
        ]
        with self.assertRaisesRegex(Exception, "not a Proxmox bridge"):
            auto_detect_bridge("10.0.0.1", "root", dry_run=False)

    @patch("lib.proxmox_guest._ssh_run")
    def test_no_bridge_raises(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        with self.assertRaises(Exception):
            auto_detect_bridge("10.0.0.1", "root", dry_run=False)

    def test_dry_run_returns_vmbr0(self):
        result = auto_detect_bridge("10.0.0.1", "root", dry_run=True)
        self.assertEqual(result, "vmbr0")


class TestResolveStoragePool(unittest.TestCase):
    @patch("lib.proxmox_guest._ssh_run")
    def test_explicit_pool(self, mock_run):
        mock_run.return_value = MagicMock(stdout="Name Type Status\nlocal-lvm dir active\n", returncode=0)
        result = _resolve_storage_pool("local-lvm", "10.0.0.1", "root", [], "images,rootdir")
        self.assertEqual(result, "local-lvm")

    @patch("lib.proxmox_guest._ssh_run")
    def test_explicit_pool_inactive_raises(self, mock_run):
        mock_run.return_value = MagicMock(stdout="Name Type Status\nlocal dir active\n", returncode=0)
        with self.assertRaises(ProvisionError):
            _resolve_storage_pool("local-lvm", "10.0.0.1", "root", [], "images,rootdir")

    @patch("lib.proxmox_guest._ssh_run")
    def test_explicit_pool_uses_unfiltered_fallback(self, mock_run):
        filtered = MagicMock(stdout="", stderr="unsupported option", returncode=1)
        unfiltered = MagicMock(
            stdout="Name Type Status\nlocal-lvm lvmthin active\n",
            returncode=0,
        )
        mock_run.side_effect = [filtered, unfiltered]
        result = _resolve_storage_pool("local-lvm", "10.0.0.1", "root", [], "images,rootdir")
        self.assertEqual(result, "local-lvm")

    @patch("lib.proxmox_guest._ssh_run")
    def test_auto_selects_active_pool(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Name         Type    Status  Total    Used    Available  %\n"
                   "local        dir     active  100G     20G     80G        20%\n"
                   "local-lvm    lvmph   active  200G     50G     150G       25%\n",
            returncode=0
        )
        result = _resolve_storage_pool("auto", "10.0.0.1", "root", [], "images,rootdir")
        self.assertEqual(result, "local")

    @patch("lib.proxmox_guest._ssh_run")
    def test_auto_no_pools_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Name  Type  Status\n", returncode=0
        )
        with self.assertRaises(Exception):
            _resolve_storage_pool("auto", "10.0.0.1", "root", [], "images,rootdir")

    @patch("lib.proxmox_guest._ssh_run")
    def test_auto_filtered_query_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="unsupported option", returncode=1)
        with self.assertRaises(ProvisionError):
            _resolve_storage_pool("auto", "10.0.0.1", "root", [], "vztmpl")


class TestBridgePrefixDetection(unittest.TestCase):
    @patch("lib.proxmox_guest._ssh_run")
    def test_detects_prefix(self, mock_run):
        mock_run.return_value = MagicMock(stdout="10.0.0.1/23\n", returncode=0)
        result = _get_bridge_prefix_length("10.0.0.1", "root", [], "vmbr0")
        self.assertEqual(result, "23")


class TestCreateContainer(unittest.TestCase):
    @patch("lib.proxmox_node._ssh_run")
    def test_pct_create_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="boom", returncode=1)
        with self.assertRaises(ProvisionError):
            _create_container(
                vmid=100,
                target_ip="10.0.0.50",
                template_path="/var/lib/vz/template/cache/debian.tar.zst",
                memory_mb=2048,
                cores=1,
                root_pool="local-lvm",
                storage_amount="10G",
                cidr_prefix="24",
                bridge="vmbr0",
                gateway="10.0.0.1",
                nameservers=["8.8.8.8"],
                hostname="lxc-10-0-0-50",
                node_ip="10.0.0.1",
                user="root",
                ssh_opts=[],
                dry_run=False,
            )


class TestResolveTemplateName(unittest.TestCase):
    @patch("lib.proxmox_node._ssh_run")
    def test_debian_resolves_latest(self, mock_run):
        # pveam update, pveam available, pveam download
        pveam_available = MagicMock(
            stdout="system/debian-11-standard_11.7-1_amd64.tar.zst\n"
                   "system/debian-12-standard_12.0-1_amd64.tar.zst\n"
                   "system/debian-13-standard_13.1-2_amd64.tar.zst\n"
                   "system/ubuntu-22.04-standard_22.04-1_amd64.tar.zst\n",
            returncode=0
        )
        mock_run.return_value = pveam_available
        result = _resolve_template_name("debian", "local", "10.0.0.1", "root", [])
        self.assertIn("debian-13-standard", result)
        download_call = mock_run.call_args_list[2]
        self.assertIn(
            "flock --exclusive /run/lock/basaltwater-template-",
            download_call.args[3],
        )
        self.assertIn("grep -Fqx", download_call.args[3])
        self.assertEqual(download_call.kwargs["timeout"], 1800)

    @patch("lib.proxmox_node._ssh_run")
    def test_ubuntu_passthrough(self, mock_run):
        pveam_available = MagicMock(
            stdout="system/ubuntu-22.04-standard_22.04-1_amd64.tar.zst\n"
                   "system/ubuntu-24.04-standard_24.04-1_amd64.tar.zst\n",
            returncode=0
        )
        mock_run.return_value = pveam_available
        result = _resolve_template_name("ubuntu", "local", "10.0.0.1", "root", [])
        self.assertIn("ubuntu-24.04", result)

    @patch("lib.proxmox_node._ssh_run")
    def test_dry_run_uses_unversioned_template_placeholder(self, mock_run):
        result = _resolve_template_name(
            "debian", "local", "10.0.0.1", "root", [], dry_run=True
        )
        self.assertEqual(
            result,
            "/var/lib/vz/template/cache/debian-standard_latest_amd64.tar.zst",
        )
        mock_run.assert_called_once()

    @patch("lib.proxmox_node._ssh_run")
    def test_no_match_checks_downloaded(self, mock_run):
        # available returns nothing for "alpine"
        available = MagicMock(stdout="NAME\nsystem/debian-12-standard.tar.zst\n", returncode=0)
        # list shows a downloaded alpine template
        local_list = MagicMock(
            stdout="Name                              Size\n"
                   "alpine-3.19-standard_3.19-1_amd64.tar.zst  3M\n",
            returncode=0
        )
        mock_run.side_effect = [MagicMock(returncode=0), available, local_list]
        result = _resolve_template_name("alpine", "local", "10.0.0.1", "root", [])
        self.assertIn("alpine-3.19", result)

    @patch("lib.proxmox_node._ssh_run")
    def test_no_match_raises(self, mock_run):
        available = MagicMock(stdout="NAME\nsystem/debian-12-standard.tar.zst\n", returncode=0)
        local_list = MagicMock(stdout="Name  Size\n", returncode=0)
        mock_run.side_effect = [MagicMock(returncode=0), available, local_list]
        with self.assertRaises(Exception):
            _resolve_template_name("nonexistent-os", "local", "10.0.0.1", "root", [])


class TestCheckContainerExists(unittest.TestCase):
    @patch("lib.proxmox_node._ssh_run")
    def test_no_containers(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = check_container_exists("10.0.0.1", "10.0.0.50")
        self.assertFalse(result)

    @patch("lib.proxmox_node._ssh_run")
    def test_container_list_failure_is_not_treated_as_empty(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="ssh timeout",
            returncode=255,
        )

        with self.assertRaisesRegex(ProvisionError, "Failed to query containers"):
            check_container_exists("10.0.0.1", "10.0.0.50")

    @patch("lib.proxmox_node._ssh_run")
    def test_container_with_matching_ip(self, mock_run):
        list_result = MagicMock(stdout="100\n101\n", returncode=0)
        config_100 = MagicMock(
            stdout="net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24,gw=10.0.0.1,type=veth\n",
            returncode=0
        )
        mock_run.side_effect = [list_result, config_100]
        result = check_container_exists("10.0.0.1", "10.0.0.50")
        self.assertTrue(result)

    @patch("lib.proxmox_node._ssh_run")
    def test_container_without_matching_ip(self, mock_run):
        list_result = MagicMock(stdout="100\n", returncode=0)
        config_100 = MagicMock(
            stdout="net0: name=eth0,bridge=vmbr0,ip=10.0.0.99/24,gw=10.0.0.1,type=veth\n",
            returncode=0
        )
        mock_run.side_effect = [list_result, config_100]
        result = check_container_exists("10.0.0.1", "10.0.0.50")
        self.assertFalse(result)

    @patch("lib.proxmox_node._ssh_run")
    def test_substring_ip_does_not_false_positive(self, mock_run):
        # Regression: previously "10.0.0.5" would falsely match a container
        # whose IP was "10.0.0.50/24" because of substring matching.
        list_result = MagicMock(stdout="100\n", returncode=0)
        config_100 = MagicMock(
            stdout="net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24,gw=10.0.0.1,type=veth\n",
            returncode=0,
        )
        mock_run.side_effect = [list_result, config_100]
        self.assertFalse(check_container_exists("10.0.0.1", "10.0.0.5"))

    @patch("lib.proxmox_node._ssh_run")
    def test_matches_when_no_cidr_suffix(self, mock_run):
        list_result = MagicMock(stdout="100\n", returncode=0)
        config_100 = MagicMock(
            stdout="net0: name=eth0,bridge=vmbr0,ip=10.0.0.50,gw=10.0.0.1,type=veth\n",
            returncode=0,
        )
        mock_run.side_effect = [list_result, config_100]
        self.assertTrue(check_container_exists("10.0.0.1", "10.0.0.50"))


class TestAutoDetectBridge(unittest.TestCase):
    @patch("lib.proxmox_guest._ssh_run")
    def test_ssh_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="ssh: timeout", returncode=255)
        with self.assertRaises(ProvisionError) as ctx:
            auto_detect_bridge("10.0.0.1")
        self.assertIn("Failed to query bridges", str(ctx.exception))

    @patch("lib.proxmox_guest._ssh_run")
    def test_prefers_vmbr0(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout="vmbr1\nvmbr0\nvmbr2\n", returncode=0),
            MagicMock(stdout="", returncode=0),
        ]
        self.assertEqual(auto_detect_bridge("10.0.0.1"), "vmbr0")


class TestNameserverFiltering(unittest.TestCase):
    def test_loopback_rejected(self):
        self.assertFalse(_is_usable_nameserver("127.0.0.53"))
        self.assertFalse(_is_usable_nameserver("127.0.0.1"))
        self.assertFalse(_is_usable_nameserver("::1"))

    def test_link_local_rejected(self):
        self.assertFalse(_is_usable_nameserver("169.254.1.1"))
        self.assertFalse(_is_usable_nameserver("0.0.0.0"))

    def test_global_accepted(self):
        self.assertTrue(_is_usable_nameserver("1.1.1.1"))
        self.assertTrue(_is_usable_nameserver("8.8.8.8"))

    def test_invalid_rejected(self):
        self.assertFalse(_is_usable_nameserver("not-an-ip"))

    @patch("lib.proxmox_guest._ssh_run")
    def test_filters_loopback_from_resolv_conf(self, mock_run):
        # systemd-resolved scenario: resolvectl prints upstream, resolv.conf has stub.
        mock_run.return_value = MagicMock(
            stdout="1.1.1.1\n8.8.8.8\n127.0.0.53\n",
            returncode=0,
        )
        result = _get_host_nameservers("10.0.0.1", "root", [])
        self.assertEqual(result, ["1.1.1.1", "8.8.8.8"])

    @patch("lib.proxmox_guest._ssh_run")
    def test_falls_back_when_only_loopback(self, mock_run):
        mock_run.return_value = MagicMock(stdout="127.0.0.53\n", returncode=0)
        result = _get_host_nameservers("10.0.0.1", "root", [])
        self.assertEqual(result, ["1.1.1.1"])


class TestParsePveamAvailable(unittest.TestCase):
    def test_real_whitespace_format(self):
        # Actual pveam available output uses whitespace-separated columns.
        stdout = (
            "section          template\n"
            "system           debian-11-standard_11.7-1_amd64.tar.zst\n"
            "system           debian-12-standard_12.7-1_amd64.tar.zst\n"
            "system           debian-13-standard_13.1-2_amd64.tar.zst\n"
            "system           ubuntu-24.04-standard_24.04-1_amd64.tar.zst\n"
            "turnkeylinux     debian-12-turnkey-wordpress_18.0-1_amd64.tar.gz\n"
        )
        debian = _parse_pveam_available(stdout, "debian")
        # Must include both standard debian images, but NOT turnkey-wordpress.
        self.assertIn("debian-11-standard_11.7-1_amd64.tar.zst", debian)
        self.assertIn("debian-12-standard_12.7-1_amd64.tar.zst", debian)
        self.assertIn("debian-13-standard_13.1-2_amd64.tar.zst", debian)
        for entry in debian:
            self.assertNotIn("turnkey", entry)

    def test_legacy_slash_format(self):
        # Older format with section/template prefix should still work.
        stdout = (
            "system/debian-12-standard_12.0-1_amd64.tar.zst\n"
            "system/ubuntu-24.04-standard_24.04-1_amd64.tar.zst\n"
        )
        self.assertEqual(
            _parse_pveam_available(stdout, "debian"),
            ["debian-12-standard_12.0-1_amd64.tar.zst"],
        )

    def test_skips_section_and_blank_lines(self):
        stdout = "section\n\nsystem  debian-12-standard_12.0-1_amd64.tar.zst\n"
        self.assertEqual(
            _parse_pveam_available(stdout, "debian"),
            ["debian-12-standard_12.0-1_amd64.tar.zst"],
        )


class TestTemplateSortKey(unittest.TestCase):
    def test_picks_higher_major_over_lexical(self):
        # Lexically "debian-9" > "debian-10"; ensure version-aware sort wins.
        names = [
            "debian-10-standard_10.7-1_amd64.tar.gz",
            "debian-9-standard_9.7-1_amd64.tar.gz",
            "debian-12-standard_12.0-1_amd64.tar.zst",
        ]
        names.sort(key=_template_sort_key)
        self.assertTrue(names[-1].startswith("debian-12-"))

    def test_picks_higher_minor(self):
        names = [
            "debian-12-standard_12.10-1_amd64.tar.zst",
            "debian-12-standard_12.2-1_amd64.tar.zst",
        ]
        names.sort(key=_template_sort_key)
        self.assertTrue(names[-1].startswith("debian-12-standard_12.10"))


class TestPublicKeyResolution(unittest.TestCase):
    def test_returns_none_when_no_key_set(self):
        self.assertIsNone(_resolve_public_key_path(None))

    def test_returns_none_when_pub_missing(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix="_id", delete=False) as fh:
            fh.write(b"private")
            path = fh.name
        try:
            self.assertIsNone(_resolve_public_key_path(path))
        finally:
            os.unlink(path)

    def test_returns_pub_path_when_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            priv = os.path.join(tmp, "id_test")
            pub = priv + ".pub"
            with open(priv, "w") as fh:
                fh.write("priv")
            with open(pub, "w") as fh:
                fh.write("ssh-ed25519 AAAA...")
            self.assertEqual(_resolve_public_key_path(priv), pub)


class TestPublicKeyUpload(unittest.TestCase):
    @patch("lib.proxmox_node._ssh_run")
    def test_upload_reuses_proxmox_ssh_connection(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="/tmp/basaltwater_pubkey.abc\n",
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
            key_file.write("ssh-ed25519 AAAA test\n")
            key_file.flush()

            path = _upload_pubkey_to_host(
                key_file.name,
                "192.0.2.10",
                "root",
                ["-i", "/keys/proxmox"],
                dry_run=False,
            )

        self.assertEqual(path, "/tmp/basaltwater_pubkey.abc")
        self.assertEqual(
            mock_run.call_args_list[1].kwargs["input_data"],
            "ssh-ed25519 AAAA test\n",
        )


class TestHostnameLengthCap(unittest.TestCase):
    def test_long_friendly_name_truncated_to_63(self):
        long_name = "a" * 100
        result = _build_container_hostname("10.0.0.50", long_name)
        self.assertLessEqual(len(result), 63)

    def test_truncation_does_not_leave_trailing_hyphen(self):
        # "abcdef-" * 10 truncated at 63 might end on a hyphen; ensure stripped.
        weird = ("abcdefghij-" * 10)
        result = _build_container_hostname("10.0.0.50", weird)
        self.assertLessEqual(len(result), 63)
        self.assertFalse(result.endswith("-"))

    def test_short_name_unchanged(self):
        self.assertEqual(_build_container_hostname("10.0.0.50", "web"), "web")


class TestCreateContainerInjectsPubkey(unittest.TestCase):
    @patch("lib.proxmox_node._ssh_run")
    def test_pubkey_arg_added_when_provided(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout="", stderr="", returncode=0),  # pct create
            MagicMock(stdout="status: running\n", returncode=0),  # pct status
        ]
        _create_container(
            vmid=200,
            target_ip="10.0.0.51",
            template_path="local:vztmpl/debian.tar.zst",
            memory_mb=1536,
            cores=2,
            root_pool="local-lvm",
            storage_amount="20G",
            cidr_prefix="24",
            bridge="vmbr0",
            gateway="10.0.0.1",
            nameservers=["1.1.1.1"],
            hostname="web-01",
            node_ip="10.0.0.10",
            user="root",
            ssh_opts=[],
            ssh_pubkey_remote_path="/tmp/basaltwater_pubkey.abc",
            ipv6_cidr="2001:db8::51/64",
            gateway6="2001:db8::1",
        )
        pct_cmd = mock_run.call_args_list[0].args[3]
        self.assertIn("--memory 1536", pct_cmd)
        self.assertIn("--ssh-public-keys", pct_cmd)
        self.assertIn("/tmp/basaltwater_pubkey.abc", pct_cmd)
        self.assertIn("ip6=2001:db8::51/64", pct_cmd)
        self.assertIn("gw6=2001:db8::1", pct_cmd)
        self.assertIn("--start 1", pct_cmd)

    @patch("lib.proxmox_node._ssh_run")
    def test_no_pubkey_arg_when_omitted(self, mock_run):
        mock_run.side_effect = [
            MagicMock(stdout="", stderr="", returncode=0),
            MagicMock(stdout="status: running\n", returncode=0),
        ]
        _create_container(
            vmid=200,
            target_ip="10.0.0.51",
            template_path="local:vztmpl/debian.tar.zst",
            memory_mb=2048,
            cores=2,
            root_pool="local-lvm",
            storage_amount="20G",
            cidr_prefix="24",
            bridge="vmbr0",
            gateway="10.0.0.1",
            nameservers=["1.1.1.1"],
            hostname="web-01",
            node_ip="10.0.0.10",
            user="root",
            ssh_opts=[],
        )
        pct_cmd = mock_run.call_args_list[0].args[3]
        self.assertNotIn("--ssh-public-keys", pct_cmd)


if __name__ == '__main__':
    unittest.main()

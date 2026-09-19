"""Tests for XRDP configuration functions."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from lib.config import SetupConfig
from desktop.xrdp_steps import (
    _detect_xrdp_glamor_render_node,
    _ensure_user_in_group,
    _generate_sesman_ini,
    _generate_xrdp_ini,
    _generate_xorg_conf,
    _install_xrdp_packages,
    _remove_legacy_xrdp_socket_environment,
    _validate_xrdp_tls_certificate,
    harden_xrdp,
    install_xrdp,
)
from desktop.desktop_environment_steps import configure_xfce_for_rdp, install_desktop
from lib.xrdp_certificate import XrdpCertificateHealth


class TestGenerateSesmanIni(unittest.TestCase):
    """Test sesman.ini generation."""

    def setUp(self):
        self.config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="workstation_dev",
        )
    
    def test_generates_valid_ini_format(self):
        """sesman.ini should have proper INI sections."""
        result = _generate_sesman_ini(self.config)
        
        # Check required sections exist
        self.assertIn("[Globals]", result)
        self.assertIn("[Security]", result)
        self.assertIn("[Sessions]", result)
        self.assertIn("[Logging]", result)
        self.assertIn("[Xorg]", result)
        
    def test_uses_xorg_backend_only(self):
        """Should use Xorg backend, not Xvnc."""
        result = _generate_sesman_ini(self.config)
        
        # Xorg section should exist
        self.assertIn("[Xorg]", result)
        self.assertIn("param=/usr/local/libexec/basaltwater-xrdp-Xorg", result)
        self.assertIn("param=xrdp/xorg.conf", result)
        self.assertNotIn("param=/etc/X11/xrdp/xorg.conf", result)
        self.assertIn("param=.local/share/xorg/Xorg.%s.log", result)
        self.assertNotIn("param=.xorgxrdp.%s.log", result)
        
        # Xvnc section should NOT exist
        self.assertNotIn("[Xvnc]", result)
        self.assertNotIn("Xvnc", result)
        
    def test_does_not_include_unsupported_end_session_command(self):
        result = _generate_sesman_ini(self.config)

        self.assertNotIn("EndSessionCommand", result)
        
    def test_security_settings(self):
        """Should include security restrictions."""
        result = _generate_sesman_ini(self.config)
        
        # Security settings
        self.assertIn("AllowRootLogin=false", result)
        self.assertIn("TerminalServerUsers=basaltwater-desktop", result)
        self.assertIn("AllowAlternateShell=false", result)
        self.assertIn("EnableUserWindowManager=false", result)
        self.assertIn("AlwaysGroupCheck=true", result)

    def test_renders_native_session_lifecycle_policy(self):
        self.config.rdp_idle_timeout = 14400

        result = _generate_sesman_ini(self.config)

        self.assertIn("MaxSessions=1", result)
        self.assertIn("KillDisconnected=false", result)
        self.assertIn("DisconnectedTimeLimit=0", result)
        self.assertIn("IdleTimeLimit=14400", result)
        self.assertIn("SessionSockdirGroup=xrdp", result)
        self.assertIn("param=/usr/local/libexec/basaltwater-xrdp-Xorg", result)


class TestGenerateXrdpIni(unittest.TestCase):
    def test_project_template_renders_without_policy_placeholders(self):
        template_path = (
            Path(__file__).resolve().parents[1]
            / "desktop"
            / "config"
            / "xrdp.ini.template"
        )
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="workstation_dev",
        )

        rendered = _generate_xrdp_ini(config, template_path.read_text(encoding="utf-8"))

        self.assertNotIn("{RDP_", rendered)
        self.assertIn("[Channels]", rendered)
        self.assertIn("drdynvc=true", rendered)
        self.assertIn("rdpdr=false", rendered)
        self.assertIn("rail=false", rendered)
        self.assertIn("xrdpvr=false", rendered)
        self.assertIn("certificate=/etc/xrdp/cert.pem", rendered)
        self.assertIn("key_file=/etc/xrdp/key.pem", rendered)
        self.assertNotIn("channel_code", rendered)
        xorg_section = rendered.split("[Xorg]\n", 1)[1]
        self.assertNotIn("\nip=", xorg_section)

    def test_renders_listener_and_secure_coding_channel_defaults(self):
        template = (
            "address={RDP_BIND_ADDRESS}\n"
            "rdpdr={RDP_DRIVE_REDIRECTION}\n"
            "rdpsnd={RDP_AUDIO}\n"
            "cliprdr={RDP_CLIPBOARD}\n"
        )
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="workstation_dev",
        )

        rendered = _generate_xrdp_ini(config, template)

        self.assertIn("address=127.0.0.1", rendered)
        self.assertIn("rdpdr=false", rendered)
        self.assertIn("rdpsnd=false", rendered)
        self.assertIn("cliprdr=true", rendered)

    def test_renders_explicit_channel_policy(self):
        template = (
            "address={RDP_BIND_ADDRESS}\n"
            "rdpdr={RDP_DRIVE_REDIRECTION}\n"
            "rdpsnd={RDP_AUDIO}\n"
            "cliprdr={RDP_CLIPBOARD}\n"
        )
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="workstation_dev",
            rdp_bind_address="10.0.0.25",
            enable_rdp=True,
            rdp_clipboard=False,
            rdp_drive_redirection=True,
            rdp_audio=True,
        )

        rendered = _generate_xrdp_ini(config, template)

        self.assertIn("address=10.0.0.25", rendered)
        self.assertIn("rdpdr=true", rendered)
        self.assertIn("rdpsnd=true", rendered)
        self.assertIn("cliprdr=false", rendered)


class TestEnsureUserInGroup(unittest.TestCase):
    """Test xRDP group membership helper."""

    @patch('desktop.xrdp_steps.run')
    def test_returns_false_when_adduser_fails(self, mock_run):
        """Should not report a change if adduser fails."""
        mock_run.side_effect = [
            Mock(returncode=1, stdout="", stderr=""),
            Mock(returncode=1, stdout="", stderr="permission denied"),
        ]

        result = _ensure_user_in_group("xrdp", "ssl-cert")

        self.assertFalse(result)

    @patch('desktop.xrdp_steps.run')
    def test_returns_true_only_after_verified_membership_change(self, mock_run):
        """Should verify membership after a successful add."""
        mock_run.side_effect = [
            Mock(returncode=1, stdout="", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]

        result = _ensure_user_in_group("xrdp", "ssl-cert")

        self.assertTrue(result)

    @patch('desktop.xrdp_steps.run')
    def test_returns_false_when_membership_cannot_be_verified(self, mock_run):
        """Should not report a change if membership cannot be verified after adduser."""
        mock_run.side_effect = [
            Mock(returncode=1, stdout="", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
            Mock(returncode=1, stdout="", stderr=""),
        ]

        result = _ensure_user_in_group("xrdp", "ssl-cert")

        self.assertFalse(result)


class TestDetectXrdpGlamorRenderNode(unittest.TestCase):
    """Test the opt-in xorgxrdp glamor detection path."""

    @patch("desktop.xrdp_steps.run")
    @patch("desktop.xrdp_steps._ensure_user_in_group")
    @patch("desktop.xrdp_steps._find_xrdp_glamor_render_node")
    def test_returns_accessible_supported_node(
        self, mock_find, mock_ensure_group, mock_run
    ):
        mock_find.return_value = ("/dev/dri/renderD128", "i915")
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        result = _detect_xrdp_glamor_render_node("testuser")

        self.assertEqual(result, ("/dev/dri/renderD128", "i915"))
        mock_ensure_group.assert_called_once_with("testuser", "render")
        mock_run.assert_called_once_with(
            "runuser -u testuser -- test -r /dev/dri/renderD128"
            " -a -w /dev/dri/renderD128",
            check=False,
        )

    @patch("desktop.xrdp_steps.run")
    @patch("desktop.xrdp_steps._ensure_user_in_group")
    @patch("desktop.xrdp_steps._find_xrdp_glamor_render_node")
    def test_falls_back_when_node_is_not_accessible(
        self, mock_find, mock_ensure_group, mock_run
    ):
        mock_find.return_value = ("/dev/dri/renderD128", "amdgpu")
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="")

        result = _detect_xrdp_glamor_render_node("testuser")

        self.assertIsNone(result)
        mock_ensure_group.assert_called_once_with("testuser", "render")

    @patch("desktop.xrdp_steps._ensure_user_in_group")
    @patch("desktop.xrdp_steps._find_xrdp_glamor_render_node")
    def test_falls_back_when_no_supported_node_exists(
        self, mock_find, mock_ensure_group
    ):
        mock_find.return_value = None

        self.assertIsNone(_detect_xrdp_glamor_render_node("testuser"))
        mock_ensure_group.assert_not_called()


class TestValidateXrdpTlsCertificate(unittest.TestCase):
    @patch("desktop.xrdp_steps.inspect_xrdp_certificate_pair")
    def test_accepts_healthy_or_expiring_pair(self, mock_inspect):
        mock_inspect.return_value = XrdpCertificateHealth(
            "warning",
            "/etc/xrdp/cert.pem",
            "/etc/xrdp/key.pem",
            ("certificate expires soon",),
        )

        _validate_xrdp_tls_certificate()

    @patch("desktop.xrdp_steps.run")
    @patch("desktop.xrdp_steps.inspect_xrdp_certificate_pair")
    def test_rejects_unusable_pair(self, mock_inspect, mock_run):
        mock_inspect.return_value = XrdpCertificateHealth(
            "error",
            "/etc/xrdp/cert.pem",
            "/etc/xrdp/key.pem",
            ("private key is unreadable",),
        )

        with self.assertRaisesRegex(RuntimeError, "certificate validation failed"):
            _validate_xrdp_tls_certificate()

        mock_run.assert_called_once_with(
            "systemctl stop xrdp", check=False
        )


class TestInstallXrdp(unittest.TestCase):
    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.remove')
    @patch('desktop.xrdp_steps.os.path.exists', return_value=True)
    def test_removes_legacy_socket_environment(self, _exists, mock_remove, mock_run):
        _remove_legacy_xrdp_socket_environment()

        self.assertEqual(mock_remove.call_count, 2)
        mock_run.assert_called_once_with("systemctl daemon-reload")

    """Test XRDP installation and configuration."""

    def setUp(self):
        for name in ("assert_desktop_idle", "install_session_runtime", "_require_peer_authenticated_sesrun"):
            runtime_patcher = patch(f"desktop.xrdp_steps.{name}")
            self.addCleanup(runtime_patcher.stop)
            runtime_patcher.start()
        source_patcher = patch("desktop.xrdp_steps.get_os_id", return_value="")
        self.addCleanup(source_patcher.stop)
        source_patcher.start()
        certificate_patcher = patch(
            "desktop.xrdp_steps._validate_xrdp_tls_certificate"
        )
        self.addCleanup(certificate_patcher.stop)
        certificate_patcher.start()
        package_patcher = patch(
            "desktop.xrdp_steps.is_package_installed",
            return_value=True,
        )
        self.addCleanup(package_patcher.stop)
        package_patcher.start()
        home_patcher = patch(
            "desktop.xrdp_steps.get_user_home",
            return_value="/home/testuser",
        )
        self.addCleanup(home_patcher.stop)
        home_patcher.start()
        glamor_patcher = patch(
            "desktop.xrdp_steps._detect_xrdp_glamor_render_node",
            return_value=None,
        )
        self.addCleanup(glamor_patcher.stop)
        glamor_patcher.start()

    @patch("desktop.xrdp_steps.run")
    def test_fails_when_xrdp_package_install_fails(self, mock_run):
        with patch("desktop.xrdp_steps.is_package_installed", return_value=False):
            mock_run.return_value = Mock(returncode=100, stdout="", stderr="apt failed")
            config = SetupConfig(
                host="test.example.com",
                username="testuser",
                system_type="workstation_dev",
                desktop="xfce",
            )

            with self.assertRaisesRegex(RuntimeError, "xRDP package installation failed"):
                install_xrdp(config)

    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('desktop.xrdp_steps.is_service_active')
    def test_installs_required_packages(self, mock_is_active, mock_open, mock_makedirs, mock_exists, mock_run):
        """Should install xrdp, xorgxrdp, and utilities when missing."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        package_states = iter([False] * 7 + [True] * 7)

        with patch('desktop.xrdp_steps.is_package_installed', side_effect=package_states):
            config = SetupConfig(
                host="test.example.com",
                username="testuser",
                system_type="workstation_dev",
                desktop="xfce"
            )

            install_xrdp(config)

        install_calls = [c for c in mock_run.call_args_list if 'apt-get install' in str(c)]
        self.assertGreater(len(install_calls), 0)
        first_install = str(install_calls[0])
        self.assertIn("xrdp", first_install)
        self.assertIn("xorgxrdp", first_install)
        self.assertIn("dbus-x11", first_install)

    @patch("desktop.xrdp_steps.run")
    def test_install_handles_modified_package_conffiles(self, mock_run):
        """Package upgrades must not prompt for basaltwater-managed files."""
        mock_run.side_effect = [
            Mock(returncode=0, stdout="Inst xrdp (0.10.6.1-2 Debian:unstable)", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]

        _install_xrdp_packages(("xrdp", "xorgxrdp"))

        commands = [call.args[0] for call in mock_run.call_args_list]
        for command in commands:
            self.assertIn("Dpkg::Options::=--force-confdef", command)
            self.assertIn("Dpkg::Options::=--force-confold", command)

    @patch("desktop.xrdp_steps._configure_xrdp_package_source", return_value="sid")
    @patch("desktop.xrdp_steps.run")
    def test_only_rdp_packages_explicitly_select_sid(self, mock_run, source):
        mock_run.return_value = Mock(returncode=0, stdout="")
        _install_xrdp_packages(("xrdp", "xorgxrdp", "scrot", "dbus-x11"))
        for call in mock_run.call_args_list:
            command = call.args[0]
            self.assertIn("xrdp/sid xorgxrdp/sid scrot dbus-x11", command)
            self.assertNotIn("-t sid", command)

    @patch("desktop.xrdp_steps._configure_xrdp_package_source", return_value="sid")
    @patch("desktop.xrdp_steps.run")
    def test_core_upgrade_guard_handles_architecture_suffix(self, mock_run, source):
        mock_run.return_value = Mock(returncode=0, stdout="Inst libc6:amd64 (2.43 Debian:unstable)")
        with self.assertRaisesRegex(RuntimeError, "core packages: libc6"):
            _install_xrdp_packages(("xrdp",))
        self.assertEqual(mock_run.call_count, 1)


    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('desktop.xrdp_steps.is_service_active')
    def test_creates_xwrapper_config(self, mock_is_active, mock_open_func, mock_makedirs, mock_exists, mock_run):
        """Should create /etc/X11/Xwrapper.config with correct content."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )
        
        install_xrdp(config)
        
        # Check that Xwrapper.config was written
        write_calls = [c for c in mock_open_func().write.call_args_list]
        xwrapper_content = ''.join([str(c[0][0]) for c in write_calls if c[0]])
        
        self.assertIn("allowed_users=anybody", xwrapper_content)
        self.assertIn("needs_root_rights=no", xwrapper_content)

    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('desktop.xrdp_steps.is_service_active')
    def test_creates_apparmor_compatible_xorg_log_directory(
        self, mock_is_active, mock_open_func, mock_makedirs, mock_exists, mock_run
    ):
        """Rootless Xorg should log in the path allowed by Debian AppArmor."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce",
        )

        install_xrdp(config)

        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            "runuser -u testuser -- mkdir -p /home/testuser/.local/share/xorg",
            run_commands,
        )
        self.assertIn("chmod 700 /home/testuser/.local/share/xorg", run_commands)

    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('desktop.xrdp_steps.is_service_active')
    def test_writes_sesman_ini(self, mock_is_active, mock_open_func, mock_makedirs, mock_exists, mock_run):
        """Should write sesman.ini with Xorg backend."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )
        
        install_xrdp(config)
        
        # Check that sesman.ini content was written
        write_calls = [c for c in mock_open_func().write.call_args_list]
        combined_content = ''.join([str(c[0][0]) for c in write_calls if c[0]])
        
        self.assertIn("[Xorg]", combined_content)
        self.assertIn("param=/usr/local/libexec/basaltwater-xrdp-Xorg", combined_content)
        
    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('desktop.xrdp_steps.is_service_active')
    def test_creates_xorg_conf_with_correct_settings(self, mock_is_active, mock_open_func, mock_makedirs, mock_exists, mock_run):
        """X.Org config should have correct driver and screen size."""
        def exists_side_effect(path):
            if path == "/etc/X11/xrdp/xorg.conf.bak":
                return False
            return True

        mock_exists.side_effect = exists_side_effect
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )
        
        install_xrdp(config)
        
        # Find the xorg.conf content
        write_calls = [c for c in mock_open_func().write.call_args_list]
        combined_content = ''.join([str(c[0][0]) for c in write_calls if c[0]])
        
        # Check for xrdpdev driver
        self.assertIn('Driver "xrdpdev"', combined_content)
        
        # Disable the driver's default render-node probe for software mode.
        self.assertIn('Option "DRMDevice" ""', combined_content)
        self.assertIn('owner /dev/shm/ rw,', combined_content)
        self.assertIn('owner /dev/shm/** rw,', combined_content)
        
        # Check for virtual screen size (updated for 4K support)
        self.assertIn('Virtual 3840 2160', combined_content)

        self.assertIn(
            'cp /etc/X11/xrdp/xorg.conf /etc/X11/xrdp/xorg.conf.bak',
            [call.args[0] for call in mock_run.call_args_list],
        )

    def test_generated_xorg_conf_has_managed_resize_settings(self):
        content = _generate_xorg_conf()

        self.assertIn('Driver "xrdpdev"', content)
        self.assertIn('Option "DRMDevice" ""', content)
        self.assertIn('Option "DefaultServerLayout" "X11 Server"', content)
        self.assertNotIn('UseGlamor', content)
        self.assertNotIn('SWCursor', content)
        self.assertIn('Modes "640x480" "800x600" "1024x768"', content)
        self.assertIn('Virtual 3840 2160', content)

    def test_generated_xorg_conf_enables_detected_glamor_node(self):
        content = _generate_xorg_conf("/dev/dri/renderD128")

        self.assertIn('Option "DRMDevice" "/dev/dri/renderD128"', content)
        self.assertIn('Option "DRI3" "1"', content)
        self.assertIn(
            'Option "DRMAllowList" "amdgpu i915 xe msm radeon"',
            content,
        )
        self.assertNotIn('Option "UseGlamor" "false"', content)

    def test_install_enables_glamor_for_detected_node(self):
        mock_file = unittest.mock.mock_open()
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce",
        )

        with (
            patch("desktop.xrdp_steps.run") as mock_run,
            patch("desktop.xrdp_steps.os.path.exists", return_value=True),
            patch("desktop.xrdp_steps.os.makedirs"),
            patch("builtins.open", mock_file),
            patch("desktop.xrdp_steps.is_service_active", return_value=True),
            patch(
                "desktop.xrdp_steps._detect_xrdp_glamor_render_node",
                return_value=("/dev/dri/renderD128", "i915"),
            ),
        ):
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            install_xrdp(config)

        combined_content = "".join(
            str(call.args[0])
            for call in mock_file().write.call_args_list
            if call.args
        )
        self.assertIn('Option "DRMDevice" "/dev/dri/renderD128"', combined_content)
        self.assertIn('Option "DRI3" "1"', combined_content)
        self.assertIn("/dev/dri/renderD128 rw,", combined_content)

    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data='exec {SESSION_CMD}\n')
    @patch('desktop.xrdp_steps.is_service_active')
    def test_uses_lxqt_session_command(self, mock_is_active, mock_open_func, mock_makedirs, mock_exists, mock_run):
        """LXQt RDP sessions should start LXQt, not fall back to XFCE."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="lxqt"
        )

        install_xrdp(config)

        write_calls = [c for c in mock_open_func().write.call_args_list]
        combined_content = ''.join([str(c[0][0]) for c in write_calls if c[0]])
        self.assertIn("DefaultWindowManager=/etc/xrdp/basaltwater-startwm.sh", combined_content)
        from desktop.session_runtime import SESSION_COMMANDS
        self.assertEqual(SESSION_COMMANDS[config.desktop], ["startlxqt"])
        
    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('desktop.xrdp_steps.is_service_active')
    def test_does_not_grant_gpu_groups_for_software_xrdp(self, mock_is_active, mock_open_func, mock_makedirs, mock_exists, mock_run):
        """The xrdpdev software path does not require DRM device access."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )
        
        install_xrdp(config)
        
        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertFalse(any("usermod" in command for command in run_commands))
        self.assertFalse(any("group video" in command for command in run_commands))
        self.assertFalse(any("group render" in command for command in run_commands))

    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    @patch('desktop.xrdp_steps.is_service_active')
    def test_refreshes_xrdp_services_once_after_configuration(self, mock_is_active, mock_open_func, mock_makedirs, mock_exists, mock_run):
        """Should refresh xrdp services once after writing configuration."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )

        install_xrdp(config)

        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("systemctl reload-or-restart xrdp-sesman xrdp", run_commands)
        self.assertNotIn("systemctl restart xrdp-sesman", run_commands)
        self.assertNotIn("systemctl restart xrdp", run_commands)


class TestInstallDesktop(unittest.TestCase):
    @patch("desktop.desktop_environment_steps.install_package", return_value=False)
    @patch("desktop.desktop_environment_steps.is_package_installed", return_value=False)
    def test_fails_when_desktop_package_install_fails(self, _mock_installed, _mock_install):
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce",
        )

        with self.assertRaisesRegex(RuntimeError, "desktop installation failed"):
            install_desktop(config)


class TestHardenXrdp(unittest.TestCase):
    """Test XRDP hardening."""
    
    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.is_service_active')
    def test_skips_if_xrdp_not_installed(self, mock_is_active, mock_exists, mock_run):
        """Should skip if xrdp.ini doesn't exist."""
        mock_exists.return_value = False
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev"
        )
        
        harden_xrdp(config)
        
        # Should not call systemctl restart
        restart_calls = [c for c in mock_run.call_args_list if 'restart' in str(c)]
        self.assertEqual(len(restart_calls), 0)
        
    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.is_service_active')
    def test_refreshes_xrdp_services_when_ssl_cert_membership_changes(self, mock_is_active, mock_exists, mock_run):
        """Should refresh xrdp services if ssl-cert access changes."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        membership_results = iter([1, 0])

        def run_side_effect(cmd, **kwargs):
            if cmd.startswith("id -nG xrdp"):
                return Mock(returncode=next(membership_results), stdout="", stderr="")
            return Mock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev"
        )
        
        harden_xrdp(config)
        
        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("getent group ssl-cert && adduser xrdp ssl-cert", run_commands)
        self.assertIn("systemctl reload-or-restart xrdp", run_commands)
        self.assertFalse(any("xrdp-sesman" in command for command in run_commands))

    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.is_service_active')
    def test_skips_service_refresh_when_already_hardened(self, mock_is_active, mock_exists, mock_run):
        """Should skip reloading active services when ssl-cert access already exists."""
        mock_exists.return_value = True
        mock_is_active.return_value = True
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev"
        )

        harden_xrdp(config)

        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertFalse(any("reload-or-restart" in command for command in run_commands))
        self.assertFalse(any(command.startswith("systemctl start ") for command in run_commands))

    @patch('desktop.xrdp_steps.run')
    @patch('desktop.xrdp_steps.os.path.exists')
    @patch('desktop.xrdp_steps.is_service_active')
    def test_skips_service_refresh_when_ssl_cert_add_fails(self, mock_is_active, mock_exists, mock_run):
        """Should not refresh running services if ssl-cert membership was not changed."""
        mock_exists.return_value = True
        mock_is_active.return_value = True

        def run_side_effect(cmd, **kwargs):
            if cmd.startswith("id -nG xrdp"):
                return Mock(returncode=1, stdout="", stderr="")
            if cmd == "getent group ssl-cert && adduser xrdp ssl-cert":
                return Mock(returncode=1, stdout="", stderr="permission denied")
            return Mock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev"
        )

        harden_xrdp(config)

        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("getent group ssl-cert && adduser xrdp ssl-cert", run_commands)
        self.assertFalse(any("reload-or-restart" in command for command in run_commands))
        self.assertFalse(any(command.startswith("systemctl start ") for command in run_commands))


class TestConfigureXfceForRdp(unittest.TestCase):
    """Test XFCE RDP compatibility configuration."""

    def setUp(self):
        self.helpers = {}
        for name in ("update_channel", "clean_legacy_xfwm", "remove_legacy_pm_stub", "remove_legacy_override"):
            patcher = patch(f"desktop.desktop_environment_steps.{name}")
            self.helpers[name] = patcher.start()
            self.addCleanup(patcher.stop)
        glob_patcher = patch("desktop.desktop_environment_steps.glob.glob", return_value=[])
        self.glob = glob_patcher.start()
        self.addCleanup(glob_patcher.stop)
        home_patcher = patch(
            "desktop.desktop_environment_steps.get_user_home",
            return_value="/home/testuser",
        )
        self.addCleanup(home_patcher.stop)
        home_patcher.start()

    @patch('desktop.desktop_environment_steps.run')
    @patch('desktop.desktop_environment_steps.os.makedirs')
    @patch('desktop.desktop_environment_steps.os.path.exists', return_value=False)
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_notifications_use_session_environment(self, opened, exists, makedirs, run):
        self.glob.side_effect = [["/usr/lib/aarch64-linux-gnu/xfce4/notifyd/xfce4-notifyd"], []]
        configure_xfce_for_rdp(SetupConfig(host="vm", username="testuser", system_type="agent_workstation"))
        content = ''.join(call.args[0] for call in opened().write.call_args_list)
        self.assertIn("Exec=/usr/lib/aarch64-linux-gnu/xfce4/notifyd/xfce4-notifyd", content)
        self.assertNotIn("systemctl --user", content)
    
    @patch('desktop.desktop_environment_steps.run')
    @patch('desktop.desktop_environment_steps.os.makedirs')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_skips_for_non_xfce_desktop(self, mock_open, mock_makedirs, mock_run):
        """Should skip configuration for non-XFCE desktops."""
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="i3"  # Not XFCE
        )
        
        configure_xfce_for_rdp(config)
        
        # Should not create any files
        self.assertEqual(mock_open.call_count, 0)
        
    @patch('desktop.desktop_environment_steps.run')
    @patch('desktop.desktop_environment_steps.os.makedirs')
    @patch('desktop.desktop_environment_steps.os.path.exists')
    @patch('desktop.desktop_environment_steps.os.remove')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_disables_light_locker(self, mock_open, mock_remove, mock_exists, mock_makedirs, mock_run):
        """Should disable light-locker autostart."""
        mock_exists.return_value = False
        mock_run.return_value = Mock(returncode=0)
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )
        
        configure_xfce_for_rdp(config)
        
        # Check that light-locker.desktop was written
        write_calls = [c for c in mock_open().write.call_args_list]
        combined_content = ''.join([str(c[0][0]) for c in write_calls if c[0]])
        
        self.assertIn("Light Locker", combined_content)
        self.assertIn("Hidden=true", combined_content)
        
    @patch('desktop.desktop_environment_steps.run')
    @patch('desktop.desktop_environment_steps.os.makedirs')
    @patch('desktop.desktop_environment_steps.os.path.exists')
    @patch('desktop.desktop_environment_steps.os.remove')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_disables_dpms(self, mock_open, mock_remove, mock_exists, mock_makedirs, mock_run):
        """Should disable DPMS in power manager config."""
        mock_exists.return_value = False
        mock_run.return_value = Mock(returncode=0)
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )
        
        configure_xfce_for_rdp(config)
        
        # The merge helper receives only the managed power settings.
        settings = self.helpers["update_channel"].call_args.args[1]
        self.assertIn("dpms-enabled", settings)
        self.assertIn("false", settings)

    @patch('desktop.desktop_environment_steps.run')
    @patch('desktop.desktop_environment_steps.os.makedirs')
    @patch('desktop.desktop_environment_steps.os.path.exists')
    @patch('desktop.desktop_environment_steps.os.remove')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_removes_stale_display_workarounds(self, mock_open, mock_remove, mock_exists, mock_makedirs, mock_run):
        """Should remove stale display profile and legacy xfsettingsd override."""
        mock_exists.return_value = True
        mock_run.return_value = Mock(returncode=0)

        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )

        configure_xfce_for_rdp(config)

        removed_paths = [call.args[0] for call in self.helpers["remove_legacy_override"].call_args_list]
        self.assertIn("/home/testuser/.config/autostart/xfsettingsd.desktop", removed_paths)
        self.assertIn("/home/testuser/.config/xfce4/xfconf/xfce-perchannel-xml/displays.xml", removed_paths)
        mock_remove.assert_not_called()
        
    @patch('desktop.desktop_environment_steps.run')
    @patch('desktop.desktop_environment_steps.os.makedirs')
    @patch('desktop.desktop_environment_steps.os.path.exists')
    @patch('desktop.desktop_environment_steps.os.remove')
    @patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_retires_pm_stub(self, mock_open, mock_remove, mock_exists, mock_makedirs, mock_run):
        """Delegate exact-match migration; never install a global stub."""
        mock_exists.return_value = False
        mock_run.return_value = Mock(returncode=0)
        
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            desktop="xfce"
        )
        
        configure_xfce_for_rdp(config)
        
        # No global stub is written; only known old content may be removed.
        write_calls = [c for c in mock_open().write.call_args_list]
        combined_content = ''.join([str(c[0][0]) for c in write_calls if c[0]])
        
        self.assertNotIn("pm-is-supported", combined_content)
        self.helpers["remove_legacy_pm_stub"].assert_called_once_with()


if __name__ == '__main__':
    unittest.main()

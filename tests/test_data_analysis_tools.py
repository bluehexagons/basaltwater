"""Tests for the opt-in data-analysis setup capability."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from lib.reconstruct import (
    DATA_ANALYSIS_MARKER_PACKAGES,
    check_package_installed,
    reconstruct_configuration,
    run_reconstruct_command,
)


class TestDataAnalysisConfig(unittest.TestCase):
    def test_setup_parser_preserves_omitted_state_for_patch_merges(self) -> None:
        parser = create_setup_argument_parser("test")

        args = parser.parse_args(["example.com"])
        selected = parser.parse_args(["example.com", "--data-analysis"])
        disabled = parser.parse_args(["example.com", "--no-data-analysis"])

        self.assertIsNone(args.install_data_analysis_tools)
        self.assertTrue(selected.install_data_analysis_tools)
        self.assertFalse(disabled.install_data_analysis_tools)

    def test_saved_config_normalizes_omitted_state_to_false(self) -> None:
        config = SetupConfig(
            host="example.com",
            username="agent",
            system_type="agent_vm",
            install_data_analysis_tools=None,  # type: ignore[arg-type]
        )

        self.assertIs(config.to_dict()["install_data_analysis_tools"], False)

    def test_saved_config_round_trip_retains_selected_bundle(self) -> None:
        config = SetupConfig(
            host="example.com",
            username="agent",
            system_type="agent_vm",
            install_data_analysis_tools=True,
        )

        restored = SetupConfig.from_dict(
            config.host,
            config.system_type,
            config.to_dict(),
        )

        self.assertTrue(restored.install_data_analysis_tools)
        self.assertTrue(restored.install_python)

    def test_patch_merge_distinguishes_omitted_and_disabled_bundle(self) -> None:
        cached = SetupConfig(
            host="example.com",
            username="agent",
            system_type="agent_vm",
            install_data_analysis_tools=True,
        )
        omitted = SetupConfig(
            host="example.com",
            username="agent",
            system_type="agent_vm",
            install_python=None,  # type: ignore[arg-type]
            install_data_analysis_tools=None,  # type: ignore[arg-type]
        )
        disabled = SetupConfig(
            host="example.com",
            username="agent",
            system_type="agent_vm",
            install_python=None,  # type: ignore[arg-type]
            install_data_analysis_tools=False,
        )

        self.assertTrue(
            merge_setup_configs(cached, omitted).install_data_analysis_tools
        )
        self.assertFalse(
            merge_setup_configs(cached, disabled).install_data_analysis_tools
        )

    @patch("basaltwater.get_all_configs")
    def test_info_displays_data_analysis_feature(self, mock_get_configs) -> None:
        mock_get_configs.return_value = [
            {
                "host": "192.0.2.10",
                "system_type": "agent_vm",
                "args": {
                    "username": "agent",
                    "install_python": True,
                    "install_data_analysis_tools": True,
                },
            }
        ]

        output = io.StringIO()
        with redirect_stdout(output):
            result = basaltwater.show_info()

        self.assertEqual(result, 0)
        self.assertIn("Features: Python, Data analysis", output.getvalue())


class TestDataAnalysisReconstruction(unittest.TestCase):
    @patch("lib.reconstruct.subprocess.run")
    def test_package_detection_requires_installed_status(self, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="install ok installed",
        )

        self.assertTrue(check_package_installed("python3-pandas"))
        mock_run.assert_called_once_with(
            ["dpkg-query", "-W", "-f=${Status}", "python3-pandas"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("lib.reconstruct.subprocess.run")
    def test_package_detection_rejects_unvalidated_names(self, mock_run) -> None:
        self.assertFalse(check_package_installed("pandas; touch /tmp/unexpected"))
        mock_run.assert_not_called()

    @patch("lib.reconstruct.detect_smb_mounts", return_value=[])
    @patch("lib.reconstruct.detect_scrub_operations", return_value=[])
    @patch("lib.reconstruct.detect_sync_operations", return_value=[])
    @patch("lib.reconstruct.detect_deployments", return_value=[])
    @patch("lib.reconstruct.detect_samba", return_value=False)
    @patch("lib.reconstruct.detect_python", return_value=False)
    @patch("lib.reconstruct.detect_node", return_value=False)
    @patch("lib.reconstruct.detect_go", return_value=False)
    @patch("lib.reconstruct.detect_av_tools", return_value=False)
    @patch("lib.reconstruct.detect_gl_tools", return_value=False)
    @patch("lib.reconstruct.check_package_installed", return_value=True)
    def test_reconstruct_restores_analysis_and_python_flags(
        self,
        mock_check_package,
        _mock_gl_tools,
        _mock_av_tools,
        _mock_go,
        _mock_node,
        _mock_python,
        _mock_samba,
        _mock_deployments,
        _mock_sync,
        _mock_scrub,
        _mock_mounts,
    ) -> None:
        config, _extras = reconstruct_configuration(
            host="example.com",
            username="agent",
        )

        self.assertTrue(config.install_data_analysis_tools)
        self.assertTrue(config.install_python)
        self.assertEqual(
            [call.args[0] for call in mock_check_package.call_args_list],
            list(DATA_ANALYSIS_MARKER_PACKAGES),
        )

    @patch("lib.reconstruct.reconstruct_configuration")
    def test_reconstruct_output_reports_analysis_bundle(self, mock_reconstruct) -> None:
        mock_reconstruct.return_value = (
            SetupConfig(
                host="localhost",
                username="agent",
                system_type="server_dev",
                install_data_analysis_tools=True,
            ),
            {},
        )

        output = io.StringIO()
        with redirect_stdout(output):
            result = run_reconstruct_command(compact=True)

        self.assertEqual(result, 0)
        self.assertIn('"install_data_analysis_tools": true', output.getvalue())


if __name__ == "__main__":
    unittest.main()

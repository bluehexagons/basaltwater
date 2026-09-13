"""Integration boundaries for combined Gogs and Samba server setup."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from unittest.mock import mock_open, patch

from lib.config import SetupConfig
from lib.validation import validate_gogs_settings
from web import gogs_steps


def _config(**kwargs: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "10.0.0.41",
        "username": "admin",
        "system_type": "server_web",
        "gogs": [":3000", "/srv/data/gogs"],
    }
    values.update(kwargs)
    return SetupConfig(**values)


class TestGogsSambaPathBoundaries(unittest.TestCase):
    def test_sibling_share_on_same_data_mount_is_allowed(self) -> None:
        config = _config(
            samba_shares=[
                ["write", "projects", "/srv/data/shares/projects", "alice:secret"]
            ]
        )

        validate_gogs_settings(config)

    def test_share_containing_live_gogs_data_is_rejected(self) -> None:
        config = _config(
            samba_shares=[["write", "data", "/srv/data", "alice:secret"]]
        )

        with self.assertRaisesRegex(ValueError, "must not overlap"):
            validate_gogs_settings(config)

    def test_share_inside_live_gogs_data_is_rejected(self) -> None:
        config = _config(
            samba_shares=[
                ["read", "repositories", "/srv/data/gogs/repositories", "alice:secret"]
            ]
        )

        with self.assertRaisesRegex(ValueError, "must not overlap"):
            validate_gogs_settings(config)

    def test_samba_metadata_cache_inside_gogs_data_is_rejected(self) -> None:
        config = _config(samba_metadata_cache="/srv/data/gogs/samba-cache")

        with self.assertRaisesRegex(
            ValueError,
            "metadata cache must not overlap the live Gogs data",
        ):
            validate_gogs_settings(config)


class TestGogsConfigPermissions(unittest.TestCase):
    def test_generated_app_config_is_owner_only(self) -> None:
        config = _config()
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "app.ini")
            with (
                patch.object(gogs_steps, "write_gogs_state"),
                patch.object(gogs_steps, "_configure_git_ssh_access"),
                patch.object(gogs_steps, "replace_units"),
                patch.object(gogs_steps, "is_service_active", return_value=True),
                patch.object(gogs_steps, "_wait_for_gogs_ready"),
                patch.object(gogs_steps, "_ensure_gogs_admin_account"),
                patch.object(gogs_steps, "_run_gogs_post_setup_commands"),
                patch.object(gogs_steps, "check_gogs_storage_health"),
                patch.object(gogs_steps, "_maybe_configure_firewall"),
                patch.object(gogs_steps, "run"),
                patch.object(gogs_steps, "open", mock_open(), create=True),
                patch.object(
                    gogs_steps,
                    "generate_gogs_app_ini",
                    return_value="[security]\nSECRET_KEY = secret\n",
                ),
            ):
                gogs_steps._complete_gogs_setup(
                    config,
                    domain="",
                    port=3000,
                    backend_port=3000,
                    data_path="/srv/data/gogs",
                    git_home="/home/git",
                    config_path=config_path,
                    tag_name="v1.0.0",
                    archive_sha256="a" * 64,
                )

            self.assertEqual(stat.S_IMODE(os.stat(config_path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

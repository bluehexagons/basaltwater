"""NAS storage ordering and combined-service regression coverage."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type
from smb import samba_steps
from web.gogs_steps import generate_gogs_service


class NasCompositionTests(unittest.TestCase):
    def test_plain_and_combined_nas_prepare_storage_before_services(self):
        for combined in (False, True):
            with self.subTest(combined=combined):
                config = SetupConfig(
                    host="nas", username="admin", system_type="server_lite",
                    enable_samba=True,
                    samba_shares=[["write", "files", "/srv/files", "alice:password"]],
                    storage_mounts=[["data", "/srv", "ext4", "empty"]],
                    gogs=[":3000", "/srv/gogs"] if combined else None,
                    enable_syncthing=combined,
                )
                functions = [step.__name__ for _, step in get_steps_for_system_type(config)]
                self.assertIn("setup_vm_storage", functions)
                # Samba is reconciled by remote_setup after the composed plan.
                for service in (["setup_gogs", "setup_syncthing"] if combined else []):
                    self.assertLess(functions.index("setup_vm_storage"), functions.index(service))

    def test_gogs_requires_its_configuration_data_mount(self):
        with patch("web.gogs_steps._get_git_home", return_value="/home/git"):
            unit = generate_gogs_service("/srv/gogs/custom/conf/app.ini")
        self.assertIn('RequiresMountsFor="/srv/gogs/custom"', unit)

    def test_samba_preflights_all_mounts_before_touching_any_share(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory) / "smb.conf"
            conf.write_text("[global]\n")
            config = SetupConfig(host="nas", username="admin", system_type="server_lite", samba_shares=[
                ["write", "one", "/srv/one", "alice:password"],
                ["write", "two", "/srv/two", "alice:password"],
            ])
            with patch.object(samba_steps, "SMB_CONF_PATH", str(conf)), patch.object(samba_steps, "assert_declared_storage_mount", side_effect=[None, RuntimeError("missing mount")]), patch.object(samba_steps, "_prepare_samba_share") as prepare:
                with self.assertRaisesRegex(RuntimeError, "missing mount"):
                    samba_steps.reconcile_samba_shares(config)
                prepare.assert_not_called()

    def test_samba_rejects_redirected_share_before_permissions_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "smb.conf").write_text("[global]\n")
            (root / "outside").mkdir()
            (root / "share").symlink_to(root / "outside", target_is_directory=True)
            config = SetupConfig(host="nas", username="admin", system_type="server_lite", samba_shares=[
                ["write", "files", str(root / "share"), "alice:password"],
            ])
            with patch.object(samba_steps, "SMB_CONF_PATH", str(root / "smb.conf")), patch.object(samba_steps, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "symlinked Samba"):
                    samba_steps.reconcile_samba_shares(config)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

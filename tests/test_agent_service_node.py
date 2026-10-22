"""Verify service interpreter provenance before executing downloaded code."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import service_node


class ServiceNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def archive(self, extra: tuple[str, str] | None = None, *, member_root: str = "node") -> tuple[Path, str]:
        path = self.root / "node.tar.xz"
        with tarfile.open(path, "w:xz") as archive:
            for name in (f"{member_root}/bin/node", f"{member_root}/lib/node_modules/npm/bin/npm-cli.js"):
                member = tarfile.TarInfo(name)
                member.size = 4
                member.mode = 0o777
                archive.addfile(member, io.BytesIO(b"code"))
            link = tarfile.TarInfo(f"{member_root}/bin/npm")
            link.type = tarfile.SYMTYPE
            link.linkname = "../lib/node_modules/npm/bin/npm-cli.js"
            archive.addfile(link)
            if extra:
                member = tarfile.TarInfo(extra[0])
                member.type = tarfile.SYMTYPE
                member.linkname = extra[1]
                archive.addfile(member)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def extract(self, path: Path, digest: str) -> Path:
        return service_node._extract_node_archive(path, self.root / "extracted", digest=digest, member_root="node")

    def test_checksum_failure_precedes_extraction(self) -> None:
        path, _ = self.archive()
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.extract(path, "0" * 64)
        self.assertFalse((self.root / "extracted").exists())

    def test_archive_keeps_internal_npm_link_and_removes_write_permissions(self) -> None:
        root = self.extract(*self.archive())
        self.assertEqual((root / "bin/npm").read_bytes(), b"code")
        self.assertEqual((root / "bin/node").stat().st_mode & 0o777, 0o755)

    def test_archive_rejects_escaping_paths_and_links_before_extraction(self) -> None:
        for member in (("../escape", "node/bin/node"), ("node/bin/bad", "../../../escape"),
                       ("/node/bad", "/etc/passwd")):
            with self.subTest(member=member):
                with self.assertRaisesRegex(RuntimeError, "Unsafe|Escaping"):
                    self.extract(*self.archive(member))
                self.assertFalse((self.root / "extracted").exists())

    def test_untrusted_ownership_mode_or_symlink_prevents_execution(self) -> None:
        for mode, uid in ((stat.S_IFREG | 0o755, 1000), (stat.S_IFREG | 0o775, 0),
                          (stat.S_IFLNK | 0o777, 0)):
            with self.subTest(mode=mode, uid=uid), \
                    patch.object(service_node, "validate_filesystem_path"), \
                    patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=uid)), \
                    patch.object(service_node, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "root-owned"):
                    service_node.verify_service_node()
                run.assert_not_called()

    def test_exact_supported_interpreter_and_sanitized_environment_required(self) -> None:
        with patch.object(service_node, "_root_owned_path"), \
                patch.object(service_node, "run") as run, \
                patch.dict(os.environ, {"NODE_OPTIONS": "--require=untrusted", "NODE_PATH": "/untrusted",
                                        "npm_config_userconfig": "/untrusted/npmrc",
                                        "NODE_EXTRA_CA_CERTS": "/trusted/ca.pem"}):
            for version in ("v20.20.0", "v24.22.0", "v26.12.1", "invalid"):
                run.return_value = SimpleNamespace(stdout=version)
                with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "requires"):
                    service_node.verify_service_node()
            run.return_value = SimpleNamespace(stdout="v" + service_node.SERVICE_NODE_VERSION)
            service_node.verify_service_node()
            environment = run.call_args.kwargs["env"]
        self.assertNotIn("NODE_OPTIONS", environment)
        self.assertNotIn("NODE_PATH", environment)
        self.assertNotIn("npm_config_userconfig", environment)
        self.assertEqual(environment["NODE_EXTRA_CA_CERTS"], "/trusted/ca.pem")

    def test_unprivileged_install_never_creates_paths_or_downloads(self) -> None:
        with patch.object(service_node.os, "geteuid", return_value=1000), \
                patch.object(service_node, "directory_fd") as directory, \
                patch.object(service_node.urllib.request, "urlopen") as download:
            with self.assertRaisesRegex(RuntimeError, "root-owned"):
                service_node.install_service_node()
        directory.assert_not_called()
        download.assert_not_called()

    def test_existing_corrupt_runtime_is_preserved_without_download(self) -> None:
        with patch.object(service_node.os, "geteuid", return_value=0), \
                patch.object(service_node.platform, "machine", return_value="x86_64"), \
                patch.object(service_node.platform, "system", return_value="Linux"), \
                patch.object(service_node, "directory_fd"), \
                patch.object(service_node, "_root_owned_path"), \
                patch.object(service_node.os.path, "lexists", return_value=True), \
                patch.object(service_node, "verify_service_node", side_effect=RuntimeError("corrupt")), \
                patch.object(service_node.urllib.request, "urlopen") as download:
            with self.assertRaisesRegex(RuntimeError, "corrupt"):
                service_node.install_service_node()
        download.assert_not_called()

    def test_download_checksum_failure_never_publishes_or_executes_runtime(self) -> None:
        member_root = f"node-v{service_node.SERVICE_NODE_VERSION}-linux-x64"
        archive, _digest = self.archive(member_root=member_root)
        installed = self.root / 'published'
        with patch.object(service_node.os, "geteuid", return_value=0), \
                patch.object(service_node.platform, "machine", return_value="x86_64"), \
                patch.object(service_node.platform, "system", return_value="Linux"), \
                patch.object(service_node, "SERVICE_NODE_PARENT", str(self.root)), \
                patch.object(service_node, "SERVICE_NODE_ROOT", str(installed)), \
                patch.object(service_node, "directory_fd"), \
                patch.object(service_node, "_root_owned_path"), \
                patch.object(service_node.urllib.request, "urlopen", return_value=io.BytesIO(archive.read_bytes())), \
                patch.object(service_node, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                service_node.install_service_node()
        self.assertFalse(installed.exists())
        self.assertFalse(list(self.root.glob('.node-stage-*')))
        run.assert_not_called()

"""Tests for basaltwater."""

from __future__ import annotations

from contextlib import nullcontext
from functools import wraps
import os
import unittest
from unittest.mock import patch

# Existing orchestration fixtures mock target commands and use temporary trees.
# Scope the host adapter to those test executions. Importing this package must
# never disable a production safety boundary for the rest of the process.
if os.name == "posix":
    from lib import managed_storage
    from lib.managed_storage import operation_guard as real_storage_operation_guard

    _host_fixture_modules = frozenset({
        "test_audit_regressions", "test_build_serving", "test_build_target_activation",
        "test_deployment_project_tooling", "test_deploy_recovery", "test_godot_deploy",
        "test_legacy_deployment", "test_manifest_deploy", "test_refresh", "test_remote_setup",
        "test_samba_management", "test_cachyos_remote", "test_build_target_bootstrap",
        "test_kiosk_config", "test_build_setup_transport",
    })
    _run_test_case = unittest.TestCase.run

    @wraps(_run_test_case)
    def _run_with_host_fixture(self, *args, **kwargs):
        if type(self).__module__.rsplit(".", 1)[-1] not in _host_fixture_modules:
            return _run_test_case(self, *args, **kwargs)
        # Context-managed patches survive another fixture's patch.stopall(),
        # and always restore the real boundary when the individual test exits.
        with patch.object(managed_storage, "operation_guard", lambda *a, **k: nullcontext()):
            return _run_test_case(self, *args, **kwargs)

    unittest.TestCase.run = _run_with_host_fixture

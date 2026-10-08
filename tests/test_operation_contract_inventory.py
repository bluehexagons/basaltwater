"""Keep the command inventory accurate for aliases and explicit contracts."""

from __future__ import annotations

import unittest

from scripts.audit_command_contracts import inventory_source


class TestCommandInventory(unittest.TestCase):
    def test_aliases_literal_policies_and_result_consumption(self):
        source = """
from lib.remote_utils import run as execute
import lib.remote_utils as remote
from lib import remote_utils as utilities
def apply(check):
    execute(["required"])
    probe = execute(["probe"], check=False)
    remote.run(["cleanup"], check=False)
    return utilities.run(["wrapper"], check=check)
"""
        records = inventory_source(source, "common/example.py")
        self.assertEqual([record["contract"] for record in records],
                         ["required", "caller-managed", "best-effort", "delegated"])
        self.assertEqual([record["result"] for record in records],
                         ["discarded", "consumed", "discarded", "consumed"])
        self.assertTrue(all(record["scope"] == "apply" for record in records))

    def test_positional_check_and_expanded_options(self):
        records = inventory_source("""
from lib.remote_utils import run
run(["optional"], False)
run(["dynamic"], **options)
run(["required"], check=True, **options)
subprocess.run(["different"])
""", "lib/example.py")
        self.assertEqual([record["contract"] for record in records],
                         ["best-effort", "delegated", "required"])

    def test_internal_helper_and_nested_scope_are_included(self):
        records = inventory_source("""
class Probe:
    def available(self):
        return run(["probe"], check=False).returncode == 0
""", "lib/remote_utils.py")
        self.assertEqual(records[0]["scope"], "Probe.available")
        self.assertEqual(records[0]["contract"], "caller-managed")

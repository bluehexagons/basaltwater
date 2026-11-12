"""Tests for basaltwater."""

from __future__ import annotations

from contextlib import nullcontext
import os

# Existing orchestration fixtures mock target commands and use temporary trees.
# Keep the new host boundary out of those fixtures too. Storage tests exercise
# the real preflight and locks directly, and explicitly restore this boundary
# when testing its composition.
if os.name == "posix":
    from lib import managed_storage
    from lib.managed_storage import operation_guard as real_storage_operation_guard

    # This is a suite-wide host adapter, not an individual test's patcher:
    # several existing fixtures call patch.stopall() during their cleanup.
    managed_storage.operation_guard = lambda *args, **kwargs: nullcontext()

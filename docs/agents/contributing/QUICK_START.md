# Contributor quick start

These instructions complement the root [`AGENTS.md`](../../../AGENTS.md).
Preserve unrelated worktree changes and follow higher-priority session
instructions.

## Before editing

1. Run `git status -sb`.
2. Read the complete target file.
3. Identify the matching validator and capability helper.
4. Use `apply_patch`, then check `git diff --check`.

## Essential pattern

```python
from __future__ import annotations

from lib.config import SetupConfig
from lib.machine_state import can_modify_kernel


def setup_kernel_feature(config: SetupConfig) -> None:
    """Apply a kernel-specific setup step when supported."""
    del config
    if not can_modify_kernel():
        print("  ✓ Skipping (container)")
        return
    # Implementation
```

## Quick checks

```bash
python3 -m py_compile file.py
basaltw setup server_web test.com --dry-run
python3 -m unittest discover -s tests
```

## Non-negotiable rules

- Use `from __future__ import annotations`.
- Never commit secrets.
- Validate inputs at the CLI or setup boundary.
- Mock system calls; use `tempfile.TemporaryDirectory()` for test storage.
- Keep operator documentation current.

`lib.remote_utils.run()` isolates every command in a process group and applies
a one-hour default timeout (override with a positive `timeout`, or `None` for
an intentionally unbounded command). On timeout it terminates the group and
bounds output collection, including when a detached child retains a pipe.
Cleanup can add up to two five-second grace periods to the requested timeout.
Keyboard interruption also terminates the isolated command group before
propagating cancellation to the caller.
Detached processes that create their own sessions require caller-specific
supervision if they must also be terminated.

For helper selection and setup composition, use the
[quick reference](QUICK_REFERENCE.md). For the complete contributor workflow,
use the [contributor guide](README.md).

# Contributor troubleshooting

| Problem | Check |
| --- | --- |
| Import error | Run from the repository root and use package imports under `lib/` |
| Permission or capability failure | Use the operation-specific helper such as `can_manage_firewall` or `can_restart_system` |
| Test changed local state | Mock system calls and use `tempfile.TemporaryDirectory()` |
| Unexpected diff | Run `git status -sb`, preserve unrelated changes, and inspect `git diff` before staging |
| Setup affects the wrong machine | Use `--dry-run`; `remote_setup.py` mutates the target, while `bootstrap`/`self-setup` mutates the controller |

## Debugging machine capabilities

```python
from lib.machine_state import can_modify_kernel, is_container

print(f"container={is_container()}, can_modify_kernel={can_modify_kernel()}")
```

Do not assume every operation is unavailable in a container. Select the helper
that represents the action being changed. For invalid input, inspect
`lib.validation` and `lib.validators` before changing command flow.

## Edit conflicts

| Tool result | Response |
| --- | --- |
| Target text not found | Re-read the file and include more exact context |
| Multiple matches | Narrow the context or use a uniquely scoped replacement |

For architecture and test expectations, see the
[contributor guide](README.md).

## Filesystem mocks on newer Python versions

Do not call `Path.is_file()` from a mocked `os.path.isfile()` callback, or
`Path.is_dir()` from a mocked `os.path.isdir()` callback. Python 3.14 delegates
these pathlib checks to the corresponding `os.path` functions, causing recursive
mock calls. Evaluate a temporary fixture's state before entering the patch when
the test needs a fixed result; for changing state, capture the original function
before patching and call it only on temporary fixture paths.

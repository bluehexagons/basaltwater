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

## Target-side helper imports and automatic swap

Launch trusted scripts under `lib/` with `python3 -I /absolute/path/to/helper.py`
when they bootstrap the source root for package imports. Executing them directly
without isolated mode puts `lib/` on the import path before the standard library;
`lib/types.py` can then shadow Python's `types` module before the bootstrap runs.
Use `lib.vendor_installer.installer_command()` for shared vendor installers.
Test generated commands in a fresh interpreter from an unrelated directory;
importing the helper in the test process does not exercise script startup.

Automatic swap sizing reads physical RAM from `/proc/meminfo` (`MemTotal` in
kB, converted to MiB). `free` has no `--output=total` option. If `MemTotal` is
missing, invalid, or unreadable, setup warns and leaves swap unchanged.

## Nginx variables for deployment hostnames

Hostnames and site filenames can contain hyphens, but generated Nginx variable
references must use ASCII letters, digits, and underscores. `_make_cache_maps()`
hashes each site's original slug into a short hexadecimal suffix, keeping names
valid and within Nginx's default variable hash buckets even for long hostnames.
Do not reuse a filename slug directly as a variable name or replace all
punctuation with the same separator. Test configurations with hyphenated and
long domains and both static and proxy routes.

When a new configuration fails `nginx -t`, reconciliation restores and tests
the previous configuration. A successful second test confirms rollback; the
original setup step still fails so the rejected configuration can be corrected.

## Boolean CLI options on newer Python versions

Pass `type` and `choices` only to argument actions that consume a value.
`argparse.BooleanOptionalAction` does not consume one: those keywords were
deprecated in Python 3.12 and removed in 3.14, even when passed as `None`.
For helpers that build both switches and value options, use separate argument
branches. Parser regression tests can treat `DeprecationWarning` as an error
to catch this incompatibility on Python 3.13 as well.

## Filesystem mocks on newer Python versions

Do not call `Path.is_file()` from a mocked `os.path.isfile()` callback, or
`Path.is_dir()` from a mocked `os.path.isdir()` callback. Python 3.14 delegates
these pathlib checks to the corresponding `os.path` functions, causing recursive
mock calls. Evaluate a temporary fixture's state before entering the patch when
the test needs a fixed result; for changing state, capture the original function
before patching and call it only on temporary fixture paths.

For ownership fixtures, mock `os.stat()` and `os.lstat()` on the specific
temporary paths. Do not assume mocking `Path.stat()` also controls
`Path.lstat()`: Python 3.14 calls `os.lstat()` directly.

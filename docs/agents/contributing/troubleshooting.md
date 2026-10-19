# Contributor troubleshooting

| Problem | Check |
| --- | --- |
| Import error | Run from the repository root and use package imports under `lib/` |
| Permission or capability failure | Use the operation-specific helper such as `can_manage_firewall` or `can_restart_system` |
| Test changed local state | Mock system calls and use `tempfile.TemporaryDirectory()` |
| Unexpected diff | Run `git status -sb`, preserve unrelated changes, and inspect `git diff` before staging |
| Setup affects the wrong machine | Use `--dry-run`; `remote_setup.py` mutates the target, while `bootstrap`/`self-setup` mutates the controller |

## Machine and account fixtures

Do not assume every operation is unavailable in a container. Select the
capability helper that represents the action being changed. For invalid input,
inspect `lib.validation` and `lib.validators` before changing command flow.

Mock the target distribution, account, package manager, and service capabilities
explicitly. Parsing and validation should not discover the controller's account,
timezone, or services. Supply inert defaults when constructing comparison
configurations; target discovery belongs in preflight.

Passwd fixtures should use `pwd.struct_passwd`, which supports both attributes
and tuple indexing. Containers can omit login environment variables and expose
incomplete mocks. Reproduce that environment for an affected selection with:

```bash
env -u LOGNAME -u USER -u LNAME -u USERNAME \
  .venv/bin/python -m unittest tests.test_cachyos_ssh_options
```

Use `get_user_home()` for existing accounts, including root and homes outside
`/home`. Create SSH directories and keys as the target account; a root-owned
directory with mode `0700` prevents that account from writing keys.

## Python version differences

Pass `type` and `choices` only to argument actions that consume a value.
`argparse.BooleanOptionalAction` does not consume one: those keywords were
deprecated in Python 3.12 and removed in 3.14, even when passed as `None`.
Treat `DeprecationWarning` as an error in focused parser tests.

Do not call `Path.is_file()` from a mocked `os.path.isfile()` callback, or
`Path.is_dir()` from a mocked `os.path.isdir()` callback. Python 3.14 delegates
these pathlib checks to the corresponding `os.path` functions, causing recursive
mock calls. Evaluate a temporary fixture's state before entering the patch when
the test needs a fixed result; for changing state, capture the original function
before patching and call it only on temporary fixture paths.

For ownership fixtures, mock `os.stat()` and `os.lstat()` on the specific
temporary paths. Do not assume mocking `Path.stat()` also controls
`Path.lstat()`: Python 3.14 calls `os.lstat()` directly.

Launch trusted scripts under `lib/` with `python3 -I /absolute/path/to/helper.py`
when they bootstrap the source root. Without isolated mode, `lib/types.py` can
shadow the standard library's `types` module before the bootstrap runs. Use
`lib.vendor_installer.installer_command()` for shared vendor installers.
Exercise script startup from an unrelated directory in a fresh interpreter;
importing the helper in the test process does not check this boundary.

## Failed installs and configuration activation

Verify required applications and plugins after installation and propagate
failure to the setup runner. Successful package commands alone do not prove
the requested application is ready. Test reruns and explicit opt-outs alongside
initial installation.

For files, runtime publication, Nginx, and systemd units, use the owning
[transaction mechanism](transaction-safety.md) and preserve the previous state
when activation fails. A successful rollback does not make the rejected setup
step successful. Keep corruption, permission, interrupted-publication, and
rollback cases when consolidating tests.

For a live incomplete operation, follow the
[operator recovery guide](../../operations/transaction-recovery.md); do not
delete locks or recovery markers to make a retry proceed.

For check selection and test maintenance, see the
[contributor guide](README.md). Shared validators, capability helpers, and
command contracts are in the [quick reference](quick-reference.md).

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

## Debian source repair after repository changes

When replacing `basaltwater-debian.sources`, calculate missing base and security
suites using the other source files. The managed file is replaced in full, so
its existing entries must not count as coverage that survives replacement.
Regression tests should remove an external base or security source after an
initial repair and verify that both suites remain available on subsequent runs.

## Account homes and fresh SSH directories

Use `get_user_home()` for existing accounts, including root and users whose
homes are outside `/home`. SSH keys, Python tooling and uv timer checks must use
the same passwd home. Create `.ssh` and generate its keys as the target account;
a root-created directory with mode `0700` prevents the user from writing keys.

The shell installer requires `curl` even when `wget` is available: the uv
bootstrap downloads its installer with `curl`. Include it in both prerequisite
detection and automatic package installation.

## Managed swap blocks in fstab

Replace an existing managed swap block in place and preserve surrounding mount
entries and whitespace. Removing both separating newlines can concatenate two
unrelated mounts into an invalid line. Cover replacement, removal and reruns with
mount entries on both sides of the managed block.

## Required desktop application installs

Verify requested applications and their required plugins after installation,
and propagate failed installs to the setup runner. Reconcile the system Flathub
remote even when Flatpak is already installed. Callers must check
`install_flatpak_if_needed()` before proceeding; its result includes remote
preparation. LibreOffice retains its APT fallback when Flatpak preparation fails.

## Service configuration failures

Rejected nginx configuration must fail the setup step after restoring the
previous site and enabled entry. Restore regular files and symlinks as they
were, including a previously absent entry. Reload failures also require rollback
because activation may have partially completed. The webhook setup removes a
newly rejected configuration; Antistatic restores an existing site's contents
and enabled state.

Stage deploy sudoers rules in a dot-prefixed candidate, validate the candidate
and current include tree before replacement, then validate the activated tree.
Restore the previous rule if activation fails. Never remove a working rule and
report success after a failed validation.

SMB mount units must escape literal `%` as `%%` in `Description`, `What`,
`Where` and `Options`; keep the actual directory and credentials filename
unchanged. Otherwise systemd expands specifiers and may reject the unit or use
the wrong share or credentials path. See the upstream
[mount unit reference](https://github.com/systemd/systemd/blob/v257/man/systemd.mount.xml).

An explicit device-pairing opt-out removes the existing portal, credentials,
Connect restart units and pairing HTTPS route even when the T3 web interface
is also disabled. That cleanup belongs in the step plan independently of web
installation; a web opt-out alone does not request pairing removal.

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

## CachyOS configuration and private runtimes

CachyOS parsing and configuration validation must not probe the host. Supply
an inert timezone when building shared `SetupConfig` defaults so those paths
do not invoke `timedatectl`. Existing saved timezone metadata remains accepted;
this profile preserves the workstation timezone and rejects `--timezone`.
The comparison baseline also needs an inert username so validation never
discovers the controller's login account. Passwd test fixtures should use
`pwd.struct_passwd`, which supports both attributes and tuple indexing. Exercise
account selection without `LOGNAME`, `USER`, `LNAME`, or `USERNAME`; containers
can omit them, exposing mocks that ordinary login environments conceal.
SSH state discovery belongs in target preflight after validation.

Remote source publication must set the staged runtime root to mode `0700`
after `copytree`, before activation. Copying a group-writable source directory
otherwise publishes permissions that the next run rejects. Keep rejecting
unsafe existing runtime directories, and test repeated publication with an
explicitly group-writable source rather than relying on the test runner's umask.

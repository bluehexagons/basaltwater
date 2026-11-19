# System administration user rename

[Topic index](sysadmin.md)

## user rename

Rename the account stored as a target's setup username. The operation runs as
a detached, root-owned systemd job on the target, logs out the old account,
moves the conventional home directory, reconciles Basaltwater-managed units
and state, verifies SSH access as the new account, and then updates the current
controller cache.

```
basaltw user rename <host> <new_username> [options]
```

| Option | Description |
|--------|-------------|
| `host` | Remote host with a saved Basaltwater setup |
| `new_username` | New local login name |
| `--admin-user USER` | Administrative SSH account (defaults to saved target user) |
| `-i, --key PATH` | SSH identity file |
| `--new-home PATH` | Explicit absolute destination home |
| `--keep-home` | Keep the existing home path |
| `-n, --dry-run` | Run target preflight without changing anything |
| `-y, --yes` | Skip the destructive-operation confirmation |
| `--resume OPERATION_ID` | Resume a staged operation after interruption |

Examples:

```bash
basaltw user rename myserver newadmin
basaltw user rename myserver newadmin --admin-user root --yes
basaltw user rename myserver newadmin --dry-run
basaltw user rename myserver newadmin --resume 7d4e...
```

The saved setup username is used as the old name and is checked against the
target account and both target state files. The command requires
non-interactive remote `sudo`, a local account, a systemd target, and an
unambiguous home directory. `root`, existing destination accounts/groups,
mounted or symlinked homes, conflicting cron/mail or managed SMB credential
files, and unmanaged system configuration references are rejected. Historical
setup records are not rewritten. The target user's SSH session is expected to
disconnect during the migration, so the systemd start request is detached
from that session. Prefer a separate administrative account with
`--admin-user` to observe the job without starting sessions for the account
being renamed. Polling uses that identity while it can read a valid status,
including an earlier attempt's status while waiting for a resumed worker.
The new username is a fallback when the administrative identity cannot
connect through SSH. On resume, either identity can read the staged
operation; home-selection options and `--dry-run` cannot be changed.
An authenticated session whose status command is temporarily unavailable or
returns malformed JSON (including shell banners or a non-object JSON value)
waits without guessing another login. Malformed output cannot establish
completion; polling remains bounded and reports an unresolved operation if
no valid result arrives. Once polling reaches the new identity, it
keeps using it instead of repeatedly trying the retired login and triggering
failed-login protection.

Preflight also rejects unmanaged systemd fragments and drop-ins that reference
the old login or home, including overrides of managed services. Reconcile those
references before retrying. A managed SMB mount whose mountpoint would change
is rejected before services stop: its systemd mount and automount filenames
must match their `Where=` path. Use `--keep-home` for a home-local mount, or
remove and recreate the mount/automount pair outside the home before renaming.
The rename command does not migrate mount unit names.

Home-moving renames also reject discoverable rootless container storage under
the old home before services stop. Podman can persist absolute static-directory
paths in its database; moving an initialized store can cause a database
configuration mismatch even when UID/GID and subordinate ID ranges survive.
Use `--keep-home` to retain those paths, or plan and validate a deliberate,
operator-owned container migration before requesting a home move. Rename does
not run Podman, reset or export stores, or modify application databases.

The storage guard checks the conventional
`~/.local/share/containers/storage`, plus paths found in static
`XDG_DATA_HOME`, `XDG_CONFIG_HOME`, and `CONTAINERS_STORAGE_CONF` assignments in
the old home's `.config/environment.d/*.conf`. It reads `storage.conf` from
both the conventional and configured XDG containers configuration directories,
plus `/usr/share/containers/storage.conf` and `/etc/containers/storage.conf`,
and checks their `[storage]` `graphroot` and `rootless_storage_path` values.
An explicit `CONTAINERS_STORAGE_CONF` must name a file called `storage.conf`.
Only absolute, normalized paths with literal values or simple `$HOME`,
`${HOME}`, `$USER`, or known XDG variable substitutions are supported. Each
configuration file is limited to 64 KiB and the environment directory to 64
entries; conflicting assignments and environment values with escapes or inline
comments are rejected.
Configuration and store paths are opened without following symlinks.
Malformed, unreadable, oversized, or unsupported configuration blocks the move;
user `containers.conf`, `containers.conf.d`, and `storage.conf.d` also require
operator inspection because their path/precedence rules are outside this guard.

A discovered store is allowed only when absent or completely empty. Any entry,
including an empty metadata subdirectory or lock file, conservatively blocks
the move; neither database contents nor `auth.json` are read. Stores configured
outside the old home are not moved and are outside this check. This is bounded
discovery, not a universal rootless migration check: shell startup scripts,
per-session environment, command-line overrides, arbitrary configuration
locations, and other container engines are not discovered. Operators using
those must inspect their own storage locations and use `--keep-home` or manage
the migration themselves. The guard does not certify external stores for a
username change.

Interrupted operations retain their original login shell and managed service
activation snapshots in the private target operation directory under
`/var/lib/basaltwater/user-renames/OPERATION_ID/`. Resume rechecks safety before
identity cutover without replacing those recovery facts with the temporary
`nologin` shell or stopped service state. An identity, external login shell, or
managed-unit inventory change blocks recovery for inspection. Failure cleanup
restores the original shell only after the current worker installed its
temporary shell, and only while that shell and the recorded UID/GID still
match; a rejected external shell change is preserved.

From `configuration-updated` and later phases, resume requires the destination
account's recorded UID, primary GID, destination home and original shell, plus
the new username in the saved machine/setup files, before reloading, enabling
or starting services. Previously present state files must still exist, and the
old account must be absent. Identity drift blocks recovery for inspection.
Managed home-file rewrites protect existing destination paths on every pass,
including destinations such as `/home/alice archive` that contain the old home
prefix, so interrupted configuration updates can safely revisit those files.

Fresh cutover changes the login and home with one native `usermod` invocation.
If interrupted after the login changed, resume checks the renamed account's
UID, primary GID, shell, and current home before stopping its managed units and
terminating its live sessions and user manager again. It moves the home only
when the account still points to the recorded original directory and the
destination is absent. An already moved home is retained. Unexpected or
missing homes, or both home paths existing, block recovery for inspection
rather than replaying an uncertain move. These checks preserve the original
shell and service snapshots. Concurrent logins can still make native account
tools reject a busy user; keep the affected account logged out during recovery.
The container storage guard is rechecked before any pending home move on
resume, expanding static paths with both the recorded old username and the
current new username while using the original home. This covers storage
initialized after login cutover but before the home move. Historical operations
whose home has already moved can continue reconciliation; that does not
establish that their container databases work.

Each resume records a durable attempt ID before starting the detached job;
the controller waits for that attempt's
result, so an earlier failed status cannot terminate the new wait. If the new
worker cannot start or acknowledge the attempt, the command reports the
operation as in progress or unreachable and retains the same `--resume` path.
Wait for an active job to finish before requesting another resume.

The target must already have the current Basaltwater target files installed so
the migration helper is available under `/opt/basaltwater/lib/`. Run the
normal target upgrade first if preflight reports that the helper is missing.

# Sysadmin Convenience Commands

Quick-access commands for daily server administration tasks. All commands
inherit SSH credentials (username, key, port) from the saved Basaltwater
configuration for the host when available, so you rarely need to pass them
explicitly.

Commands started in a terminal can prompt for a password-protected key's
passphrase. For piped commands and parallel operations, preload the key with
`ssh-agent`/`ssh-add`; see [SSH authentication](SSH.md). This is especially
important for `fan`, `df`, and `reachable`, which may create several SSH
connections at once.

Non-interactive commands use a one-hour command deadline, including transfers,
mount operations, service changes, key installation, and each fan-out/upgrade
target. Health and one-shot logs use 120 seconds; reachability uses a 10-second
total bound in addition to its 5-second connection limit. Timeout terminates
the local process group and reports status 124 (aggregate commands report a
failed host). Completed remote mutations are not rolled back, and detached
remote work may require inspection before retrying. Interactive `ssh` and
explicit `logs --follow` sessions remain unbounded.

## Command Index

| Command | Summary |
|---------|---------|
| [`mount`](#mount) | Mount a remote directory via sshfs |
| [`umount`](#umount) | Unmount an sshfs mount |
| [`health`](#health) | One-shot health summary for a remote host |
| [`ssh`](#ssh) | Open an SSH session using saved config |
| [`push`](#push) | Rsync a local path to a remote host |
| [`pull`](#pull) | Rsync a remote path to local |
| [`key push`](#key-push) | Install a local public key on a remote host |
| [`df`](#df) | Multi-host disk usage table |
| [`fan`](#fan) | Run a command on multiple hosts in parallel |
| [`svc`](#svc) | Manage a systemd service |
| [`logs`](#logs) | Show or follow journalctl output |
| [`upgrade`](#upgrade) | Run apt upgrade across one or more hosts |
| [`reachable`](#reachable) | Check which saved hosts respond via SSH |
| [`user rename`](#user-rename) | Rename a managed target login and reconcile its configuration |
| [`agent auth login`](AGENT_AUTHENTICATION.md#authorize-codex-on-a-target) | Authorize a target-owned Codex session from a minimal control system |

---

## mount

Mount a remote directory using sshfs. The local mountpoint is created
automatically if it does not exist.

```
basaltw mount <host>:<remote_path> <local_path> [options]
```

| Option | Description |
|--------|-------------|
| `host:path` | Remote host and path, e.g. `myhost:/srv/data` |
| `local_path` | Local mountpoint (created if absent) |
| `--ro` | Mount read-only |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file (overrides saved config) |
| `-p, --port N` | SSH port |

```bash
basaltw mount myserver:/var/log /mnt/myserver-logs
basaltw mount myserver:/srv/data /mnt/data --ro
basaltw mount 10.0.0.10:/home/admin /mnt/admin -u admin -i ~/.ssh/id_ed25519
```

Mounts use `reconnect` and `ServerAliveInterval=30` so short network
interruptions recover automatically.

---

## umount

Unmount an sshfs mount by local path or by host name.

```
basaltw umount <local_path|hostname>
```

```bash
basaltw umount /mnt/myserver-logs   # by local path
basaltw umount myserver             # by host name (finds the mount automatically)
```

When given a host name, `findmnt` is used to locate the mount point. If more
than one mount matches the host, you must specify the local path directly.

---

## health

SSH into a host and print a structured health summary:

- Uptime and load average
- Memory and swap usage
- Disk usage across all mounts (entries above 85% are prefixed with `[!]`)
- Failed systemd units
- Last 10 journal errors
- Pending apt upgrade count
- Reboot-required status

```
basaltw health <host> [options]
```

| Option | Description |
|--------|-------------|
| `host` | Remote host (IP or hostname) |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file (overrides saved config) |

```bash
basaltw health myserver
basaltw health 10.0.0.10 -u admin
```

Each section degrades gracefully — if a command is unavailable on the remote
host (e.g. `journalctl` on a non-systemd system), that section prints
`(unavailable)` and the rest continues.

---

## ssh

Open an interactive SSH session using credentials from the saved Basaltwater
config for the host. Adds `ControlMaster=auto` for connection reuse.

```
basaltw ssh <host> [options] [-- <remote_command>]
```

| Option | Description |
|--------|-------------|
| `host` | Remote host (IP or hostname) |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file (overrides saved config) |
| `-p, --port N` | SSH port |
| `-- cmd ...` | Run a remote command instead of opening a shell |

```bash
basaltw ssh myserver
basaltw ssh myserver -- journalctl -f
basaltw ssh myserver -- systemctl status nginx
```

Uses `execvp` to replace the current process, so the terminal is fully
interactive (signals, terminal size, etc. all work correctly).

---

## push

Sync a local file or directory to a remote host using rsync over SSH.

```
basaltw push <local_path> <host>:<remote_path> [options]
```

| Option | Description |
|--------|-------------|
| `local_path` | Local source path |
| `host:path` | Remote destination |
| `--delete` | Delete files from the remote that are absent locally |
| `-n, --dry-run` | Show what would be transferred without transferring |
| `-u, --username U` | SSH username |
| `-i, --key PATH` | SSH identity file |
| `-p, --port N` | SSH port |

```bash
basaltw push ./dist myserver:/var/www/app
basaltw push ./data myserver:/backup/data --dry-run
basaltw push ./data myserver:/backup/data --delete
```

When `--delete` is specified without `--dry-run`, a confirmation prompt is
shown before proceeding.

---

## pull

Sync a remote file or directory to local using rsync over SSH.

```
basaltw pull <host>:<remote_path> [<local_path>] [options]
```

| Option | Description |
|--------|-------------|
| `host:path` | Remote source |
| `local_path` | Local destination (defaults to `./<basename>`) |
| `-n, --dry-run` | Show what would be transferred without transferring |
| `-u, --username U` | SSH username |
| `-i, --key PATH` | SSH identity file |
| `-p, --port N` | SSH port |

```bash
basaltw pull myserver:/var/log ./logs
basaltw pull myserver:/srv/data          # saves to ./data
basaltw pull myserver:/var/log --dry-run
```

---

## key push

Append the local public key to `~/.ssh/authorized_keys` on the remote host.
Idempotent — skips if the key is already present. Creates the `.ssh` directory
and `authorized_keys` file with correct permissions if needed.

```
basaltw key push <host> [options]
```

| Option | Description |
|--------|-------------|
| `host` | Remote host (IP or hostname) |
| `--pubkey PATH` | Public key file (default: `~/.ssh/id_ed25519.pub`) |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH key to authenticate with |

```bash
basaltw key push myserver
basaltw key push myserver --pubkey ~/.ssh/id_rsa.pub
basaltw key push 10.0.0.10 -u admin -i ~/.ssh/bootstrap_key
```

---

## df

Run `df -h` on one or more remote hosts in parallel and print a combined table
sorted by percent used. Entries above 85% are prefixed with `[!]`.

```
basaltw df <host> [<host2> ...] [options]
```

| Option | Description |
|--------|-------------|
| `hosts` | One or more remote hosts |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file |

```bash
basaltw df myserver
basaltw df web1 web2 db1
basaltw df web1 web2 -u admin
```

Hosts that cannot be reached are reported as warnings and omitted from the table.

---

## fan

Run a shell command on multiple hosts concurrently, printing each host's output
in a labeled block followed by a pass/fail summary.

```
basaltw fan <host> [<host2> ...] [options] -- <command>
```

| Option | Description |
|--------|-------------|
| `hosts` | One or more remote hosts |
| `-- cmd` | Command to execute (required) |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file |

```bash
basaltw fan web1 web2 -- uptime
basaltw fan web1 web2 db1 -- systemctl restart myapp
basaltw fan web1 web2 -u deploy -- git -C /srv/app pull
```

All hosts run concurrently. Output is serialized per host after all results are
collected, sorted by hostname for stable output.

---

## svc

Manage a systemd service on a remote host. Defaults to `status`.

```
basaltw svc <host> <unit> [action] [options]
```

| Argument | Description |
|----------|-------------|
| `host` | Remote host (IP or hostname) |
| `unit` | Unit name, e.g. `nginx`, `myapp.service` |
| `action` | `status` (default), `restart`, `start`, `stop`, `enable`, `disable`, `reload` |

| Option | Description |
|--------|-------------|
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file |

```bash
basaltw svc myserver nginx              # show status
basaltw svc myserver nginx restart      # restart and show status
basaltw svc myserver myapp.service stop
basaltw svc myserver nginx enable
```

Mutating actions (`restart`, `start`, `stop`, `enable`, `disable`, `reload`)
use `sudo` and display a status readout afterward.

---

## logs

Show recent journal entries for a systemd unit, or follow live output.

```
basaltw logs <host> <unit> [options]
```

| Option | Description |
|--------|-------------|
| `-n, --lines N` | Number of lines to show (default: 50) |
| `-f, --follow` | Follow live output |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file |

```bash
basaltw logs myserver nginx
basaltw logs myserver myapp -f
basaltw logs myserver nginx -n 200
```

Uses `execvp` so `-f` gives a true live stream with correct terminal behavior.

---

## upgrade

With no host arguments, update the installed Basaltwater worktree on its
selected channel. With one or more hosts, run `apt-get update && apt-get
upgrade` on those hosts in parallel and report which require a reboot.

```bash
basaltw upgrade
```

The installed-source form refuses to overwrite local worktree changes. See
[Installation](INSTALLATION.md#channels-and-upgrades) for channel selection.

### Remote host upgrade

```
basaltw upgrade <host> [<host2> ...] [options]
```

| Option | Description |
|--------|-------------|
| `hosts` | One or more remote hosts |
| `--check` | Only count pending upgrades; do not install |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file |

```bash
basaltw upgrade myserver
basaltw upgrade web1 web2 db1
basaltw upgrade web1 web2 --check     # show pending counts only
```

Requires `sudo` access on the remote host. Hosts that fail are reported and do
not affect the result for other hosts.

---

## reachable

Probe hosts via SSH and print a latency table. Connections have a 5-second
limit and the full probe has a 10-second deadline.

```
basaltw reachable [<hosts>] [options]
```

| Option | Description |
|--------|-------------|
| `hosts` | Explicit host list (default: all saved configs) |
| `--pattern GLOB` | Filter saved hosts by glob pattern |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file |

```bash
basaltw reachable                      # probe all saved hosts
basaltw reachable '*.example.com'      # glob filter on saved hosts
basaltw reachable --pattern 'web*'
basaltw reachable web1 web2 db1        # explicit host list
```

All probes run concurrently. The summary line lists any unreachable hosts by
name. Exit code is 1 if any host is unreachable, 0 if all respond.

---

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
from that session. On resume, the controller tries both the configured
administrative identity and the new username; home-selection options and
`--dry-run` cannot be changed during a resume.

The target must already have the current Basaltwater target files installed so
the migration helper is available under `/opt/basaltwater/lib/`. Run the
normal target upgrade first if preflight reports that the helper is missing.

# System administration files and access

[Topic index](sysadmin.md)

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
basaltw ssh myserver -u root -- id
basaltw ssh -u root myserver -- python3 /opt/basaltwater/basaltwater.py headscale-client status --url https://control.example.com
```

Connection options may precede or follow the host, before the first `--`.
That delimiter is consumed once; every following token belongs to the remote
command, including flags and later literal `--` tokens. Unknown connection
options before it are errors. An explicit `--` requires a command; omit it
to open an interactive session. The direct form `basaltw ssh myserver id -u`
also works: the first command word ends connection-option parsing.

Fresh service VMs may not have a `basaltw` launcher. For root-local commands,
use `python3 /opt/basaltwater/basaltwater.py` as root on the VM, as above.

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

Push and pull validate the host, username, SSH port, and paths before starting
rsync. Local paths remain local operands, including names containing colons or
starting with a dash. Remote arguments travel through rsync's protected argument
protocol. A trailing slash on a local source retains rsync's usual meaning:
copy the directory's contents instead of the directory itself.

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

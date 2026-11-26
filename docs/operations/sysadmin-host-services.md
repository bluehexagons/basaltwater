# System administration health and services

[Topic index](sysadmin.md)

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

Hosts can be names saved with `setup --name`, IP addresses, or DNS hostnames.
Saved names resolve to their configured host address and SSH credentials in the
active workspace. Output keeps the names you supplied. Use `--username` or
`--key` to override the saved credentials.

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
basaltw fan mesh-dns mesh-app -- systemctl is-active tailscaled
basaltw fan -u root mesh-dns mesh-app -- journalctl -u tailscaled --no-pager -n 20
```

The first `--` is required to separate hosts and connection options from the
remote command. Options may appear before, between, or after hosts, before
the delimiter. All remote tokens, including flags and later literal `--`,
are preserved. A missing command or unknown connection option is an error.

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
[Installation](../getting-started/installation.md#channels-and-upgrades) for channel selection.

### Remote host upgrade

```
basaltw upgrade <host> [<host2> ...] [options]
```

| Option | Description |
|--------|-------------|
| `hosts` | One or more remote hosts |
| `--check` | Count pending upgrades by simulating with cached APT package lists |
| `--with-new-pkgs` | Allow new packages required by upgrades, such as a new kernel package |
| `-u, --username U` | SSH username (overrides saved config) |
| `-i, --key PATH` | SSH identity file |

```bash
basaltw upgrade myserver
basaltw upgrade web1 web2 db1
basaltw upgrade web1 web2 --check
```

Package upgrades require root SSH or noninteractive `sudo` access in the
target. The command stops on a failed APT refresh, prohibits package removal,
and reports failures per target. It reports the installed Debian version when
available and lists guests with a reboot-required marker. Schedule reboots
separately.

### Debian VMs on Proxmox

Target the VM's saved setup name or guest address. For a point update such as
Debian 13.7 to 13.8, keep the guest's APT sources on its installed codename. Use
the same package options for preview and apply. The preview uses local package
lists on the guest, which may be older than the lists fetched during apply.

```bash
basaltw upgrade debian-app --with-new-pkgs --check
basaltw upgrade debian-app --with-new-pkgs
basaltw vm health debian-app
```

For a saved Proxmox VM, `basaltw vm reboot debian-app` requests a guest reboot
when you are ready. Use `basaltw proxmox rolling-update` for the hypervisors.

For Debian 12 to 13, first bring Bookworm up to date, verify a recoverable VM
backup, and test access to the Proxmox console. Follow Debian's release upgrade
procedure inside the guest, including repository changes and the minimal and
full upgrades. After reboot, check the guest version, SSH, and its services.
[Debian 13 upgrade guide](https://www.debian.org/releases/trixie/release-notes/upgrading.en.html)

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

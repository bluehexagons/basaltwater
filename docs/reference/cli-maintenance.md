# Maintenance and host utilities

[Documentation index](command-line.md)

## Maintenance and Utilities

### Local Proxmox kiosk

```text
basaltw kiosk status [--json]
basaltw kiosk doctor [--json]
sudo basaltw kiosk start [--json]
sudo basaltw kiosk stop [--json]
sudo basaltw kiosk restart [--json]
```

These commands target the local host and accept no remote host argument.
`status` and `doctor` are read-only and support ordinary accounts where local
permissions allow inspection. `start`, `stop`, and `restart` require root and
control the managed kiosk session. All five commands support `--json`.
See [Proxmox kiosk](../workstations/proxmox-kiosk.md) for setup and operation.

### Firmware audit and updates

```text
basaltw firmware audit [--no-refresh] [--json] [--install-dependencies]
basaltw firmware update [DEVICE_ID] [--no-refresh] [--allow-running-guests] [--install-dependencies] [--yes]
```

Both commands operate on the local machine through `fwupdmgr`. When it is
missing, Basaltwater offers to install the Debian `fwupd` package with APT;
`--install-dependencies` records that consent without a separate dependency
prompt. `audit` reports DMI identity, related package versions, supported
devices, and available fwupd releases. Metadata is refreshed unless
`--no-refresh` is supplied.

`update` repeats the audit and requires a separate firmware confirmation. On a
Proxmox host, running guests block the update unless
`--allow-running-guests` is explicit, while an incomplete guest-state check
always blocks it. `--yes` skips the firmware confirmation and forwards
fwupd's non-interactive consent; it never bypasses the audit or guest check.
Basaltwater always suppresses fwupd's reboot prompt and does not reboot the
machine. See [Firmware auditing and
updates](../operations/firmware.md) for coverage limits, legacy vendor firmware, and
recovery precautions.

### Cleaning obsolete local configuration

Use `cleanup` after upgrading Basaltwater when a saved setup or development
registry entry was written by an older, incompatible revision. A host argument
limits the operation to that setup host:

```bash
# Inspect only the saved setup state for this VM.
basaltw cleanup 192.168.0.41 --dry-run

# Remove obsolete setup state after reviewing the findings.
basaltw cleanup 192.168.0.41 --yes

# Inspect and clean every invalid setup cache and Proxmox record.
basaltw cleanup --dry-run
basaltw cleanup --yes

# Select one category explicitly.
basaltw cleanup --setup-cache --yes
basaltw cleanup --proxmox-registry --yes
```

With a host argument, setup-cache cleanup is selected by default. Add
`--proxmox-registry` when the host is a registered Proxmox node, or omit the
host to inspect the entire local Proxmox registry. The command removes only
setup-cache files that cannot be loaded by the current `SetupConfig` and
Proxmox records that fail the current schema or validation. Valid records are
preserved. It makes a timestamped copy of every changed file in
`<workspace>/cleanup-backups/` before modifying anything; use `--workspace` to
select a different workspace. `--yes` is required for non-interactive use,
and `--dry-run` never changes files.

### GitHub Maintenance

```bash
basaltw maintenance github --root ~/repos audit
basaltw maintenance github --root ~/repos prune --yes
basaltw maintenance github --root ~/repos prune --delete-caches --yes
```

Defaults: keep 2 releases, delete expired artifacts, prune caches only when
`--delete-caches` is set, and treat caches as stale after 90 days.

Use `--dry-run` to inspect planned deletions. The command discovers repositories
from the current directory by default, or from repeatable `--root` paths, and
requires the local `gh` CLI to be authenticated.

### Recurring Host Maintenance

Security monitoring, package updates, ecosystem updates, restart checks, and
cleanup are installed as systemd services and timers during setup. Each
standard setup rerun also runs one bounded cleanup pass near the end, so a
dormant host does not have to wait for the next scheduled trigger. Inspect the
recurring jobs with:

```bash
sudo systemctl list-timers --all '*auto-*' '*security-monitor*' '*cleanup-*'
sudo journalctl -u cleanup-maintenance.service -n 100 --no-pager
```

See [`maintenance.md`](../operations/maintenance.md) for schedules and policy controls.

### Network Inventory

```text
basaltw network list
basaltw network init <profile> [--management CIDR] [--control-plane CIDR] [--guest-network CIDR]
basaltw network add-host <profile> <name> <address> [--provider NAME] [--role ROLE]
basaltw network import-proxmox <profile> [--host NAME] [--tag TAG]
basaltw network import-proxmox-guests <profile> [--host NAME] [--tag TAG]
basaltw network plan-proxmox <profile> [--proxmox] [--json]
```

`plan-proxmox` is read-only and requires at least one management source and
one control-plane address before it will produce a non-error plan.

### Proxmox Management

```text
basaltw proxmox add <name> <address> [--user USER] [--key PATH]
basaltw proxmox hosts
basaltw proxmox remove <name-or-address>
basaltw proxmox probe <host>
basaltw proxmox probe-cluster <address> [--user USER] [--key PATH] [--tag TAG]
basaltw proxmox join-cluster <host> --via <member> [--link INDEX=IP]... [--join-address HOST] [--timeout SECONDS] [--dry-run] [--yes]
basaltw proxmox audit <host> [<host> ...] [--json]
basaltw proxmox rolling-update <target> [<target> ...] [--dry-run] [--resume] [--reboot-timeout SECONDS]
basaltw proxmox top <host> [<host> ...]
basaltw proxmox plan place [options]
basaltw proxmox plan rebalance [options]
basaltw proxmox ls <host>
basaltw proxmox status <host> <vmid>
basaltw proxmox start <host> <vmid>
basaltw proxmox pause <host> <vmid>  # alias: suspend
basaltw proxmox resume <host> <vmid>
basaltw proxmox stop <host> <vmid> [--force]
basaltw proxmox destroy <host> <vmid> [-y] [--force]
basaltw proxmox health <host> <vmid> [--no-ssh]
basaltw proxmox config <host> <vmid> [--pending]
basaltw proxmox reconfigure <host> <vmid> --set KEY=VALUE [--set ...]
basaltw proxmox modify <host> <vmid> [--cores N] [--memory N[M|G]]
basaltw proxmox resize-disk <host> <vmid> <volume> <size>
basaltw proxmox backups <host> <vmid>
basaltw proxmox backup <host> <vmid> [--storage POOL] [--mode MODE] [--compress FORMAT] [--timeout SECONDS] [--dry-run]
basaltw proxmox snapshots <host> <vmid>
basaltw proxmox snapshot <host> <vmid> <name> [--description TEXT] [--dry-run]
basaltw proxmox rollback <host> <vmid> <name> [--dry-run]
basaltw proxmox delsnapshot <host> <vmid> <name> [-y] [--dry-run]
basaltw proxmox migrate <host> <vmid> <target> [--online] [--with-local-disks]
basaltw proxmox clean-disks <host> [--delete] [--yes] [--dry-run]
basaltw proxmox unlock <host> <vmid> [--dry-run]
basaltw proxmox notifications install-webhook <host> <url> [--send-test]
basaltw proxmox notifications test-webhook <host>
basaltw proxmox [shell]

basaltw vm list <host> [--json]
basaltw vm show <local-name> [--json]
basaltw vm show <host> <id> [--json]
basaltw vm health <local-name> [--no-ssh] [--json]
basaltw vm health <host> <id> [--no-ssh] [--json]
basaltw vm stats <target> [<id>] [--json]
basaltw vm status <target> [<id>] [--json]
basaltw vm start <target> [<id>] [--json]
basaltw vm pause <target> [<id>] [--json]  # alias: suspend
basaltw vm resume <target> [<id>] [--json]
basaltw vm shutdown <target> [<id>] [--timeout SECONDS] [--json]
basaltw vm stop <target> [<id>] [--json]
basaltw vm reboot <target> [<id>] [--timeout SECONDS] [--json]  # alias: restart
basaltw vm autostart <target> [<id>] [--json]
basaltw vm autostart <target> [<id>] --enable [--order N] [--start-delay SECONDS] [--shutdown-timeout SECONDS] [--json]
basaltw vm autostart <target> [<id>] --disable [--json]
basaltw vm snapshot list <local-name> [--json]
basaltw vm snapshot list <host> <id> [--json]
basaltw vm backup list <local-name> [--json]
basaltw vm backup list <host> <id> [--json]
basaltw vm destroy <local-name> [-y] [--force]
basaltw vm destroy <host> <id> [-y] [--force]
```

`probe` caches bridge, gateway, DNS, and storage recommendations. `audit` is
read-only and checks core Proxmox services, cluster quorum, active tasks,
configured storage, root free space, guest locks, running guests, and the reboot
marker. It exits nonzero when the host is not healthy and supports stable JSON
output for automation.

`backup` requires local node ownership and verifies a new nonempty archive for
the VMID on the selected active backup storage after `vzdump` exits successfully.
`--timeout` bounds the SSH wait in scripts and terminals: default 86400 seconds
(24 hours), allowed range 1–604800 (seven days). On timeout, remote completion
remains uncertain; inspect Proxmox tasks and storage before retrying. There is
no automatic retry or cleanup. `--dry-run` makes read-only preflight queries
without starting a backup. The Proxmox shell supports the same options. See
[backup operation limits](../operations/proxmox.md#placement-backups-and-migration) for archive
verification and recovery boundaries.

`join-cluster` joins an empty registered Proxmox VE 9.x node through a registered,
quorate cluster member with root SSH access. It obtains a temporary root API
ticket over trusted SSH and pins the member's API certificate, so existing 2FA
policy can remain enabled. The ticket is passed through stdin and is never
stored or printed. `--dry-run` performs read-only inspections without issuing
credentials or joining. Normal execution confirms replacement of `/etc/pve`;
`--yes` skips only confirmation. Specify every Corosync link with `--link INDEX=IP`
for clusters with multiple networks. Success verifies both nodes and fresh SSH
access before updating the new host's registered SSH identity to the member's
identity. Interrupted joins require task and membership inspection before retry.
See [joining clusters with 2FA](../operations/proxmox.md#joining-a-cluster-with-two-factor-authentication)
for prerequisites, limits, and recovery behavior.

Registered host records contain `schema_version: 1` and `provider: proxmox`.
`v2.0.0` rejects incompatible development records rather than
guessing how to interpret them. Remove an incompatible record by its stored
name or address, then register it again:

```bash
basaltw proxmox remove pve1
basaltw proxmox add pve1 10.0.0.10 --user root --key ~/.ssh/proxmox_ed25519
```

The provider-neutral `basaltw vm ...` commands provide stable inventory,
inspection, health, power-state lifecycle, snapshot, and backup-list output,
plus confirmed QEMU VM destruction. Their JSON envelope includes
`schema_version`, `provider`, `host`, `operation`, and `resources`. With
`--json`, stdout contains only the JSON result; provider progress, warnings,
and errors go to stderr. A failure before a result is available leaves stdout
empty and returns a nonzero exit status. Health checks can return a JSON
result with a nonzero status when the guest is unhealthy. In the command
shapes above, `<target> [<id>]` means either `<host> <id>` or the exact
friendly `--name` (or saved IP address) of a Basaltwater setup with the ID
omitted. Name resolution is fail-closed: the saved setup must identify a QEMU
VM and a registered provider host, and both its expected Proxmox name and
configured IPv4 address must match the observed guest. Tags are not accepted
as VM names.

Use `start`, `pause`/`suspend`, `resume`, `shutdown`, `stop`, and
`reboot`/`restart` for power-state lifecycle. `shutdown` requests a clean guest
shutdown, while `stop` immediately powers off the guest and can cause data
loss. Shutdown and reboot accept the native provider `--timeout`; every
lifecycle command queries and reports the resulting provider state. The
provider-specific `proxmox` lifecycle paths remain available for compatibility
with scripts that use an explicit host and VMID.

`vm stats` reports the provider's live CPU and memory use, allocated/used disk
counters, cumulative disk and network I/O, and uptime without requiring a
guest agent. Text output calls out CPU at 90%, memory at 85%, any reported swap
use, and disk usage at 90%; JSON includes the same messages in `warnings`.
Counters for a stopped guest may be zero, and a single sample is a diagnostic
starting point rather than a capacity trend.

`vm autostart` without a mode is read-only. Use `--enable` or `--disable` to
change start-at-boot behavior. With `--enable`, `--order` sets priority (lower
starts first and stops last), `--start-delay` staggers later guests, and
`--shutdown-timeout` gives the guest time for a clean shutdown. Unspecified
ordering values are preserved. Staggering storage, database, and application
VMs avoids having every guest saturate an older host's disks immediately after
a power outage.

`vm destroy` prints the observed name, VMID, provider host, and local name
before asking the operator to type `yes`; `--yes` skips only that prompt and
`--force` uses an immediate stop rather than graceful shutdown. A successful
destroy is verified with a fresh provider inventory. The saved setup is
retained so it remains available as a reconstruction/reprovisioning
declaration; remove it separately with `basaltw rm NAME` when it is no
longer wanted. Other Proxmox-specific guest mutations and host-administration
operations remain under `basaltw proxmox ...` until their command paths
are migrated.

`proxmox delsnapshot` also asks for the exact response `yes` before deleting a
snapshot; `-y`/`--yes` skips that prompt for automation. Its `--dry-run` option
prints the command without prompting or deleting. The interactive Proxmox
shell applies the same confirmation rule.

`rolling-update` uses saved setup commands and workspace credentials. It audits
all targets before making changes, audits each node again after its update and
reboot, and stops before an automatic reboot if that node still has running or
locked guests. After inspecting saved checkpoints, `--resume` continues the
same ordered targets with unchanged setup, skips completed nodes, retries known
failed patches, and refuses uncertain in-progress outcomes. See [Proxmox
operations](../operations/proxmox.md) for manual recovery. Mutating subcommands
accept `--dry-run` where supported; a rolling update dry run still performs the
read-only preflight audits.

### Interactive Shell

`basaltw shell` opens a REPL for saved configurations. The shell loads
`~/.basaltwaterrc` on startup and persists history at
`~/.local/share/basaltwater/shell_history`.

Useful shell commands:

- `list`, `info`, `cmd`, `deploy`, `rm`, `recall`, `reconstruct`
- `new` / `setup` for a guided saved-setup flow
- `workspace` to change the active workspace
- `proxmox` to enter the Proxmox sub-shell

### Sysadmin Shortcuts

See [`sysadmin.md`](../operations/sysadmin.md) for the host shortcut commands (`mount`,
`health`, `ssh`, `push`, `pull`, `df`, `fan`, `svc`, `logs`, `upgrade`,
`reachable`, `key`).

For `ssh` and `fan`, connection options may appear before or after hosts
(and between `fan` hosts), before the first `--`. The delimiter separates
remote argv: all following tokens, including flags and later literal `--`,
are passed to the remote command. Unknown connection options are rejected.
`fan` requires the delimiter and a command. `ssh` without a command opens an
interactive session; an explicit delimiter with no command is an error.
SSH also accepts a direct command without the delimiter, such as
`basaltw ssh myserver -u root id -u`; the first command word ends connection
option parsing. Prefer the explicit delimiter for scripts.

Interactive remote setup confirms a new host key through OpenSSH's normal
fingerprint prompt, saves it in the workspace, and continues without a separate
enrollment command. Changed keys remain rejected. Ordinary setup dry runs do not
connect or save trust. Selected-builder dry runs require existing trust and use
read-only SSH probes as described above. See
[first connection during setup](../operations/ssh.md#first-connection-during-setup).

For unattended setup or administration commands before the first setup,
enroll the key and verify the displayed fingerprint independently:

```bash
basaltw ssh-key enroll example.com
```

For automation, pass `--fingerprint SHA256:YOUR_VERIFIED_FINGERPRINT` to enroll
without a prompt. Use the same `--workspace` as the subsequent commands.
Other SSH commands use strict checking against the workspace `known_hosts`
file and refuse hosts that have not been enrolled by setup or explicitly.

## Testing and bootstrap

The [installation guide](../getting-started/installation.md) covers supported
installation and local control-plane commands. Use the following when working
from a checkout or refreshing completion manually. Completion setup requires
`basaltw` and `register-python-argcomplete` on `PATH`; see the
[shell-completion reference](shell-completion.md) if either command is missing.

```bash
python3 -m unittest discover -s tests
./run_tests.py --suite smoke
uv tool install --upgrade argcomplete
basaltw completions --shell bash
```

The full test matrix and detailed bootstrap behavior are covered by
[`operations.md`](../operations/operations.md).

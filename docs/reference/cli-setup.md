# Setup commands and common flags

[Documentation index](command-line.md)

## Setup at a glance

### System Types

| Type | Description |
|------|-------------|
| `control_plane` | Debian infrastructure control plane with administrator tools |
| `agent_vm` | Headless agent coding VM; GitHub CLI default, explicit coding provider required |
| `agent_workstation` | Graphical agent coding workstation; adds Firefox ESR |
| `agent_code_vm` | Full graphical agent VM with T3 Code and Geany; Playwright is opt-in |
| `agent_cachyos` | Coding tools for an existing CachyOS KDE workstation; supports local setup and remote-controller setup over SSH |
| `workstation_desktop` | Desktop workstation with GUI |
| `workstation_dev` | Developer workstation |
| `pc_dev` | PC development environment |
| `server_dev` | Development server |
| `server_web` | Web server |
| `server_github_runner` | Dedicated Debian 13 x86-64 GitHub Actions runner for a private repository or organization |
| `server_lite` | Lightweight server |
| `server_wsl` | Local Ubuntu tools inside Windows 11 WSL 2; see [Windows WSL](../workstations/windows-wsl.md) |
| `server_proxmox` | Proxmox host server |
| `custom_steps` | Run an explicitly selected step list for advanced or development workflows |

`agent_cachyos` runs `pacman -Syu` on the target before installing selected
tools. This applies to setup reruns and local refresh. `--dry-run` previews the
step. See [CachyOS package behavior](../workstations/cachyos-profile-reference.md#package-and-repository-behavior).

### Core Flags

| Flag | Description |
|------|-------------|
| `host` | Hostname or IP address; with `--provision-on`, use IPv4 or IPv4/PREFIX (bare IPv4 means `/24`) |
| `username` | Optional SSH username |
| `-k, --key PATH` | SSH private key |
| `-p, --password PASS` | Target Unix account password, also used for RDP when enabled. A command-line value may appear in shell history and process arguments. |
| `--nopasswd` / `--no-nopasswd` | Retain or remove the VM setup user's unrestricted passwordless sudo capability rule; disabled by default |
| `--privilege-broker` / `--no-privilege-broker` | Re-enable or disable the approval engine included by default with the web panel; disabling removes approval listeners and retains audit history |
| `--approval-panel [PORT]` / `--no-approval-panel` | Install or remove the standalone HTTPS approval panel (default port 9444), independently of main-panel approvals; requires a non-root operator on an agent or GitHub runner VM and conflicts with `--nopasswd` and hardened modes |
| `--approval-panel-password [PASSWORD]` | Set the standalone panel's independent password; omit `PASSWORD` to prompt securely, and omit the flag on a patch to retain the existing password. Legacy alias: `--privilege-broker-password`. A command-line value may appear in shell history and process arguments. |
| `--harden-agent` / `--no-harden-agent` | Apply or remove administrator/root-equivalent group restrictions and the hardened coding-agent policy; mutually exclusive with `--nopasswd` when enabled |
| `--harden-user` / `--no-harden-user` | Also apply or remove password locking, mode-`0700` home, sensitive system-data/device-group restrictions, SSH forwarding/user-rc restrictions, and disabled systemd lingering; enabling implies `--harden-agent` and rejects RDP |
| `-t, --timezone TZ` | Timezone |
| `--hostname NAME` | Set the target system hostname; distinct from the saved `--name` label |
| `--mdns` / `--no-mdns` | Enable or disable Avahi/mDNS advertisement of the target hostname as `NAME.local` |
| `--ip ADDRESS/PREFIX` | Stage a persistent static IPv4 address; CIDR prefix is required |
| `--ipv6 ADDRESS/PREFIX` | Stage a persistent static IPv6 address; CIDR prefix is required |
| `--gateway IP` | IPv4 default gateway; requires `--ip` or an IPv4 `--provision-on` target |
| `--gateway6 IP` | IPv6 default gateway; requires `--ipv6` |
| `--dns IP` | DNS server; repeatable and accepts IPv4 or IPv6 addresses |
| `--network-interface NAME` | Interface to configure; defaults to the interface carrying the default route |
| `--headscale` / `--no-headscale` | Manage native Headscale hosting on supported Debian profiles; disable preserves data |
| `--headscale-name NAME` | Register and bind the managed Headscale server during hosted setup |
| `--headscale-domain HOSTNAME` | Hosted public control hostname |
| `--headscale-base-domain HOSTNAME` | MagicDNS suffix, separate from public hostnames |
| `--headscale-onboarding-domain HOSTNAME` | Separate static HTTPS device instruction hostname |
| `--headscale-onboarding-control-url HTTPS_ORIGIN` | Non-secret external control endpoint for static onboarding; does not select a local client; requires an onboarding domain |
| `--headscale-public-ipv4 IP`, `--headscale-public-ipv6 IP` | Public relay addresses for hosted Headscale |
| `--headscale-resolver IP` | Candidate overlay resolver metadata; repeatable, does not publish DNS |
| `--tailscale NAME` | Select a saved Headscale reference for local native client setup |
| `--tailscale-tag TAG` | Authorize private automatic enrollment; repeatable for named client setup |
| `--tailscale-login-server HTTPS_ORIGIN` | Explicit local native client selection |
| `--no-tailscale` | Disable managed client setup while retaining device identity |
| `--unbound` / `--no-unbound` | Manage Unbound on a separate explicitly enrolled Debian DNS node |
| `--activate-network` | Safely make requested addresses live, verify SSH on each address, then persist the configuration |
| `--workspace PATH` | Workspace root for config, credentials, known_hosts, and history |
| `--machine TYPE` | Machine type override; defaults to `auto` on the target |
| `--proxmox-balloon-target PERCENT` | Override the automatic `server_proxmox` node balloon target (1-95) |
| `--control-plane` | Add the common administrator/Linux tool bundle to any profile |
| `--name NAME` | Friendly name for the configuration |
| `--tags TAG1,TAG2` | Comma-separated tags |
| `--image SOURCE` | VM HTTPS qcow2 URL or `STORAGE:import/FILE` / `STORAGE:iso/FILE` reference; used with `--machine vm` |
| `--image-storage STORAGE` | Storage for downloaded VM images; prefers `import`, then falls back to `iso` content |
| `--verify-provider` | For `setup` with `--provision-on`, verify the cached guest against Proxmox and reconcile supported provider-side settings |
| `--image-sha512 HEX` | Required 128-character SHA-512 for a custom HTTPS VM image URL |
| `--steps STEP...` | Run an explicit space-separated step list with `custom_steps` |
| `--dry-run` | Validate the request and preview the setup handoff without applying target setup |
| `--auto-restart` / `--no-auto-restart` | Control normal automatic restarts |
| `--auto-restart-force-days N` | Force restart after N days of deferrals |
| `--auto-restart-grace N` | Warning period before an automatic restart |
| `--restart-if-needed` | After successful setup, restart for `/run/reboot-required` or pending Proxmox VM configuration |
| `--wait-for-restart` | With `--restart-if-needed` on a remote target, wait for a new boot and run `basaltw health` |
| `--shutdown` | After successful setup, gracefully shut down a provisioned Proxmox VM; takes precedence over restart options |

### Maintain offline VMs

Setup automatically starts an existing stopped Proxmox VM or resumes a paused
one, then waits up to five minutes for SSH before continuing. A running VM
whose SSH is still starting gets the same wait. This also applies to reruns
that reuse saved provisioning metadata. Setup verifies the VM's identity
before changing its power state and checks node memory capacity before
starting a stopped VM. Provider errors or an SSH timeout stop setup.

Add `--shutdown` to leave the VM offline after a successful setup:

```bash
basaltw setup server_lite 10.0.0.50 admin --provision-on pve1 --shutdown
```

This example reuses an already provisioned VM's saved configuration. For a new
VM, supply the usual memory, storage, and other provisioning options.
`--shutdown` requires a provisioned QEMU VM (`--provision-on`, `--machine vm`).
It is a one-time controller action and is not saved for future setup runs.
Failed setup leaves the VM running for inspection. A dry run previews power
operations without contacting the provider.

Shutdown uses Proxmox's graceful shutdown with a 180-second timeout and verifies
that the VM stopped. It never forces the VM off; a failed shutdown returns
nonzero. `--shutdown` takes precedence over both `--restart-if-needed` and
`--wait-for-restart`: setup shuts down directly, including when updates or
provider configuration changes would otherwise require a restart. The next
start applies those changes.

### Restart after setup

`--restart-if-needed` checks the target after a successful setup and requests a
restart when `/run/reboot-required` is present or a VM with `--provision-on`
has pending Proxmox configuration changes, including RAM or vCPU changes.
The VM's expected Proxmox name and configured setup address must match uniquely.
Pending replacements and removals trigger a provider-side `qm reboot`, which
gracefully shuts down and starts the VM to apply the changes. This applies all
queued changes, including changes made through the Proxmox GUI. Provider SSH
uses `--provision-user` and `--provision-key`; an inspection or restart failure
returns nonzero without falling back to an in-guest reboot. The shutdown has a
180-second timeout and never forces the VM off. Without `--wait-for-restart`,
the command waits for the provider operation but skips guest SSH and health
verification. VMs without `--provision-on` retain the reboot-marker check.
It does not treat a newer installed kernel without that marker as sufficient
evidence to reboot. The flag is a one-time action: it may interrupt active
sessions and work. On Proxmox hosts,
it authorizes graceful guest shutdown in the node's configured shutdown order
before rebooting. Health failures, active tasks, and locked guests block the
request. Shutdown never forces guests off; any guest still running afterward
blocks the host reboot. HA-managed guests require operator-managed maintenance.
`--dry-run` never requests a restart.

By default, setup requests the restart and exits without waiting for the host
to return. Add `--wait-for-restart` to wait up to five minutes for a remote
target to boot again over SSH, then run the standard `basaltw health` summary.
On Proxmox, the health check reuses root authentication with the setup SSH key
and connection. The Proxmox profile preserves existing accounts and does not
provision the named setup user. `basaltw health HOST` also defaults to root
when the saved profile is `server_proxmox`; `-u USER` overrides that default.
This flag requires `--restart-if-needed` and a remote target; if no reboot is
needed, setup skips both the wait and health check. Local targets can use
`--restart-if-needed`, but cannot use `--wait-for-restart` because the
controller would be rebooting with the target.

Proxmox allows up to 30 minutes for guest shutdown plus five minutes for the
host to return. Its shutdown and restart job runs on the node under the
maintenance lock, so it can finish even if the controller is a guest being
stopped. The job repeats the maintenance checks before shutdown and reboot.
The synchronous Proxmox shutdown command and its task verification share the
30-minute shutdown budget; CLI progress output is separate from its final API
result. Setup prints the exact restart service name. With `--wait-for-restart`,
it checks that service while the original boot is reachable and reports a
failed job promptly, including its journal command, instead of waiting for the
full restart timeout. A queued job is not proof that the host rebooted.
Without the wait flag, inspect failures on the node with
`sudo journalctl -u 'basaltwater-setup-reboot-*' --no-pager`. A failed shutdown
can leave some guests stopped. After reboot, Proxmox's existing autostart and
HA policies determine which guests start.

For this explicit restart only, inactive storage produces a warning rather
than blocking the restart, regardless of its backend or configured content.
The restart flag authorizes the outage for local storage, stale entries, and
shares with mixed content or guest disks. A reboot may help restore storage;
it can also stop a container serving shares on the node. The same policy
applies before shutdown, after shutdown, and during the post-reboot check,
even if every enabled pool is inactive. Dependent guests and jobs may remain
unavailable until storage returns; setup does not start guests or change their
autostart settings. Failed storage probes or invalid API responses, active
tasks, locks, quorum, and other health gates still block the restart.
Normal audits and scheduled reboot checks retain their strict storage checks.
Scheduled package updates and kernel-only cleanup log inactive guest storage
as a warning; their other maintenance checks still block unsafe work.

```bash
basaltw setup server_web example.com admin --restart-if-needed
basaltw setup server_web example.com admin \
  --restart-if-needed --wait-for-restart
```

The recurring `--auto-restart` policy is separate. Its scheduled restart
service defers for active sessions and recognized work; the one-time setup
flag explicitly requests a restart as soon as setup finishes.

## Choose a topic

<a id="common-setup-flags"></a>
- [Common machine configuration flags](cli-setup-common.md) Setup options and saved-host declarations.

<a id="development-flags"></a>
<a id="cachyos-ssh-flags"></a>
- [Development and CachyOS SSH flags](cli-setup-development.md) Development runtimes and workstation access.

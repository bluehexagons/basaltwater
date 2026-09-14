# Command-Line Reference

Reference for the upcoming stable `v2.0.0` `infra-tools` CLI. The code help and
`lib/arg_parser.py` are the source of truth; this page summarizes the command
surface and behaviors that are easy to miss.

Related pages:

- [`SYSADMIN.md`](./SYSADMIN.md) for remote host shortcuts
- [`SSH.md`](./SSH.md) for passphrase-protected keys and SSH-agent setup
- [`NETWORKING.md`](./NETWORKING.md) for workspace network inventory
- [`CICD.md`](./CICD.md) for webhook CI/CD setup
- [`WORKSTATIONS.md`](./WORKSTATIONS.md) for desktop profiles and application choices
- [`GODOT.md`](./GODOT.md) for graphical/headless Godot installation and updates
- [`SYNCTHING.md`](./SYNCTHING.md) for private peer and folder synchronization
- [`MACHINE_TYPES.md`](./MACHINE_TYPES.md) for machine type behavior
- [`CREDENTIALS.md`](./CREDENTIALS.md) for the credential workflow map
- [`AGENT_AUTHENTICATION.md`](./AGENT_AUTHENTICATION.md) for agent auth
  sources, rotation, recovery, and portability
- [`GIT_ACCESS.md`](./GIT_ACCESS.md) for Git policy and HTTPS authentication
- [`AGENT_SKILLS.md`](./AGENT_SKILLS.md) for managed Codex/OpenCode workflow
  skills and capability routing
- [`AGENT_SECURITY.md`](./AGENT_SECURITY.md) for coding-user privilege,
  Codex session policy, hardened mode, and accepted security boundaries
- [`README.md`](./README.md) for the full documentation map

## Commands

```text
infra-tools --version
infra-tools setup <system_type> <host> [username] [options]
infra-tools patch <host> [username] [options]
infra-tools shares <host> [username] [options]
infra-tools recall <host> [username] [options]
infra-tools reconstruct [--compact]
infra-tools list [pattern] [--json]
infra-tools info [pattern] [--compact]
infra-tools cmd [pattern]
infra-tools rm <pattern>
infra-tools cleanup [host] [options]
infra-tools deploy <pattern> [--yes]
infra-tools credentials set <username> [password]
infra-tools credentials list
infra-tools credentials remove <username>
infra-tools completions [options]
infra-tools python-tools [options]
infra-tools bootstrap [options]
infra-tools self-setup [options]
infra-tools local [subcommand]
infra-tools desktop <command> ...
infra-tools firmware <audit|update> [options]
infra-tools channel [CHANNEL]
infra-tools upgrade
infra-tools user rename <host> <new_username> [options]
infra-tools agent doctor [HOST USER] [options]
infra-tools agent update [HOST USER] [options]
infra-tools agent auth set HOST USER --tool TOOL --file PATH
infra-tools agent auth status HOST USER [--tool TOOL]
infra-tools agent auth pull HOST USER [--output-dir PATH] [--tool TOOL]
infra-tools agent web pair HOST USER [-k PATH]
infra-tools agent workspace <create|list|status|remove> ...
infra-tools agent maintenance <hold|status|release> [HOST USER] [options]
infra-tools agent support-bundle [--output PATH] [--browser-smoke]
infra-tools gogs health HOST [--json] [--min-free-bytes N] [--min-free-inodes N]
infra-tools gogs repo-configure [REPOSITORY] --github-url URL --gogs-url URL [options]
infra-tools homebox health HOST [--json]
infra-tools homebox backup HOST /absolute/target/archive.tar.gz [--dry-run]
infra-tools homebox restore HOST /absolute/target/archive.tar.gz --yes [--dry-run]
infra-tools cicd connect BUILD APP [options]
infra-tools cicd status BUILD [--json]
infra-tools cicd test BUILD TARGET
infra-tools maintenance github [--root PATH] <audit|prune> [options]
infra-tools shell
infra-tools network ...
infra-tools proxmox ...
infra-tools ssh-key enroll <host> [--port PORT] [--yes]
```

`infra-tools --version` prints one stable line containing the installed project
version, suitable for feedback and support records.

For the desktop command catalog, including accessibility, window operations,
waits, and live smoke checks, see [Desktop automation](DESKTOP_AUTOMATION.md).

Use `infra-tools agent doctor --capability t3code` to check the managed T3
service, native runtime, provider authentication, Git identity, pairing helper,
endpoint, and agent skill. Add `--fix` to rebuild missing native dependencies,
configure the GitHub HTTPS credential helper after a successful login, enable
the service at boot, and restart it when inactive.

### Bootstrap and self-setup flags

`bootstrap` and its `self-setup` alias prepare the local orchestration host.
On an unsupported host, bootstrap asks for `[y/N]` confirmation before any
local system-package operation. Use `--skip-system-packages` after manually
installing the controller prerequisites when the host uses a non-APT package
manager.
When that host is a Proxmox VM, install and activate the guest agent with:

```bash
sudo infra-tools self-setup --qemu-guest-agent
```

| Flag | Description |
|------|-------------|
| `--qemu-guest-agent` | Install `qemu-guest-agent`, then start and enable its systemd service; requires root and cannot be combined with `--skip-system-packages` |

The same option is available on `install.sh`; place it before
`--setup` or `--local-setup` so the installer forwards it to its internal
self-setup step. It is intended for a Debian VM running the orchestration
tools, not for an LXC container.

## Setup at a glance

### System Types

| Type | Description |
|------|-------------|
| `control_plane` | Debian infrastructure control plane with administrator tools |
| `agent_vm` | Headless agent coding VM; defaults to GitHub CLI and Codex |
| `agent_workstation` | Graphical agent coding workstation; adds Firefox ESR |
| `agent_code_vm` | Full graphical agent VM with T3 Code and Geany; Playwright is opt-in |
| `workstation_desktop` | Desktop workstation with GUI |
| `workstation_dev` | Developer workstation |
| `pc_dev` | PC development environment |
| `server_dev` | Development server |
| `server_web` | Web server |
| `server_lite` | Lightweight server |
| `server_proxmox` | Proxmox host server |
| `custom_steps` | Run an explicitly selected step list for advanced or development workflows |

### Core Flags

| Flag | Description |
|------|-------------|
| `host` | Hostname or IP address; with `--provision-on`, use IPv4 or IPv4/PREFIX (bare IPv4 means `/24`) |
| `username` | Optional SSH username |
| `-k, --key PATH` | SSH private key |
| `-p, --password PASS` | SSH password |
| `--nopasswd` / `--no-nopasswd` | Retain or remove the VM setup user's unrestricted passwordless sudo capability rule; disabled by default |
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

### Common Setup Flags

| Flag | Description |
|------|-------------|
| `--lan-access` / `--no-lan-access` | Infer or remove target-adjacent LAN access: a private IPv4 target uses its `/24`, a ULA IPv6 target uses `/64`, and an explicit static prefix is honored |
| `--access-source IP_OR_CIDR [IP_OR_CIDR ...]` | Restrict managed inbound services to one or more sources; accepts multiple values and may be repeated |
| `--no-access-source` | Clear saved custom generic access sources; use `--no-lan-access` separately to remove inferred LAN access |
| `--rdp` / `--no-rdp` | Enable or disable remote RDP ingress; desktop profiles retain loopback XRDP |
| `--rdp-existing-password` | Local setup only: reuse an existing non-root desktop account password; a missing profile password is requested securely |
| `--rdp-bind-address IP` | Bind XRDP to one local IP; defaults to all IPv4 interfaces (`0.0.0.0`) |
| `--rdp-source IP_OR_CIDR` | Restrict UFW RDP ingress to a source; repeatable; `--no-rdp-source` clears profile sources |
| `--rdp-clipboard` / `--no-rdp-clipboard` | Control clipboard redirection; enabled by default |
| `--rdp-drive-redirection` / `--no-rdp-drive-redirection` | Control drive, printer, and device redirection; disabled by default |
| `--rdp-audio` / `--no-rdp-audio` | Control audio redirection; disabled by default |
| `--rdp-idle-timeout SECONDS` | Disconnect an idle session after this interval; 0 disables |
| `--desktop [xfce\|i3\|cinnamon\|lxqt]` | Configure one shared XRDP desktop; explicitly enables desktop capability on headless profiles |
| `--browser NAME` | Browser to install |
| `--editor [geany\|vscode]` | Install an explicit graphical editor; requires a desktop-capable setup or `--rdp` |
| `--flatpak` | Install desktop apps via Flatpak |
| `--office` | Install LibreOffice |
| `--apt-install PACKAGE` | Install a package via apt |
| `--flatpak-install PACKAGE` | Install a package via Flatpak |
| `--dark` | Configure dark theme |

Static address configuration is validated before any remote work begins and
supports NetworkManager, systemd-networkd, and ifupdown on Debian. Without
`--activate-network`, direct-host configuration is persisted but not activated
live; reboot or deliberately restart the interface after reviewing it.

For `--provision-on`, the IPv4 gateway and DNS default to values discovered
from the selected Proxmox bridge and node. Those values are saved with the
setup and restored on reruns. If a legacy setup cache is missing them,
infra-tools refreshes the Proxmox defaults instead of skipping discovery. Once
guest SSH is available, setup also verifies the live IPv4 default route and
repairs a missing route before package installation; the normal final network
step persists the repaired configuration. A provisioned VM connects for this
handoff through retained, key-only root SSH. VMs and LXCs use the same stable
privileged channel for route repair and streamed setup staging, so setup does
not depend on the configured account's current sudo policy. A standard VM user
joins `sudo` without receiving a temporary passwordless bootstrap rule.
`--nopasswd` deliberately grants that user unrestricted non-interactive sudo;
root transport lets a later rerun add or remove the rule safely. Patch commands
preserve an omitted passwordless-sudo choice;
`--no-nopasswd` removes it explicitly. Selecting `--nopasswd` exits a saved
hardened posture, while explicitly combining it with a hardening flag is
rejected. When setup is launched from a terminal, SSH may prompt
for the configured private-key passphrase; piped or otherwise non-interactive
runs require the key to be loaded in an SSH agent. See
[SSH authentication](SSH.md) for the same behavior across transfers,
Proxmox operations, maintenance, and agent commands.

`--verify-provider` is a setup-only check and requires `--provision-on`. Use it
when a cached provisioned guest should be compared with Proxmox even though
the saved declaration has not changed; supported provider-side drift, such as
the guest vCPU count, is reconciled and verified. Managed disks are also
verified against the cached pool and minimum-size declaration. All provider
reconciliation driven by saved VM metadata is existing-only: if the saved VM
cannot be found, setup stops instead of silently creating a replacement.

After moving an infra-tools-provisioned QEMU VM with the Proxmox GUI, rerun its
saved setup command with the destination in `--provision-on`. A changed
provider host is never replaced with the cached source: infra-tools verifies
that any matching source VM is stopped, requires the destination VM to match
the saved name, IPv4 address, and managed-disk identities, then updates the
saved provider binding only after remote setup succeeds. A missing destination
is rejected rather than provisioned as a replacement. The destination bridge,
storage pools, and minimum disk sizes are verified on every rebind before any
provider-side settings are reconciled; a verified stopped destination is
started and allowed time for SSH to return. Supply the moved VM's actual
`--bridge` when it changed. Storage pool names may also be updated with
`--storage` when the logical disk names are unchanged. A size change is accepted
only when the live disk is already at least that large; setup verifies and
adopts the declaration but does not resize the disk. A different disk set
during a provider rebind remains rejected. On the same saved provider VM,
setup can add new named disks that each have a new `--storage-mount`; it still
rejects removals, replacements, and changes to saved disk or mount identities.

A GUI clone that replaces the original can use the same rebind path only while
the saved source VM is stopped. A clone intended to coexist with its source
must first receive a unique IPv4 address and Proxmox/system hostname, and must
be saved as a separate setup identity; do not run two copies with the same
network or credential identity.

`--activate-network` uses a retry-safe transaction for an existing host and
must be run from a separate controller. The remote
setup temporarily adds the requested addresses without removing the address
carrying its SSH connection. After that setup process exits, the controller
requires every requested endpoint to return a unique transaction identity,
persists the new backend configuration through the verified address, and
verifies both the old and new endpoints again. The transaction snapshots the
affected backend settings and restores them together with temporary addresses
and routes if a later check fails. Commit/finalize retries are idempotent, so a
lost SSH response does not leave an ambiguous result. On success, the
saved setup moves to the new IPv4 address (or IPv6 when no IPv4 was requested).
The one-shot activation flag is not retained in the saved setup.
The old address remains live only until reboot; it is absent from the new
persistent configuration. Existing ifupdown files changed for the selected
interface receive a one-time `.infra-tools.bak` copy.

```bash
infra-tools setup server_lite 192.168.1.50 admin \
  --hostname app-01 \
  --ip 192.168.1.50/24 --gateway 192.168.1.1 \
  --dns 1.1.1.1 --dns 1.0.0.1 \
  --network-interface eth0

# Reassign an existing saved host without interrupting the setup SSH session
infra-tools patch 192.168.1.50 admin \
  --ip 192.168.1.60/24 --gateway 192.168.1.1 \
  --dns 1.1.1.1 --network-interface eth0 --activate-network
```

`--hostname` and these static network flags are intentionally rejected for
`server_proxmox`: changing a Proxmox node name or bridge address can affect
cluster identity and requires a node-specific migration plan. The runtime also
refuses a generic change when the selected interface is a Linux bridge, even
if the host was given another setup type. Existing Proxmox VM and LXC guests
can use the same `patch ... --activate-network` handoff as other Debian hosts.
The handoff requires a complete guest-conflict scan, refuses concurrent
metadata changes, updates and reads back `qm ipconfig0` or `pct net0` while
preserving the guest's other network fields, and verifies both SSH endpoints
before guest persistence. Newly provisioned guests receive their initial
address from cloud-init or `pct`, so initial provisioned setup rejects
`--activate-network`.

Use `--lan-access` when every managed administrative service should be private:

```bash
infra-tools setup workstation_dev 192.168.0.25 agent \
  --lan-access --rdp
```

The preset is derived from the target address instead of trusting every RFC
1918 and ULA range. This covers typical home and lab `/24` networks across the
10/8, 172.16/12, and 192.168/16 blocks, plus ULA `/64` networks. An explicit
`--ip` or `--ipv6` prefix is honored without expanding beyond its enclosing
private-address block. If the target hostname does not resolve to a private
address, setup stops; remove `--lan-access` and use `--access-source` for routed
management networks, VLANs, VPNs, or intentionally wider subnets.

For a narrower or mixed policy, one flag accepts several sources:

```bash
--access-source 192.168.0.0/24 10.0.0.0/8 100.64.0.0/10
```

Generic sources apply to managed SSH, RDP, TCP web ports, T3 Code web and
pairing endpoints, direct Gogs, and Samba ingress. On `server_proxmox`, they
populate Proxmox's standard `management` IP set, which covers the web GUI,
SSH, VNC, and SPICE. Service-specific `--rdp-source`,
`--web-interface-source`, and `--gogs-source` values remain available and are
added to the generic set for that service. `--samba-source` provides the same
service-only addition for SMB ingress. Cloudflare tunnels and intentionally
public Antistatic endpoints retain their own exposure policy.

Without a generic source or `--rdp-source`, enabling RDP keeps a globally
rate-limited UFW rule. On rerun, infra-tools installs requested source rules
before removing broad rules and reconciles only its own comment-tagged rules.
The single desktop is retained through disconnect until logout. Session-count
and disconnect-cleanup flags are removed; saved legacy policies are migrated
with warnings for nondefault behavior. See [XRDP](XRDP.md) for runtime commands,
human takeover, console conversion, and rollback.

Setup `--dry-run` validates the requested configuration and prints the planned
upload or local-copy handoff without applying target setup. It does not run the
target-side step list or prove that installation will succeed. Deployment and
provisioning options can still perform preflight work, such as fetching a
repository; use a simple profile when exploring without those dependencies.
The separate target-side `remote_setup.py --dry-run` prints the selected step
list. Neither preview is a completed live setup.

### Development Flags

| Flag | Description |
|------|-------------|
| `--node` | Install nvm + Node.js + PNPM |
| `--go` | Install Go |
| `--python` | Install Python aliases + uv |
| `--data-analysis` | Install the larger Python analysis bundle: NumPy, pandas, SciPy, Matplotlib, JupyterLab, and csvkit; also enables `--python` |
| `--av-tools` | Install ImageMagick, FFmpeg (including ffprobe), and ExifTool for image, audio, video, and metadata processing |
| `--gl-tools` | Install the minimal OpenGL inspection and debugging bundle: Mesa utilities and apitrace |
| `--godot` | Install the newest stable verified Godot Engine release for graphical or headless use |
| `--godot-bundle BUNDLE` | Add `web` or `publishing`; repeatable and automatically enables `--godot` |

Selecting a managed runtime also installs its update timer. Godot is fetched
from the official release channel rather than Debian's package version and is
placed on the system `PATH` as `godot` and `godot4`, including for agent users.
The `web` bundle adds verified, version-matched web export templates for the
configured account. The `publishing` bundle adds Butler and, on x86_64,
user-owned SteamCMD; publishing credentials remain user-managed and are never
copied by setup. Planned `dotnet`, `android`, `gdextension`, and `assets`
bundles remain roadmap items and are not accepted yet.
See [`GODOT.md`](./GODOT.md) for the artifact and headless-use contract, and
[`MAINTENANCE.md`](./MAINTENANCE.md) for schedules and update policy.

### Agent host flags

These flags prepare a Debian VM or local control plane for agentic coding. They
work with any setup type. `agent_vm` is the recommended terminal-only profile,
`agent_workstation` adds a desktop and Firefox ESR, and `agent_code_vm` adds
the common T3 Code web service, Geany, RDP, private source ranges, protected T3
pairing, read-write Git, and active auth sources. Playwright remains an explicit
fallback through `--browser-automation playwright` for SSH-only or standalone
Codex and OpenCode sessions.
All three default to GitHub CLI and Codex. `--agent-tool` values add to those
defaults and accept comma-separated lists; use `--no-agent-tool` to remove a
default. The full profile does not choose Proxmox capacity. It installs Node.js
automatically for T3 Code, while Go and other project runtimes remain optional.
The coding identity remains in the `sudo` group by default, but its sudo use
requires the account password. `--nopasswd` restores the former VM-wide
`NOPASSWD:ALL` policy for convenience, while leaving Codex's capable standard
feature set available. `--harden-agent` instead removes administrator and
root-equivalent supplementary groups. `--harden-user` adds
a private home, password lock, sensitive system-data/device-group removal,
disabled SSH forwarding and user rc, and disabled systemd lingering, making it
suitable for headless CI/CD and disposable evaluation; it cannot be combined
with RDP. Codex sessions use auto-reviewed workspace permissions by default;
standard sessions still allow an explicit user or client selection of full
access and YOLO-like modes. Hardened sessions stay in the workspace boundary
with no approval escalation and also disable live web search, common
credential-path reads, apps/plugins, MCP servers, native browser/computer use,
remote control, and unmanaged hooks. See [Agentic coding
security](AGENT_SECURITY.md) for the exact boundaries and supply-chain
guidance.
The shared CLI baseline includes small coding and inspection tools such as
`ripgrep` (`rg`), `jq`, SQLite, `file`, `tree`, `make`, and `patch`. The
substantially larger Python analysis/notebook stack remains opt-in through
`--data-analysis`. Image, audio, and video processing tools remain opt-in
through `--av-tools`; OpenGL inspection and API tracing remain opt-in through
`--gl-tools`.
Missing account and pairing passwords are requested securely from a terminal.
`server_lite` omits the standard firewall and generic CLI bundle, so use it
only when that lighter profile is intentional.

The agent setup model separates explicit tool installation, VM-level Git
policy, authentication payloads, and non-secret agent configuration. See
[agent authentication](AGENT_AUTHENTICATION.md) and [Git
access](GIT_ACCESS.md) for those workflows.
See [`BROWSER_AUTOMATION.md`](./BROWSER_AUTOMATION.md) for the separate
Playwright runtime, MCP registration, and browser security model.

For a headless T3 Code VM without selecting a broader profile, use
`--t3code-ready`. It adds GitHub CLI and Codex, read-write Git, the T3 web
service, and protected pairing. Credentials remain opt-in through the normal
`--git-auth` and `--agent-auth` options.

```bash
infra-tools setup agent_vm 10.0.0.10 agentuser \
  --agent-config active \
  --git-access read --repo https://github.com/user/my_codebase.git
```

For a provisioned graphical coding VM, use the full profile while keeping
capacity and project runtimes explicit:

```bash
infra-tools setup agent_code_vm 10.0.0.11 agentuser \
  --provision-on pve1 --name agent-1 \
  --memory 4G --cores 4 --storage root local-lvm 32G \
  --agent-tool opencode --lan-access
```

The profile supplies T3 Code, Geany, RDP, read-write Git, active auth sources,
and T3 pairing. Access sources are deliberately explicit; the
example uses `--lan-access`, while a narrower deployment can use
`--access-source` or the service-specific source flags. Passwords omitted from
the command are requested with hidden prompts;
an empty pairing password reuses the target account password. Use
`--git-access none --git-auth none --agent-auth none` or the `--no-*` switches
when a deployment needs a narrower posture.

For the local machine, the installer can select the control-plane profile and
run it immediately:

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sudo sh "$HOME/.infra_tools-install.sh" --user "$USER" --local-setup control_plane
rm -f "$HOME/.infra_tools-install.sh"
```

To convert a Debian desktop to one shared XFCE/XRDP session, first log out
graphical sessions and run from SSH or a text console. Console graphical login
is disabled. Select the agent tools needed (GitHub CLI and Codex here):

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sudo sh "$HOME/.infra_tools-install.sh" --user "$USER" --local-setup workstation_dev \
  --control-plane --agent-tool gh --agent-tool codex --desktop xfce --rdp --rdp-existing-password
rm -f "$HOME/.infra_tools-install.sh"
```

| Flag | Description |
|------|-------------|
| `--t3code-ready` | Add the headless T3 Code-ready profile: GitHub CLI, Codex, read-write Git, T3 web service, and protected pairing |
| `--nopasswd` / `--no-nopasswd` | Retain or remove unrestricted passwordless sudo for the VM setup identity; capability opt-in |
| `--harden-agent` / `--no-harden-agent` | Apply or remove administrator/root-equivalent supplementary-group restrictions and the stricter agent policy |
| `--harden-user` / `--no-harden-user` | Apply or remove the password lock, private home, sensitive system-data/device-group restrictions, SSH forwarding/user-rc restrictions, and disabled lingering; enabling implies agent hardening and is incompatible with RDP |
| `--agent-tool TOOL[,TOOL...]` | Add one or more provider tools (`gh`, `codex`, `claude`, or `opencode`) to profile defaults |
| `--no-agent-tool TOOL[,TOOL...]` | Disable one or more profile-default provider tools |
| `--web-interface INTERFACE` | Install an explicit headless web interface; currently `t3code` |
| `--web-interface-host IP` | Bind address for the selected web interface; defaults to loopback, or `0.0.0.0` when a source is supplied |
| `--web-interface-port PORT` | TCP port for the selected web interface; default `3773` |
| `--web-interface-source IP_OR_CIDR` | Add a private source specifically for direct web-interface access; repeatable; either this or a compatible generic source enables a non-loopback bind |
| `--no-web-interface` | Disable profile-provided web interfaces |
| `--no-web-interface-source` | Clear profile-provided web-interface source ranges |
| `--web-port PORT` | Manage an additional TCP web port through guest UFW; repeatable and source-restricted when generic sources are set |
| `--no-default-web-ports` | Disable the agent-VM defaults of TCP 80, 443, 8080, and 8081 |
| `--device-pairing PROVIDER` | Install the protected browser enrollment portal for a provider; repeatable, currently `t3code` |
| `--device-pairing-port PORT` | Pairing portal port; default `3774` and must differ from the web-interface port |
| `--device-pairing-auth-file PATH` | Controller-local Nginx htpasswd file for the portal; transient and not saved |
| `--device-pairing-password PASS` | Controller-local portal password; hashed locally, transient, and not saved; the portal username defaults to the setup username |
| `--no-device-pairing` | Remove the pairing broker, Nginx site, firewall rule, and installed portal password file from a saved host |
| `--browser-automation PROVIDER` | Install and register explicit agent browser automation; currently `playwright`, with selected Codex and/or OpenCode required |
| `--no-browser-automation` | Disable profile-provided browser automation |
| `--refresh-packages` | Force the APT update/upgrade and versioned runtime checks that normal reruns skip when their completion state is already present |
| `--git-access POLICY` | Set the VM's declared agent Git policy: `none`, `read`, or `read-write` |
| `--git-host HOST` | Select the GitHub CLI credential host; authenticated GitHub setup currently supports `github.com` |
| `--git-credential HTTPS_ORIGIN USERNAME` | Configure target-user Git and Git LFS authentication for one non-GitHub HTTPS origin using the matching workspace credential; repeatable |
| `--git-ca-certificate HTTPS_ORIGIN SOURCE` | Trust a local PEM path or retrieve one from an authenticated `ssh://USERNAME@HOST/ABSOLUTE_PATH` source, scoped only to one managed Git HTTPS origin; repeatable |
| `--no-git-credentials` | Remove all infra-tools-managed Git HTTPS credentials, helper configuration, and private CA files from the target user |
| `--git-auth active\|none` | Seed missing active GitHub CLI host credentials, or disable a profile auth default |
| `--git-auth-file PATH` | Seed a missing selected-host `hosts.yml` entry or one-line GitHub token from a controller-local file |
| `--agent-auth active\|none` | Seed missing selected agent credentials, refresh known-outdated Codex credentials from a current source, or disable a profile auth default |
| `--agent-auth-file TOOL PATH` | Stage one selected agent credential from a controller-local file at its canonical target path; `gh` accepts a hosts file or one-line token; setup otherwise preserves existing credentials, except for safe stale-Codex refresh; repeatable |
| `--agent-config active` | Copy known non-secret config from the active controller; does not copy auth files |
| `--interactive` | Prompt for tools, HTTPS repositories, Git policy, and credential sources |
| `--repo GIT_URL` | Clone an HTTPS repository below the selected agent workspace; repeatable |
| `--git-lfs` | Install Git LFS, initialize it for the target user, and do so before every requested repository clone |
| `--agent-workspace PATH` | Set the repository clone root; defaults to the target user's `~/repos` and may use a verified named-disk mount |
| `--backup SOURCE DESTINATION INTERVAL` | Configure a generic rsync-backed path mirror through the existing storage-ops service; repeatable |

On VM targets with agent features selected, guest UFW manages TCP ports 80,
443, 8080, and 8081 by default. Repeat `--web-port` for additional development
servers. `--no-default-web-ports` removes those agent defaults while retaining
explicit `--web-port` values. The rules are global when no generic access
source is set and source-restricted otherwise; applications must still bind a
reachable interface and provide their own authentication and transport
security. Local control planes, hardware, containers, and
`server_lite` do not receive the defaults. An explicit `--web-port` still
enables the standard SSH-rate-limited UFW policy for `server_lite`.

GitHub credential input requires `--git-access read` or `--git-access
read-write`; `none` is the public/unauthenticated repository mode.

### Setup completion access details

After a successful setup, the `Setup Complete!` block includes an `Access:`
section for selected web interfaces and services. It lists each usable URL or
endpoint with a one-line description: T3 Code, the optional protected
device-pairing portal, the generic web server, Gogs, Antistatic services, RDP,
Samba, and SSH. For example, a T3 Code setup with device pairing reports the
generated managed HTTPS endpoints as the usable links.
The direct T3 and Basic Auth listeners are intentionally omitted from this
completion summary.

Setup step output remains live while it runs. Marked warnings and errors are
also deduplicated into a final `Run notes:` section so important non-fatal
issues are easy to find without scanning the full transcript.

Agent tools are selected with repeatable or comma-separated `--agent-tool`
flags. The three agent profiles provide the narrow `gh` plus `codex` default;
supplied tools add to that set, while `--no-agent-tool` removes selected
defaults. Other profiles retain no implicit agents. Add unrelated packages with
`--apt-install`, and add runtimes with their individual flags. `--godot` works
with every profile, including the headless `agent_vm`; its system-wide launchers
make the engine available in SSH, desktop, T3 Code, and coding-agent shells.
Repeat `--godot-bundle` to give that same target account web-export and
publishing commands without changing its agent configuration.

Any setup with agent features installs a managed `~/.local/bin/infra-tools`
launcher for the target user. On a remote setup the launcher uses the source
deployed under `/opt/infra_tools`, so diagnostics and deliberate agent updates
work directly from an SSH, desktop, or T3 Code terminal without a separate
infra-tools installation on the VM. An existing executable with that name is
retained; setup never overwrites an unmanaged user launcher.

When Codex or OpenCode is selected, setup also installs the shared base
workflow skills under `~/.agents/skills`. T3 Code and Godot web setup add their
capability-specific skills. See [Managed agent workflow
skills](AGENT_SKILLS.md) for the catalog and reconciliation rules.

T3 Code is selected with `--web-interface t3code`; it is not an `--agent-tool`
provider, and infra-tools no longer installs the desktop AppImage. The server
path installs Node and T3 Code's upstream per-user background service; see
[T3_CODE.md](T3_CODE.md) for updates, LAN access, pairing, client choices, and
the loopback/HTTPS boundary. The optional
`--device-pairing t3code` portal lets a new device request its own one-time
link through Nginx Basic Auth. Setup publishes the portal through the managed
HTTPS gateway by default; see [DEVICE_PAIRING.md](DEVICE_PAIRING.md).

After a LAN T3 Code service is installed, obtain its one-time administrative
pairing URL from the control system with `infra-tools agent web pair HOST USER`
(add `--key PATH` when needed). The resulting app session includes T3's
`access:write` scope for pairing-link and client-session management. Opening the
bare service address is expected to show T3's pairing-key form; it is not an
authenticated session.

When the protected portal is selected, open the printed **T3 Code pairing
HTTPS endpoint**, answer the Basic Auth challenge, and pair the current browser
or create a link for a desktop/mobile client. Basic Auth protects only enrollment; T3 continues to
own and revoke the resulting device session. The direct HTTP mode is limited
to trusted private source CIDRs and does not encrypt the Basic Auth password.

Codex CLI, Claude Code, OpenCode, and T3 Code are installed from their official
distribution channels. T3 Code uses its documented background-service updater
with lifecycle scripts enabled only for that trusted update; see
[T3_CODE.md](T3_CODE.md) for the exact host-side command and recovery path.
The Codex installer runs with `CODEX_NON_INTERACTIVE=1`,
so setup does not prompt to start Codex or remove a conflicting installation.
The other agent tools are not installed with npm. Any
selected agent installs only that agent's tool and its required installer
dependencies.

Credential seeding and config copy are intentionally tool-scoped and transient:

- `--git-auth`/`--git-auth-file` seed only a missing selected GitHub host entry, preserve target-managed credentials on rerun, and run `gh auth setup-git`.
- `--agent-auth`/`--agent-auth-file` seed missing Codex, Claude Code, or OpenCode credentials without requiring those tools on the controller. They also replace refresh-required Codex auth when the staged source is unambiguously current; active `gh` requires controller `gh` only when its token is keyring-backed.
- `--agent-config active` copies known non-secret configuration from the active controller user.
- Codex and OpenCode receive only non-secret managed workflow skills; T3 Code
  adds its focused service and HTTPS-gateway guidance. infra-tools does not copy
  T3 Code credentials.

The root-only upload payload is removed after selected config is applied and
credentials are reconciled. Repositories are never cloned or cached on the
controller: the target VM performs each HTTPS clone after GitHub credentials
are configured.
Public repositories on any reachable Git host work without credentials. Agent
repository URLs with embedded credentials, SSH/scp syntax, and non-HTTPS schemes
are rejected. A requested repository that cannot be cloned stops setup, while an
existing repository is preserved only when its origin exactly matches.
Private non-GitHub origins use the separate `--git-credential` flow. Its
password is resolved from the workspace credential store, never embedded in a
repository URL or saved setup declaration. `--git-ca-certificate` optionally
adds origin-scoped trust for an internal CA or self-signed service certificate.
The source may be a local file or a certificate read directly from another
infra-tools host over host-key-verified SSH, so no intermediate controller copy
is required. It does not disable certificate verification globally. The target
stores the credential in a dedicated mode-`0600` file because unattended Git
and Git LFS need persistent access.

Select the relevant command below; this is a catalog, not a sequence to run.
Doctor is read-only unless `--fix` or `--record` is selected. Updates, workspace
creation, and maintenance holds change state:

```bash
infra-tools agent doctor
infra-tools agent doctor --tool codex --tool claude --json
infra-tools agent doctor --all-capabilities --json
infra-tools agent doctor --capability development --json
infra-tools agent doctor --tool codex --tool opencode --capability browser
infra-tools agent doctor --capability browser
infra-tools agent doctor --capability t3code --capability host
infra-tools agent doctor --capability t3code --capability host --record
infra-tools agent doctor --last-record --json
infra-tools agent doctor --capability t3code --fix
infra-tools agent doctor 10.0.0.10 agent --tool codex --json
infra-tools agent update --dry-run
infra-tools agent update --tool codex --tool claude
infra-tools agent update --json
infra-tools agent update 10.0.0.10 agent --tool codex --dry-run
infra-tools agent workspace create ~/repos/project api-check --base HEAD --json
infra-tools agent workspace list ~/repos/project --json
infra-tools agent workspace remove WORKTREE --dry-run --json
infra-tools agent maintenance hold --hours 8
infra-tools agent maintenance status --json
infra-tools agent maintenance release 10.0.0.10 agent
infra-tools agent support-bundle --output ~/agent-support.json
```

The default doctor check covers GitHub CLI, Codex CLI, Claude Code, and OpenCode.
Missing credential files are reported as sign-in reminders but do not make an
otherwise installed tool unhealthy. A present Codex file also gets a non-secret
freshness check; known invalid or refresh-overdue ChatGPT credentials make the
Codex doctor result unhealthy.
`--capability browser` additionally verifies managed launchers, MCP registration
for installed compatible agents, and a local Chromium interaction/rendering
smoke test.
`--capability development` inventories managed Godot, Go, and nvm/Node
toolchains. It verifies that installed engines and compilers run, reports
whether matching Godot export templates are visible, checks that Go includes
`gofmt` and a compiler when CGO is enabled, and ensures Node retains the
npm/PNPM baseline promised by `--node`.
Absent optional toolchains do not fail the capability.
`--capability t3code` checks the managed service, native runtime, pairing helper,
endpoint, provider authentication, Git identity and credential helper, and the
managed agent skill. Its `--fix` mode can rebuild blocked native dependencies,
configure the GitHub HTTPS helper after a successful login, enable the service
at boot, and restart it when inactive.
`--capability host` reports memory and swap headroom, filesystem and bounded
agent-storage use, T3 service cgroup pressure, recurring maintenance timer
state (including installed Node and Godot update jobs), the agent maintenance
hold, and pending reboots. Advisory pressure appears as warnings; critical
filesystem pressure or a recorded maintenance
failure makes the capability unhealthy.
When `--capability` is supplied without `--tool`, doctor checks only the
requested capability instead of requiring the default set of terminal agents.
`--all-capabilities` checks browser, development toolchains, host, and T3 Code
readiness and inventories the default terminal tools in the same text or JSON
result. Installed terminal tools are required; absent tools are reported as
optional inventory with `"required": false` and do not make the comprehensive
check fail. Explicit
`--tool` flags make every selected tool required, including an absent one. The
option is mutually exclusive with a narrowed `--capability` selection and works
with the remote `HOST USER` form.
Supplying `HOST USER` runs the same doctor through managed SSH from the control
system and preserves its text or JSON output and exit status. The target must
have been configured by infra-tools so `/opt/infra_tools` is present. Add
`--key PATH` when the VM uses a non-default SSH identity.

Add `--record` after a deliberate update or reboot to replace the private
readiness record at
`~/.local/state/infra_tools/agent-readiness.json`. A bare `doctor --record`
checks the default terminal tools and adds host readiness plus T3 Code when a
managed T3 installation is present; explicit `--tool` and `--capability`
selections retain their normal narrowing behavior. The mode-`0600` record
contains versions, aggregate checks, warnings, and the current Linux boot ID,
but omits executable and home paths, Git identity, credential contents,
repository contents, and process details. Use `doctor --last-record` to read it
without running checks. That command exits nonzero when the record is unhealthy
or belongs to a previous boot, so it can distinguish fresh post-reboot evidence
from an older successful audit. Both options support the remote `HOST USER`
form. Doctor JSON output retains its existing result-array shape when
`--record` is used; query the saved evidence separately with `--last-record
--json`.

`agent workspace` provides local task isolation for concurrent agents. `create`
places a dedicated `agent/TASK` branch below
`~/.local/share/infra_tools/worktrees`, leaving the primary checkout's files
untouched. `list` and `status` report branch, commit, and dirty state without
printing changed file names. The default base is the primary checkout's `HEAD`;
creation does not fetch remote refs or copy uncommitted changes. To start from
current remote main, fetch it first and pass `--base origin/main`. `remove`
accepts only a registered worktree below that managed root, refuses dirty or
untracked work, requires an `agent/*`
branch merged into the primary checkout's current `HEAD`, and never has a
force mode. Use its `--dry-run` before cleanup.

`agent maintenance hold` creates or renews a private automatic-restart hold
for 8 hours by default. `--hours N` accepts 1–72 hours, `status` reports the
active or expired deadline, and `release` is idempotent. An invalid marker
fails safe and should be released and recreated. The optional `HOST USER` form
runs the same operation as the target account through managed SSH. A hold does
not override `--auto-restart-force-days`; once that deadline is reached, the
restart proceeds even if a session, hold, or recognized workload remains.

`agent support-bundle` composes a stable local JSON snapshot from the agent,
T3 Code, browser, host, and maintenance diagnostics. It includes aggregate T3
log sizes and counts, not log text. It also records the infra-tools project
version, validated source commit, and dirty-state boolean when available, while
omitting the source branch. Browser diagnostics retain the safe-coordinate,
private-evidence, WebGL-settle, and launcher-security states so a stale or
unsafe managed launcher remains identifiable in a support artifact. Tool paths,
home paths, Git identity, repository contents and status names, prompts,
sessions, and credential contents are omitted. Without `--output` it prints
JSON; an output path must be a new file below the current user's home and is
written with mode `0600`. Browser configuration is inventoried without starting
Chromium; add `--browser-smoke` when it is relevant to exercise an explicitly
installed Playwright fallback for the report.

`agent update` deliberately updates the three user-installed terminal agents;
it is never run by an automatic host timer. The command uses each vendor's
supported path: OpenAI's standalone installer for Codex, `claude update`, and
`opencode upgrade`. It refuses executables resolved outside the current user's
home so package-manager installations remain under their package manager. It
must be run as the account that owns that home and reports the exact executable
path being updated; `/home/loren/.local/bin/codex` and
`/home/agent/.local/bin/codex` are separate installations.
Before changing a tool it checks `--version` and `--help`, retains the previous
executable, writes an atomic `in_progress` record, and repeats both checks after
the vendor updater exits. A changed or unusable executable is rolled back when
the update fails. Non-secret results are stored with mode `0600` in
`~/.local/state/infra_tools/agent-tools.json`; one prior executable per tool is
retained in the adjacent `agent-backups` directory. Codex installer bytes are
downloaded before execution with a size limit and their observed SHA-256 is
recorded, but upstream does not publish a pinned digest through this installer
contract, so the hash is audit evidence rather than independent publisher
verification.

After a non-dry-run update, the command also checks the selected terminal
tools and host readiness, adds T3 Code readiness when that managed service is
present, and saves the redacted readiness record described above. An unhealthy
post-update result or failure to persist it makes the update command exit
nonzero even when the vendor updater itself succeeded. Inspect the evidence
with `infra-tools agent doctor --last-record`; update JSON output retains its
existing per-tool result-array contract.

The optional `HOST USER` form runs that update as the target VM user. Run
`infra-tools agent update HOST USER --dry-run`, then repeat it without
`--dry-run` to apply. It does not use sudo or update another user's
installation. Remote doctor and update use the workspace `known_hosts` file
with strict host-key checking, like other managed SSH operations.

Setup reruns still skip the official installer when a selected command is
already available, then reconcile selected user-installed terminal agents
through the verified updater. Executables owned outside the target user's home
are left to their package manager. Use `infra-tools agent update` for an
agent-only update outside setup or when you want to select tools explicitly.
Codex-enabled setup reruns also perform one bounded authentication freshness
check before the updater, giving renewable credentials a chance to recover on
an infrequently started VM.
For example, when an operator is logged in as another account:

```bash
sudo -u agent -H sh -lc \
  'cd /home/agent && infra-tools agent update --tool codex'
```

The updater resets the working directory and user-scoped environment before
calling the vendor installer, so the invoking account's home and PATH do not
leak into the update.

For a deliberately chosen vendor update outside infra-tools, verify the
installed tool's supported update mechanism and use its owning account and
home directory. The managed command above supplies those settings and adds
preflight checks and rollback.

Credential rotation does not rebuild the VM or overwrite repositories:

```bash
infra-tools agent auth set 10.0.0.10 agent --tool gh --file /run/secrets/gh-hosts.yml
infra-tools agent auth set 10.0.0.10 agent --tool codex --active
infra-tools agent auth status 10.0.0.10 agent --json
python3 infra_tools.py agent auth pull 10.0.0.10 agent
```

`auth set` accepts an active-user source, a controller-local file, or
interactive source selection. Active `gh` rotation can retrieve a keyring-backed
token through the controller's `gh auth token`; active Codex, Claude Code, and
OpenCode rotation still requires their file-backed credential paths. GitHub
input is filtered to `github.com` and is installed with an atomic mode-`0600`
replacement. Status reports only tool
installation, credential presence/metadata, safe Codex refresh and cached-token
dates, and the GitHub authentication check; it never prints credential
contents, token strings, or Codex account IDs. Normal setup preserves existing
credentials except for a safe refresh of known-outdated Codex auth; `auth set`
is the deliberate replacement path for every other case.
`auth pull` copies canonical file-backed credentials from an existing agent VM
to the active controller user's canonical paths without requiring local agent
programs. A private staging directory remains available through
`--output-dir`. Known-stale Codex auth is refreshed automatically only from a
current source; other existing files require `--overwrite`. Run it from a
clone when infra-tools is not installed; see [Agent authentication](AGENT_AUTHENTICATION.md#pull-credentials-from-an-agent-vm).

The normal restart policy defers for active login sessions, coding agents,
build and Git processes, terminal multiplexers, maintenance holds, and
processes running in an infra-tools-managed agent worktree. It can still force
a reboot after seven days by default. For a host running long unattended agent
tasks, use both `--no-auto-restart` and `--auto-restart-force-days 0` only if
automatic restarts must be fully disabled, then manage pending security
reboots explicitly.

### Proxmox provisioning flags

| Flag | Description |
|------|-------------|
| `--provision-on HOST` | Create the setup target on this Proxmox node or registered host |
| `--provision-user USER` | SSH user for the Proxmox node |
| `--provision-key PATH` | SSH key for the Proxmox node; defaults to a saved host key, `--key`, or SSH config |
| `--bridge NAME` | Proxmox bridge for the new guest; defaults to the node's default-route bridge |
| `--memory SIZE` | Guest memory |
| `--balloon-min SIZE` | VM-only minimum memory for dynamic ballooning; defaults to `--memory` |
| `--balloon-shares N` | VM-only relative memory priority during balloon contention (1-50000; default 1000) |
| `--allow-memory-overcommit` | Explicitly allow running VM memory floors to exceed the node balloon target |
| `--storage root POOL AMOUNT` | Required root storage spec |
| `--storage root AMOUNT` | Root storage shorthand using saved defaults or `auto` |
| `--storage NAME POOL AMOUNT` | Declare a named non-root QEMU data disk; repeatable and addable to a saved managed VM when paired with a new mount |
| `--storage NAME AMOUNT` | Named-disk shorthand using the root-pool default |
| `--storage-mount NAME PATH [FILESYSTEM] [empty]` | Prepare the matching blank data disk at an empty guest path; filesystem defaults to `ext4` and may be `ext4` or `xfs` |
| `--storage-cache DATA_NAME CACHE_NAME [MODE]` | Consume a second named VM disk as an LVM cache for a mounted data disk; mode defaults to `writethrough` and may be `writethrough` or `writeback` |
| `--storage template POOL` | LXC template storage spec |
| `--storage template` | LXC shorthand for the saved/default template pool |
| `--cores N` | Guest vCPU count |
| `--cpu-type MODEL` | Proxmox VM CPU model; defaults to `host` |
| `--disk-discard [NAME]` / `--no-disk-discard [NAME]` | Enable or disable discard/TRIM globally, or override only `root` or one named VM disk; enabled by default |
| `--disk-ssd [NAME]` / `--no-disk-ssd [NAME]` | Advertise all declared VM disks, or only `root` or one named disk, as SSD-backed; disabled by default |
| `--disk-backup [NAME]` / `--no-disk-backup [NAME]` | Include disks in Proxmox backups globally or per device; enabled for non-swap disks and forced off for swap disks |
| `--base NAME` | Base image family |

Notes:

- `--storage` is repeatable.
- `root` storage is required on initial provisioning. A saved managed VM's
  additive mounted-disk rerun may omit it because the verified declaration is
  merged from local metadata.
- Provisioned VM disks use VirtIO SCSI single with a per-disk I/O thread and
  discard enabled. Use `--no-disk-discard` when storage policy must suppress
  guest TRIM. `--disk-ssd` is explicit because a Proxmox pool can be remote,
  mixed-media, or backed by a cache whose latency cannot be inferred reliably;
  an unqualified disk flag changes the VM-wide default. Append `root` or a
  declared data/cache disk name to override only that device. Device overrides
  win over the default regardless of command-line order.
- A mixed-media VM with an SSD boot disk and HDD data disk can use:

  ```bash
  --storage root local-lvm 32G \
  --disk-ssd root \
  --storage archive bulk-lvm 2T \
  --storage-mount archive /srv/archive ext4 empty
  ```

  To start from an all-SSD default and exclude one device, use
  `--disk-ssd --no-disk-ssd archive`. Reruns identify `root` as `scsi0` and
  named disks by their stable serials; manually attached SCSI disks outside
  the declaration are left unchanged.
- `--cpu-type host` exposes the node CPU for the best single-node performance.
  Choose a common Proxmox `x86-64-*` model for guests that must migrate across
  nodes with different CPU generations.
- Every named data disk must have exactly one mount declaration unless it is
  consumed as the cache device in `--storage-cache` or by `--swap-device`;
  logical names use lowercase letters, numbers, and hyphens and are at most 17
  characters. A same-provider setup rerun for a saved managed QEMU VM may add
  one or more named disks when each addition has a new empty-path
  `--storage-mount`. Existing storage and mount declarations may be omitted
  and are merged from saved metadata, or may be repeated unchanged. The
  provider verifies every saved disk, enough capacity, and enough SCSI slots
  for the complete request before attaching only the authorized new
  identities. Rerunning the same concise declaration after success is valid
  and performs no duplicate attachment. Additive cache media, swap disks,
  `/home`, LXC disks, manual-volume adoption, replacement, detach, and resize
  remain unsupported.
- `--storage-cache` builds a guest-side LVM cache from two entire blank disks.
  Put the data disk on the durable pool and the cache disk on SSD storage. The
  data disk retains its normal `--storage-mount`; the cache disk must not have
  one. `writethrough` is the safer default because writes reach both the cache
  and origin before completion. `writeback` can improve write latency but
  accepts additional data-loss risk if the cache volume or its backing SSD is
  lost. This declaration is provisioning-only and does not adopt an existing
  VG, partitioned disk, or populated filesystem.
- Automated mounting accepts only a confirmed blank disk and an empty `/data`
  path, a path below `/srv`, `/var/lib`, `/opt`, or `/mnt`, or `/home` while
  provisioning a new QEMU VM. The `/home` case mounts the disk before the
  setup user is created. A newly declared mounted disk can be hot-added to an
  existing saved QEMU VM; start from `infra-tools cmd NAME` when the setup has
  other service flags that must be retained. Populated-path migration,
  existing-disk adoption, detach, and data-disk resize remain unsupported.
- Guest mounts are required UUID-based systemd mounts. Missing or mismatched
  storage stops dependent Gogs and agent repository setup instead of writing
  to the root filesystem.
- To give a newly provisioned VM a separate `/home` disk, use a logical disk
  name and mount it at `/home`:

  ```bash
  --storage home-data local-lvm 32G \
  --storage-mount home-data /home ext4 empty
  ```

  The VM setup user is created only after this blank disk is mounted. This is
  available for new VMs only; it does not migrate `/home` on an existing VM.
- Provisioned VMs automatically use a matching key from the registered
  Proxmox host or the local `~/.ssh/id_ed25519`, `id_ecdsa`, or `id_rsa`
  identity. Use `--key PATH` when the guest should use a different identity;
  the matching `PATH.pub` is installed by cloud-init for the SSH handoff when
  a new VM is created. Existing-only reconciliation of a saved VM does not
  require that public-key file to remain on the controller.
- Provisioned VMs keep fixed memory by default while retaining the VirtIO balloon
  device for guest-memory telemetry. Set `--balloon-min` below `--memory` to
  opt into dynamic ballooning; the minimum cannot exceed the maximum. Provisioning
  reports running-guest floors and burst maxima before creation and warns when
  either total exceeds the node balloon target. Burst maxima may exceed the
  target with a warning; a floor-over-target configuration is refused unless
  `--allow-memory-overcommit` is supplied. `--balloon-shares` changes relative
  priority during contention; it is not a reservation.
- `server_proxmox` setup always reconciles a node balloon target. Automatic mode
  reserves at least 20% or 2 GiB (whichever is larger), caps the target at 80%,
  and prints the resulting values. `--proxmox-balloon-target` explicitly
  overrides that policy and is accepted only for `server_proxmox`.
- `server_proxmox` setup preserves the existing swap layout, reports active
  swap devices, warns for absent or direct ZFS zvol-backed swap, and persists
  and verifies `vm.swappiness=10`.
- The positional target is the guest IPv4 address. A bare address defaults to
  `/24`; use `ADDRESS/PREFIX` for another prefix. Do not repeat it with `--ip`.
- `template` storage is LXC-only.
- Direct setup defaults to `--machine auto`, which detects Debian bare metal,
  VMs, and Proxmox LXC containers on the target.
- Proxmox provisioning defaults to a VM because it is creating a new guest;
  use `--machine unprivileged` for an LXC.
- `--machine unprivileged` keeps an existing or intentional LXC path.
- `--ipv6`, gateway, DNS, and `--hostname` remain regular setup options. When
  omitted, the IPv4 gateway comes from the selected Proxmox bridge and DNS
  comes from the Proxmox node (with bridge-specific values preferred). The
  controller machine's gateway is not reused because it may be on a different
  LAN or VPN.

### Swap configuration flags

| Flag | Description |
|------|-------------|
| `--swap-mode auto|preserve|none` | `auto` reconciles declarations or creates `/swapfile` only when no swap exists; `preserve` changes no areas; `none` removes only infra-tools-owned areas |
| `--swap-file NAME PATH SIZE [priority=N]` | Manage a swap file; repeatable |
| `--swap-device NAME SOURCE [priority=N] [discard=POLICY]` | Manage a whole block device; `SOURCE` is a declared VM disk, `UUID=...`, or `/dev/disk/by-id/...`; discard is `off`, `once`, `pages`, or `both` |
| `--swap-zram NAME SIZE [priority=N] [algorithm=TOKEN]` | Manage compressed-RAM swap with `systemd-zram-generator`; repeatable |
| `--swappiness N` | Set `vm.swappiness` from 0 through 200 |
| `--zswap` / `--no-zswap` | Enable or disable the kernel's compressed zswap cache; managed zram and enabled zswap are mutually exclusive |
| `--zswap-max-pool-percent N` | Limit zswap to 1-50% of RAM; requires `--zswap` |
| `--swap-resume NAME` | Configure a declared swap device for hibernation resume |
| `--no-swap-resume` | Remove the managed hibernation resume-device configuration |
| `--swap-initialize NAME` | One-shot authorization to initialize a blank direct device; newly provisioned named VM swap disks are already tool-owned |

Linux uses the highest-priority swap first and shares I/O across areas with
equal priority. This makes a small SSD tier plus a larger HDD tier explicit:

```bash
--storage swap-fast local-lvm 16G \
--disk-ssd swap-fast \
--swap-device fast swap-fast priority=200 discard=once \
--storage swap-bulk bulk-lvm 64G \
--swap-device bulk swap-bulk priority=10 \
--swappiness 80
```

Swap disks cannot also be mounted or used as LVM cache media, and they are
always excluded from Proxmox backups. Other disks default to backup enabled;
use `--no-disk-backup` for a disposable VM and selectively restore important
working data with `--disk-backup NAME`. Direct devices are never reformatted
unless blank and named by `--swap-initialize`; existing filesystems,
partitions, mounts, or unrelated signatures stop setup. Removing a swap
declaration never wipes its block-device signature.

Infra-tools records owned areas in `/opt/infra_tools/state/swap.json` and
edits only a marked block in `/etc/fstab`. Existing unmanaged swap is
preserved. Managed fstab entries use `nofail`, and ownership is journaled
before creating files or zram so interrupted setup can be retried safely.
Btrfs swap files are rejected because their creation is
filesystem-specific. ZFS swap files, zvol-like devices, and Proxmox guest
swap disks allocated on a `zfspool` produce warnings because those layouts
have not yet been qualified by this project.

## Deployment Flags

| Flag | Description |
|------|-------------|
| `--deploy DOMAIN GIT_URL` | Deploy a repository to a domain |
| `--deploy-latest DOMAIN_OR_PATH GIT_URL` | Deploy while bypassing the release/dependency freshness policy |
| `--deployment-lite` | Use cached/pre-uploaded repository files only |
| `--deployment-full` | Pull fresh repositories and rebuild everything |
| `--full-deploy` | Always rebuild deployments even if unchanged |
| `--ssl` | Enable TLS: Let's Encrypt for domains and an IP-SAN self-signed certificate for hostless Gogs |
| `--ssl-email EMAIL` | Email for SSL registration |
| `--cloudflare` | Configure Cloudflare Tunnel; close direct HTTP/HTTPS only after the tunnel is verified active |

Repos can also ship `infra.json` manifests for multi-component deploys; see
[Deployments and manifests](./DEPLOYMENTS.md) for the schema and examples.
Ruby/Rails repositories are rejected before remote setup begins. Use a pinned
legacy infra-tools release to maintain an existing Rails deployment; current
setup does not remove its old systemd unit or same-domain generated Nginx site
when that whole domain is omitted from the current deployment set.

## CI/CD and Build / App Servers

| Flag | Description |
|------|-------------|
| `--build-server` | Configure a build server that deploys to app servers |
| `--app-server` | Configure an app server to receive deployments |
| `--deploy-target HOST` | Target app server for deployments |
| `--cicd` | Install the signed GitHub webhook receiver and isolated executor |

See [`CICD.md`](./CICD.md) for webhook configuration, service units, and
deployment-boundary behavior.

After saving one `--build-server` setup and one `--app-server` setup, connect
them from the controller without copying keys manually:

```bash
infra-tools cicd connect build app --target-name production
infra-tools cicd status build
infra-tools cicd test build production
```

`BUILD` and `APP` accept saved hosts, friendly names, or exact tags. The
controller reuses its already enrolled app-server host key, installs only the
build server's public deploy key, writes the dedicated build-user
`known_hosts` and target JSON atomically, and verifies the connection as the
unprivileged `webhook` account. If the app key is not enrolled yet, the command
shows its fingerprint and requires confirmation. For unattended enrollment,
provide an independently obtained value with `--fingerprint SHA256:...`.
`--port` and `--base-dir` customize the target connection; setup defaults are
port 22, user `deploy`, and `/var/www`. A custom base directory must already
exist as a real directory and be writable by `deploy`.

## Antistatic

`--antistatic-server` and `--antistatic-db` deploy the managed release binaries.
Hostname-based specs are reverse-proxied through Nginx;
hostless specs such as `:8080` or `:8081` listen directly on the target port.

| Flag | Description |
|------|-------------|
| `--antistatic-server [DOMAIN][:PORT]` | Deploy the lobby server |
| `--antistatic-admin USERNAME` | Enable HTTPS-only report administration using the matching workspace credential |
| `--no-antistatic-admin` | Disable administration and remove its remote credential file |
| `--antistatic-db [DOMAIN][:PORT]` | Deploy antistatic-db |

The lobby server stores bounded report collections under
`/var/lib/antistatic`, sends a local `/health` probe after each service start,
and exposes STUN directly on UDP 3478. Hostname deployments redirect ordinary
HTTP traffic to HTTPS; `--cloudflare` instead marks tunnel traffic secure at
the private nginx-to-server boundary.

Admin access requires a hostname deployment and either `--ssl` or
`--cloudflare`. Store its password separately, then reference the username.
See [Antistatic services](./ANTISTATIC.md) for the complete workflow and
credential-storage behavior.

## Gogs

See [Gogs Git service](./GOGS.md) for hostname and hostless modes, initial
credentials, Git-over-SSH, data layout, and update recovery.

Deploy a minimal self-hosted Git service with an optional hostname, port, and
data directory:

```bash
infra-tools setup server_web 192.168.1.10 \
  --gogs git.example.com:3000 /var/lib/gogs \
  --ssl --ssl-email admin@example.com
```

The Gogs port is the direct public HTTP/HTTPS port and defaults to 3000; Gogs
does not implicitly claim port 443. With `--cloudflare`, it instead remains the
private backend port because the tunnel supplies the standard public HTTPS
endpoint. Port 80 is reserved when Gogs uses nginx, TLS, a hostname, or
Cloudflare; hostless direct plaintext mode may deliberately use it.
Hostname mode requires `--ssl` or `--cloudflare`.
Use `--gogs :3000` for a hostless loopback service reached through an SSH
tunnel. To expose hostless HTTPS on a trusted private network, add `--ssl` and
repeat `--gogs-source IP_OR_CIDR`. Hostless TLS uses an IP-SAN self-signed
certificate that clients must explicitly trust; reruns replace an invalid,
mismatched, or soon-expiring certificate. Only non-global IPv4 sources from
the combined generic and Gogs-specific policy are applied, active UFW is
required, and setup fails before binding externally if it cannot verify the
rules. A literal IP cannot be used as the hostname, Cloudflare requires a
hostname, and Let's Encrypt hostname mode rejects source restrictions because
HTTP-01 renewal needs public port 80. Gogs releases require
and verify the SHA-256 supplied in GitHub release asset metadata before
extraction, and activation can
roll back to the previous release if service, post-update, or health checks
fail.

| Flag | Description |
|------|-------------|
| `--gogs DOMAIN[:PORT] [DATA_PATH]` | Install Gogs on direct public HTTP/HTTPS `PORT` (default 3000), or use `PORT` as the private backend with `--cloudflare`; port 80 is reserved when nginx/TLS/hostname/Cloudflare is used; omit `DOMAIN` for loopback/source-restricted hostless mode |
| `--gogs-source IP_OR_CIDR` | Allow one private IPv4 source to a hostless Gogs listener; repeatable and requires active UFW |

Inspect an installed service from the control system with:

```bash
infra-tools gogs health 192.168.1.10
infra-tools gogs health 192.168.1.10 --json \
  --min-free-bytes 2147483648 --min-free-inodes 20000
```

The command inherits the saved setup username and SSH key unless overridden
with `--username` or `--key`. A non-root SSH user must have non-interactive
sudo, which requires the setup's explicit `--nopasswd` compatibility mode;
otherwise connect as root. Health is nonzero when the service,
SQLite database, managed paths, local filesystem, update service/timer,
capacity thresholds, or documented nginx upload limit is unhealthy. JSON also
reports release identity, per-category usage, the external URL, and whether
the LFS HTTP endpoint is configured for non-loopback access or is loopback-only.
Reverse-proxied deployments additionally require valid nginx configuration and
a successful target-local request through the direct TLS listener or
Cloudflare origin route. This is not an external client or authentication
probe, so it does not prove public DNS, routing, edge, firewall, or credential
health.

Configure a clean local worktree so GitHub remains the canonical fetch source,
Gogs receives mirrored Git refs, and only Gogs stores Git LFS objects:

```bash
infra-tools gogs repo-configure ~/repos/project \
  --github-url https://github.com/team/project.git \
  --gogs-url https://git.example.com:3000/team/project.git \
  --track 'assets/**' --dry-run
```

Without `--dry-run`, the command configures repository-local Git LFS, the
`origin` and `gogs` remotes, two `origin` push URLs, `.lfsconfig`, and any
repeatable `--track` patterns. It never stages, commits, pushes, creates remote
repositories, or configures credentials. `--no-combined-push` configures only
GitHub as the `origin` push URL so the Gogs Git remote can be updated
explicitly. See [Gogs Git service](./GOGS.md) for the storage, credential, and
off-network clone boundaries.

## HomeBox

See [HomeBox inventory](HOMEBOX.md) for onboarding, storage, credentials,
updates, and recovery. One native systemd instance is supported per server.

| Flag | Description |
| --- | --- |
| `--homebox DOMAIN[:PORT] [DATA_PATH]` | HTTPS hostname (frontend default 443; requires `--ssl`), or `:PORT` for SSH-tunnel access; data defaults to `/var/lib/homebox` |
| `--homebox-port PORT` | Hostname mode's loopback backend port, default 7745 |
| `--homebox-admin EMAIL` | Initial owner email, default `admin@homebox.local`; target generates the password |
| `--homebox-version TAG` | Explicit stable release for an immediate upgrade, minimum/default `v0.26.2`; ordinary reruns retain the installed release while the managed timer checks newer stable releases weekly |
| `--no-homebox` | Disable service and remove its ingress while retaining data, secrets, releases, and backups |

Health, backup, and restore accept `--username`, `--key`, `--workspace`, and
`--json`. They inherit saved SSH settings; use `--username root` unless the
selected user has non-interactive sudo. Archive paths refer to the target.
Backup and restore stop the service; restore requires `--yes`. A dry run
validates command intent without connecting. Health exits nonzero for an
unhealthy or incomplete installation and probes HTTPS locally, so it does not
verify external DNS or routing. Enabled HomeBox services also receive a weekly
automatic stable-release check through the recovery-aware update timer;
`--homebox-version` remains available for a deliberate immediate upgrade.
Cloudflare ingress is not supported.

## Storage and data movement

| Flag | Description |
|------|-------------|
| `--samba` | Install and harden Samba for authenticated SMB3 file sharing |
| `--samba-source IP_OR_CIDR` | Add a Samba-only ingress source; repeatable and combined with generic access sources |
| `--no-samba-source` | Clear saved Samba-specific ingress sources |
| `--samba-metadata-cache PATH` | Put Samba's disposable TDB metadata cache in an absolute directory outside share paths |
| `--no-samba-metadata-cache` | Return Samba metadata caching to `/var/cache/samba` |
| `--share TYPE NAME PATH USERS` | Configure one Samba directory share |
| `--credential USERNAME PASSWORD` | Define a password for username-only share entries |
| `--smbclient` | Install SMB/CIFS client |
| `--mount-smb MOUNTPOINT IP CREDENTIALS SHARE SUBDIR` | Mount an SMB share persistently; `SUBDIR` may be `/` |
| `--syncthing` | Install a managed, non-root Syncthing endpoint with an authenticated HTTPS admin UI |
| `--syncthing-admin USERNAME` | Select the workspace credential used for the web administrator (default: `syncthing-admin`) |
| `--syncthing-root PATH` | Confine GUI-managed folders to a storage root (default: `/srv/syncthing`) |
| `--no-syncthing` | Stop and remove the managed service while preserving its identity, database, and synchronized files |
| `--sync SOURCE DEST INTERVAL` | Configure rsync sync |
| `--scrub DIR DBPATH REDUNDANCY FREQ` | Configure par2 integrity checking |
| `--notify TYPE TARGET` | Configure notifications |
| `--notification-level LEVEL` | Set outbound volume to `verbose`, `normal` (default), `warning`, `error`, or `off`; local logs are unaffected |
| `--notification-strict-https` | Require CA and hostname verification for HTTPS notification webhooks; omitted by default so self-signed receivers work |

Samba shares are authenticated and hardened; `TYPE` is `read` or `write`, and
`PATH` is one absolute directory. The metadata-cache option moves only Samba's
non-persistent TDB state, not share contents. See
[Samba Shares](./SAMBA_SHARES.md) for credentials, access control, fast
updates, removals, and SMB client mounts.
See [Managed Syncthing](./SYNCTHING.md) for HTTPS administration, GUI-managed
device enrollment and folders, relay behavior, and recovery boundaries.
See [Storage operations](./STORAGE_OPERATIONS.md) for sync, parity, schedules,
mount checks, and logs, and [Notifications](./NOTIFICATIONS.md) for delivery
targets and failure behavior.

## Maintenance and Utilities

### Firmware audit and updates

```text
infra-tools firmware audit [--no-refresh] [--json] [--install-dependencies]
infra-tools firmware update [DEVICE_ID] [--no-refresh] [--allow-running-guests] [--install-dependencies] [--yes]
```

Both commands operate on the local machine through `fwupdmgr`. When it is
missing, infra-tools offers to install the Debian `fwupd` package with APT;
`--install-dependencies` records that consent without a separate dependency
prompt. `audit` reports DMI identity, related package versions, supported
devices, and available fwupd releases. Metadata is refreshed unless
`--no-refresh` is supplied.

`update` repeats the audit and requires a separate firmware confirmation. On a
Proxmox host, running guests block the update unless
`--allow-running-guests` is explicit, while an incomplete guest-state check
always blocks it. `--yes` skips the firmware confirmation and forwards
fwupd's non-interactive consent; it never bypasses the audit or guest check.
infra-tools always suppresses fwupd's reboot prompt and does not reboot the
machine. See [Firmware auditing and
updates](./FIRMWARE.md) for coverage limits, legacy vendor firmware, and
recovery precautions.

### Cleaning obsolete local configuration

Use `cleanup` after upgrading infra-tools when a saved setup or development
registry entry was written by an older, incompatible revision. A host argument
limits the operation to that setup host:

```bash
# Inspect only the saved setup state for this VM.
infra-tools cleanup 192.168.0.41 --dry-run

# Remove obsolete setup state after reviewing the findings.
infra-tools cleanup 192.168.0.41 --yes

# Inspect and clean every invalid setup cache and Proxmox record.
infra-tools cleanup --dry-run
infra-tools cleanup --yes

# Select one category explicitly.
infra-tools cleanup --setup-cache --yes
infra-tools cleanup --proxmox-registry --yes
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
infra-tools maintenance github --root /home/loren/repos audit
infra-tools maintenance github --root /home/loren/repos prune --yes
infra-tools maintenance github --root /home/loren/repos prune --delete-caches --yes
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

See [`MAINTENANCE.md`](./MAINTENANCE.md) for schedules and policy controls.

### Network Inventory

```text
infra-tools network list
infra-tools network init <profile> [--management CIDR] [--control-plane CIDR] [--guest-network CIDR]
infra-tools network add-host <profile> <name> <address> [--provider NAME] [--role ROLE]
infra-tools network import-proxmox <profile> [--host NAME] [--tag TAG]
infra-tools network import-proxmox-guests <profile> [--host NAME] [--tag TAG]
infra-tools network plan-proxmox <profile> [--proxmox] [--json]
```

`plan-proxmox` is read-only and requires at least one management source and
one control-plane address before it will produce a non-error plan.

### Proxmox Management

```text
infra-tools proxmox add <name> <address> [--user USER] [--key PATH]
infra-tools proxmox hosts
infra-tools proxmox remove <name-or-address>
infra-tools proxmox probe <host>
infra-tools proxmox probe-cluster <address> [--user USER] [--key PATH] [--tag TAG]
infra-tools proxmox audit <host> [<host> ...] [--json]
infra-tools proxmox rolling-update <target> [<target> ...] [--dry-run] [--reboot-timeout SECONDS]
infra-tools proxmox top <host> [<host> ...]
infra-tools proxmox plan place [options]
infra-tools proxmox plan rebalance [options]
infra-tools proxmox ls <host>
infra-tools proxmox status <host> <vmid>
infra-tools proxmox start <host> <vmid>
infra-tools proxmox pause <host> <vmid>  # alias: suspend
infra-tools proxmox resume <host> <vmid>
infra-tools proxmox stop <host> <vmid> [--force]
infra-tools proxmox destroy <host> <vmid> [-y] [--force]
infra-tools proxmox health <host> <vmid> [--no-ssh]
infra-tools proxmox config <host> <vmid> [--pending]
infra-tools proxmox reconfigure <host> <vmid> --set KEY=VALUE [--set ...]
infra-tools proxmox modify <host> <vmid> [--cores N] [--memory N[M|G]]
infra-tools proxmox resize-disk <host> <vmid> <volume> <size>
infra-tools proxmox backups <host> <vmid>
infra-tools proxmox backup <host> <vmid> [--storage POOL] [--mode MODE] [--compress FORMAT]
infra-tools proxmox snapshots <host> <vmid>
infra-tools proxmox snapshot <host> <vmid> <name> [--description TEXT] [--dry-run]
infra-tools proxmox rollback <host> <vmid> <name> [--dry-run]
infra-tools proxmox delsnapshot <host> <vmid> <name> [--dry-run]
infra-tools proxmox migrate <host> <vmid> <target> [--online] [--with-local-disks]
infra-tools proxmox clean-disks <host> [--delete] [--yes] [--dry-run]
infra-tools proxmox unlock <host> <vmid> [--dry-run]
infra-tools proxmox notifications install-webhook <host> <url> [--send-test]
infra-tools proxmox notifications test-webhook <host>
infra-tools proxmox [shell]

infra-tools vm list <host> [--json]
infra-tools vm show <local-name> [--json]
infra-tools vm show <host> <id> [--json]
infra-tools vm health <local-name> [--no-ssh] [--json]
infra-tools vm health <host> <id> [--no-ssh] [--json]
infra-tools vm stats <target> [<id>] [--json]
infra-tools vm status <target> [<id>] [--json]
infra-tools vm start <target> [<id>] [--json]
infra-tools vm pause <target> [<id>] [--json]  # alias: suspend
infra-tools vm resume <target> [<id>] [--json]
infra-tools vm shutdown <target> [<id>] [--timeout SECONDS] [--json]
infra-tools vm stop <target> [<id>] [--json]
infra-tools vm reboot <target> [<id>] [--timeout SECONDS] [--json]  # alias: restart
infra-tools vm autostart <target> [<id>] [--json]
infra-tools vm autostart <target> [<id>] --enable [--order N] [--start-delay SECONDS] [--shutdown-timeout SECONDS] [--json]
infra-tools vm autostart <target> [<id>] --disable [--json]
infra-tools vm snapshot list <local-name> [--json]
infra-tools vm snapshot list <host> <id> [--json]
infra-tools vm backup list <local-name> [--json]
infra-tools vm backup list <host> <id> [--json]
infra-tools vm destroy <local-name> [-y] [--force]
infra-tools vm destroy <host> <id> [-y] [--force]
```

`probe` caches bridge, gateway, DNS, and storage recommendations. `audit` is
read-only and checks core Proxmox services, cluster quorum, active tasks,
configured storage, root free space, guest locks, running guests, and the reboot
marker. It exits nonzero when the host is not healthy and supports stable JSON
output for automation.

Registered host records contain `schema_version: 1` and `provider: proxmox`.
`v2.0.0` rejects incompatible development records rather than
guessing how to interpret them. Remove an incompatible record by its stored
name or address, then register it again:

```bash
infra-tools proxmox remove pve1
infra-tools proxmox add pve1 10.0.0.10 --user root --key ~/.ssh/proxmox_ed25519
```

The provider-neutral `infra-tools vm ...` commands provide stable inventory,
inspection, health, power-state lifecycle, snapshot, and backup-list output,
plus confirmed QEMU VM destruction. Their JSON envelope includes
`schema_version`, `provider`, `host`, `operation`, and `resources`. In the
command shapes above, `<target> [<id>]` means either `<host> <id>` or the exact
friendly `--name` (or saved IP address) of an infra-tools setup with the ID
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
declaration; remove it separately with `infra-tools rm NAME` when it is no
longer wanted. Other Proxmox-specific guest mutations and host-administration
operations remain under `infra-tools proxmox ...` until their command paths
are migrated.

`rolling-update` uses saved setup commands and workspace credentials. It audits
all targets before making changes, audits each node again after its update and
reboot, and stops before an automatic reboot if that node still has running or
locked guests. Mutating subcommands accept `--dry-run` where supported; a rolling
update dry run still performs the read-only preflight audits.

### Interactive Shell

`infra-tools shell` opens a REPL for saved configurations. The shell loads
`~/.infra_toolsrc` on startup and persists history at
`~/.local/share/infra_tools/shell_history`.

Useful shell commands:

- `list`, `info`, `cmd`, `deploy`, `rm`, `recall`, `reconstruct`
- `new` / `setup` for a guided saved-setup flow
- `workspace` to change the active workspace
- `proxmox` to enter the Proxmox sub-shell

### Sysadmin Shortcuts

See [`SYSADMIN.md`](./SYSADMIN.md) for the host shortcut commands (`mount`,
`health`, `ssh`, `push`, `pull`, `df`, `fan`, `svc`, `logs`, `upgrade`,
`reachable`, `key`).

Before using SSH setup, deployment, or administration commands for a new host,
enroll its host key and verify the displayed fingerprint independently:

```bash
infra-tools ssh-key enroll example.com
```

SSH commands use strict checking against the workspace `known_hosts` file and
will refuse a host that has not been explicitly enrolled.

## Testing and bootstrap

The [installation guide](./INSTALLATION.md) covers supported installation and
local control-plane commands. Use the following when working from a checkout
or refreshing completion manually:

```bash
python3 -m unittest discover -s tests
./run_tests.py --suite smoke
uv tool install --upgrade argcomplete
infra-tools completions --shell bash
```

The full test matrix and detailed bootstrap behavior are covered by
[`OPERATIONS.md`](./OPERATIONS.md).

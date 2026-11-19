# Common machine configuration flags

[Documentation index](cli-setup.md)

### Common Setup Flags

On CachyOS, `--lan-access` uses the actual private IPv4 prefix of the single
default-route interface. `--access-source` supplies explicit sources, and
either option opts into desktop UFW management for selected T3/Sunshine
services. When SSH is enabled without explicit access flags, CachyOS selects
automatic private IPv4 LAN access. The saved selection records the resolved
SSH boolean, while LAN discovery runs again on every setup and refresh; explicit
`--access-source` values are saved. Use `--no-lan-access` or
`--no-access-source` to turn off automatic SSH access discovery. See
[workstation firewall policy](../workstations/cachyos.md#optional-workstation-firewall).
The target-address inference and server-service scope below apply to Debian.

| Flag | Description |
|------|-------------|
| `--lan-access` / `--no-lan-access` | Infer or remove target-adjacent LAN access: a private IPv4 target uses its `/24`, a ULA IPv6 target uses `/64`, and an explicit static prefix is honored |
| `--access-source IP_OR_CIDR [IP_OR_CIDR ...]` | Restrict managed inbound services to one or more sources; accepts multiple values and may be repeated |
| `--no-access-source` | Clear saved custom generic access sources; use `--no-lan-access` separately to remove inferred LAN access |
| `--ssh-access-source IP_OR_CIDR` | Add a trusted SSH-only source to generic sources for Debian UFW; repeatable; leaves public web ingress unchanged |
| `--no-ssh-access-source` | Clear saved SSH-only sources; generic sources still apply |
| `--rdp` / `--no-rdp` | Enable or disable remote RDP ingress; desktop profiles retain loopback XRDP |
| `--rdp-existing-password` | Local setup only: reuse an existing non-root desktop account password; a missing profile password is requested securely |
| `--rdp-bind-address IP` | Bind XRDP to one local IP; defaults to all IPv4 interfaces (`0.0.0.0`) |
| `--rdp-source IP_OR_CIDR` | Restrict UFW RDP ingress to a source; repeatable; `--no-rdp-source` clears profile sources |
| `--rdp-clipboard` / `--no-rdp-clipboard` | Control clipboard redirection; enabled by default |
| `--rdp-drive-redirection` / `--no-rdp-drive-redirection` | Control drive, printer, and device redirection; disabled by default |
| `--rdp-audio` / `--no-rdp-audio` | Control audio redirection; disabled by default |
| `--rdp-idle-timeout SECONDS` | Disconnect an idle session after this interval; 0 disables |
| `--desktop [xfce\|i3\|cinnamon\|lxqt]` | Configure one shared XRDP desktop; explicitly enables desktop capability on headless profiles |
| `--kiosk {web,cli}` / `--no-kiosk` | On `server_proxmox` only, enable the local browser kiosk in web or CLI startup mode, or disable its managed session; mutually exclusive |
| `--kiosk-url URL` | On `server_proxmox` only, set the kiosk's direct HTTP(S) homepage; must not contain credentials, whitespace, or control characters |
| `--kiosk-destination 'NAME=URL'` | On `server_proxmox` only, add a named HTTP(S) link to the offline kiosk homepage; repeat up to 16 times to replace the entire saved list |
| `--clear-kiosk-destinations` | Clear saved kiosk links and return to the direct homepage; mutually exclusive with `--kiosk-destination` |
| `--browser NAME` | Browser to install |
| `--editor [geany\|vscode]` | Install an explicit graphical editor; requires a desktop-capable setup or `--rdp` |
| `--flatpak` | Install desktop apps via Flatpak |
| `--office` | Install LibreOffice |
| `--apt-install PACKAGE` | Install a package via apt |
| `--flatpak-install PACKAGE` | Install a package via Flatpak |
| `--dark` | Configure dark theme |

Kiosk destination names are trimmed, nonempty, at most 80 characters, and unique
without regard to case. Control and format characters are rejected. URLs use
the same validation as `--kiosk-url`; equals signs within URLs are preserved.
Nonempty destinations require an enabled kiosk. On a patch, omitted destination
flags inherit the saved list; repeats replace it, and
`--clear-kiosk-destinations` explicitly saves an empty list even when the kiosk
is absent or off. `--no-kiosk` clears both the saved homepage URL and destination
list, and cannot be combined with a nonempty destination list.

With destinations, the browser opens a static local link page that works
offline; opening a link still requires its destination to be reachable. With no
destinations, the browser opens the original direct homepage instead. For example:

```bash
basaltw setup server_proxmox pve.example root --kiosk web \
  --kiosk-destination 'Proxmox=https://pve.example:8006/' \
  --kiosk-destination 'Dashboard=https://dashboard.example/'
basaltw patch pve.example root --clear-kiosk-destinations
```

Static address configuration is validated before any remote work begins and
supports NetworkManager, systemd-networkd, and ifupdown on Debian. Without
`--activate-network`, direct-host configuration is persisted but not activated
live; reboot or deliberately restart the interface after reviewing it.

For `--provision-on`, the IPv4 gateway and DNS default to values discovered
from the selected Proxmox bridge and node. Those values are saved with the
setup and restored on reruns. If a legacy setup cache is missing them,
Basaltwater refreshes the Proxmox defaults instead of skipping discovery. Once
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
[SSH authentication](../operations/ssh.md) for the same behavior across transfers,
Proxmox operations, maintenance, and agent commands.

`--verify-provider` is a setup-only check and requires `--provision-on`. Use it
when a cached provisioned guest should be compared with Proxmox even though
the saved declaration has not changed; supported provider-side drift, such as
the guest vCPU count, is reconciled and verified. Managed disks are also
verified against the cached pool and minimum-size declaration. All provider
reconciliation driven by saved VM metadata is existing-only: if the saved VM
cannot be found, setup stops instead of silently creating a replacement.

After moving a Basaltwater-provisioned QEMU VM with the Proxmox GUI, rerun its
saved setup command with the destination in `--provision-on`. A changed
provider host is never replaced with the cached source: Basaltwater verifies
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
interface receive a one-time `.basaltwater.bak` copy.

```bash
basaltw setup server_lite 192.168.1.50 admin \
  --hostname app-01 \
  --ip 192.168.1.50/24 --gateway 192.168.1.1 \
  --dns 1.1.1.1 --dns 1.0.0.1 \
  --network-interface eth0

# Reassign an existing saved host without interrupting the setup SSH session
basaltw patch 192.168.1.50 admin \
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
basaltw setup workstation_dev 192.168.0.25 agent \
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
service-only addition for SMB ingress. `--ssh-access-source IP[/PREFIX]` adds
trusted SSH-only ingress without restricting public HTTP/HTTPS. It is repeatable,
supports IPv4 and IPv6, and combines with generic sources only for SSH firewall
rules and reconnect protection. Patches replace the saved SSH-only list when
supplied and preserve it when omitted; `--no-ssh-access-source` clears it.
Without effective SSH sources, the normal SSH rate limit remains. See
[SSH firewall policy](../operations/ssh.md#repeated-and-parallel-operations).
With Cloudflare Tunnel, generic sources
also retain direct HTTP/HTTPS origin access from those networks. Intentionally
public Antistatic endpoints retain their own exposure policy.

Without a generic source or `--rdp-source`, enabling RDP keeps a globally
rate-limited UFW rule. On rerun, Basaltwater installs requested source rules
before removing broad rules and reconciles only its own comment-tagged rules.
The single desktop is retained through disconnect until logout. Session-count
and disconnect-cleanup flags are removed; saved legacy policies are migrated
with warnings for nondefault behavior. See [XRDP](../workstations/xrdp.md) for runtime commands,
human takeover, console conversion, and rollback.

Setup `--dry-run` validates the requested configuration and prints the planned
upload or local-copy handoff without applying target setup. It does not run the
target-side step list or prove that installation will succeed. Deployment and
provisioning options can still perform preflight work, such as fetching a
repository; use a simple profile when exploring without those dependencies.
The separate target-side `remote_setup.py --dry-run` prints the selected step
list. Neither preview is a completed live setup.

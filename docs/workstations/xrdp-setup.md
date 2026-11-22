# Set up XRDP

[Topic index](xrdp.md)

## Set up XRDP

On Debian workstations, setup uses the official Debian Sid builds of `xrdp`
and `xorgxrdp` because the Trixie `xorgxrdp` build has a known Xorg crash in
the RDP capture path. Sid is added with a low-priority pin. Only `xrdp` and
`xorgxrdp` explicitly select Sid; ordinary X11 utilities retain normal package
selection. Package installation disables recommendations,
so the optional PipeWire XRDP module and its large codec/runtime dependency
tree are not installed. Minimal desktop installs can omit the recommended Xorg
server. Setup installs a missing `xserver-xorg-core` through normal Debian
package selection before the XRDP transaction; this prerequisite step preserves
an already installed Xorg server and does not pin it to Sid or bypass normal
security-update candidates. Setup simulates the XRDP transaction first and refuses it
if it would remove packages or upgrade core packages such as `libc6`,
`systemd`, or `xserver-xorg-core`. The install keeps modified configuration
files without prompting, then reapplies the managed versions after the
package transaction completes.

Generic APT repair preserves this exact managed source only with its matching
low-priority pin. It restores sources commented out by older basaltwater repair
runs. Desktop setup refreshes signed APT metadata and checks for newer XRDP and
xorgxrdp packages even when both are installed. Rerun desktop setup to apply
updates from this pinned channel. `--refresh-packages` also refreshes the
supporting desktop packages.

For a remote target, provide the Unix account password through a secret source:

```bash
basaltw setup workstation_dev 10.0.0.25 agent \
  --desktop xfce --rdp --password "$RDP_PASSWORD" \
  --lan-access
```

For local setup of an existing non-root desktop account, reuse its password
without placing it in process arguments:

```bash
sudo "$(command -v basaltw)" setup workstation_dev localhost "$USER" \
  --control-plane --desktop xfce --rdp --rdp-existing-password
```

`--rdp-existing-password` is local-only. It cannot be combined with
`--password`, does not create or change the account password, and is rejected
when the account does not already exist. When a terminal prompt is available,
an empty password for a local setup is treated as this reuse mode; provisioned
guests must receive a new password.

Restrict all managed services to several explicit networks with one flag:

```bash
--access-source 10.0.0.0/24 100.64.0.0/10
```

Add `--rdp-source IP_OR_CIDR` when a source should reach RDP without being
added to the generic policy; RDP uses the union of both lists. Without either
kind of source, XRDP remains reachable through the globally rate-limited rule.
Use `--rdp-bind-address IP` to bind the listener to one local IPv4 or IPv6
address. Direct RDP defaults to `0.0.0.0`, the IPv4 wildcard. An IPv6 wildcard
(`::`) can also accept IPv4 on dual-stack builds. Setup verifies the actual
listener and its unprivileged `xrdp` identity before enabling it at boot. The
firewall reconciles rules tagged `basaltwater RDP` and leaves unrelated UFW
rules in place.
This reconciliation also runs for `server_lite --rdp`, before starting XRDP.

Client idle-disconnect and channel controls:

```bash
--rdp-idle-timeout 3600
--no-rdp-clipboard
--rdp-drive-redirection
--rdp-audio
```

Session-count and disconnect-cleanup flags are removed. Old saved values are
migrated to the fixed policy, with warnings for nondefault behavior changes.
Idle timeout only disconnects a viewer. Logout ends desktop applications;
keep independent long-running work in SSH, tmux, or supervised agent services.

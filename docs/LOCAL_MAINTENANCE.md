# Local system maintenance

Use the `local` command for focused changes to the Debian machine where
`basaltw` is installed. These commands reuse the same package, desktop,
browser, hostname, and network steps used by full setup, but do not rebuild
the whole workstation or control-plane profile.

Mutating local commands require root, so run them with `sudo`. Use
`--dry-run` before a change when the command supports it:

```bash
sudo basaltw local --help
sudo basaltw local install --dry-run btop ripgrep
```

## Packages and system updates

Refresh Debian package metadata, upgrade installed packages, and remove
packages APT no longer needs:

```bash
sudo basaltw local update
```

Install one or more Debian packages without running the rest of setup:

```bash
sudo basaltw local install neovim tmux jq
```

Package names are validated before APT runs. The command uses the same
noninteractive APT behavior as setup. If APT sources need repair first, use
`local update`.

For larger changes that also need a language runtime, security policy, or
maintenance timer, use the normal local setup flags instead:

```bash
sudo basaltw setup workstation_dev localhost "$USER" \
  --node --python --go --dry-run
```

## Desktop environments

Configure the machine's one shared XRDP desktop while all graphical sessions
are logged out:

```bash
sudo basaltw local desktop xfce
sudo basaltw local desktop cinnamon --dark
```

Environment choices are `xfce`, `i3`, `cinnamon`, and `lxqt`. This command
installs the shared session runtime and XRDP on loopback, and disables console
graphical login. Existing applications and home files remain. It does not add
a second desktop beside GNOME. `--dark` applies the selected theme.
Use SSH or a text console for setup. See [shared desktop operation and
migration](XRDP.md) before converting an existing graphical workstation.

To expose RDP remotely and configure its firewall policy, use setup with `--rdp`:

```bash
sudo basaltw setup workstation_dev localhost "$USER" \
  --desktop xfce --rdp --rdp-existing-password
```

## Browsers and desktop applications

Install a supported browser without repeating desktop setup:

```bash
sudo basaltw local browser firefox
sudo basaltw local browser librewolf --flatpak --no-default
```

The default behavior configures the browser for the local desktop user. Use
`--no-default` when installing a secondary browser. Supported browsers are
`brave`, `firefox`, `librewolf`, `helium`, `browsh`, and `lynx`.

For LibreOffice or an explicit graphical editor, use the workstation profile's
existing flags and inspect the plan first:

```bash
sudo basaltw setup workstation_dev localhost "$USER" \
  --office --editor geany --dry-run
```

`workstation_dev` already includes Firefox ESR and Neovim. Use `--editor
geany` for the lightweight Debian IDE or `--editor vscode` for the explicitly
scoped Microsoft repository. The `pc_dev` profile adds LibreOffice, SMB client
support, and Remmina. Third-party GUI applications such as Discord are
explicit; install them by package identifier with `--flatpak-install`.

## Hostname, IP address, and DNS

Set the persistent hostname without rerunning the full setup:

```bash
sudo basaltw local hostname workstation-01
```

Advertise a server hostname on the local network with Avahi/mDNS. This enables
names such as `fileserver.local` for SSH and other LAN clients:

```bash
basaltw setup server_lite 192.168.1.50 admin \
  --hostname fileserver --mdns --dry-run
basaltw setup server_lite 192.168.1.50 admin \
  --hostname fileserver --mdns
```

The flag installs and enables `avahi-daemon`, installs `libnss-mdns`, and adds
the managed UDP 5353 UFW rule. Use `basaltw patch HOST --no-mdns` to stop
the managed Avahi service and remove its managed firewall rule. Clients must
support mDNS; Debian/Ubuntu clients can install `libnss-mdns`. mDNS is limited
to the local Layer-2 network and normally does not cross VLANs or routed
subnets.

View current interface addresses:

```bash
basaltw local ip
```

Stage a static IPv4 address, gateway, and DNS servers:

```bash
sudo basaltw local ip 192.168.1.50/24 \
  --gateway 192.168.1.1 \
  --dns 1.1.1.1 --dns 1.0.0.1 \
  --interface enp1s0
```

Use `local network` for a dual-stack configuration:

```bash
sudo basaltw local network \
  --ip 192.168.1.50/24 --gateway 192.168.1.1 \
  --ipv6 2001:db8:1::50/64 --gateway6 2001:db8:1::1 \
  --dns 1.1.1.1 --dns 2606:4700:4700::1111 \
  --interface enp1s0
```

The network commands support NetworkManager, systemd-networkd, and ifupdown.
They write persistent configuration and deliberately do not restart the
active interface, so an SSH session is not cut off. The command reports the
backend it selected. Reboot, or deliberately restart the interface after
reviewing the generated configuration, to activate the change. On ifupdown
systems, the previous configuration receives an `.infra-tools.bak` backup.

Do not apply a static address over an SSH-only connection unless you have an
out-of-band console or another recovery path. Use `--dry-run` to validate the
requested values and see the intended interface before writing anything.

## Other local maintenance commands

The focused commands complement, rather than replace, the existing local
tools:

```bash
# Install or refresh the basaltw launcher and its base dependencies.
sudo basaltw bootstrap

# Install Python aliases, uv, and shell completion support.
basaltw python-tools

# Refresh shell completion files.
basaltw completions --shell bash

# Inspect and deliberately update user-installed coding agents.
basaltw agent doctor
basaltw agent update --dry-run

# Inspect or change the basaltw source channel, then update it.
basaltw channel
basaltw upgrade
```

Use `basaltw setup ... localhost ...` when several changes should be
coordinated, when a profile's security and maintenance steps are needed, or
when the operation is part of a saved configuration. Use the focused `local`
commands for small, independent maintenance tasks.

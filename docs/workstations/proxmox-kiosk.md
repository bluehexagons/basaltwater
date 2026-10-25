# Proxmox Local Browser Kiosk

The optional kiosk starts Firefox on the local console of a Proxmox VE 9
server running Debian 13. It is intended for a node with DRM graphics and
locally connected display and input devices. This guide describes operator
configuration; it does not qualify any particular GPU, monitor, keyboard,
touchpad, Surface device, or Proxmox hardware combination.

The feature applies only to `server_proxmox`. It is incompatible with
`--desktop`, `--rdp`, and guest `--guacamole` on the host. Guacamole remains an
optional guest installation (`--guacamole`) used with the guest's HTTPS panel
as described in [Guacamole](guacamole.md). A host kiosk does not change
networking: use the existing LAN bridge and manual address configuration.
Wi-Fi failover is a separate, deferred capability.

## Configure the kiosk

Use the normal setup command with `server_proxmox`, or patch a saved Proxmox
host. For example:

```bash
sudo basaltw setup server_proxmox pve.example.lan root --kiosk web
sudo basaltw patch pve.example.lan root --kiosk cli
sudo basaltw patch pve.example.lan root --kiosk web \
  --kiosk-url https://pve.example.lan:8006/
sudo basaltw patch pve.example.lan root --no-kiosk
```

`--kiosk web` installs and enables the local browser session at boot.
`--kiosk cli` installs the kiosk runtime but boots to the normal text console;
start it when needed with `sudo systemctl start basaltwater-kiosk.service`.
The modes are mutually exclusive with `--no-kiosk`.

On a patch, omitted kiosk options inherit the saved selection. `--no-kiosk`
clears the saved URL and removes the managed unit, configuration, and systemd
preset. A URL-only patch uses the inherited enabled mode; an explicit URL with
`--no-kiosk` is rejected. Disabling the kiosk preserves the dedicated account,
Firefox profile and browser data, and installed packages.

When no URL is supplied, the kiosk opens the Basaltwater HTTPS web panel when
that panel is enabled. Otherwise it opens the native Proxmox interface at
`https://HOST:8006/`. `--kiosk-url` overrides this destination. The URL must
use HTTP or HTTPS and have a valid authority; it cannot contain user
credentials, whitespace, or control characters. When the node is offline or
its hostname cannot resolve locally, use an appropriate reachable address or
hostname and ensure the kiosk account can resolve it. For an internal TLS
service, trust your own CA through the normal system/browser trust guidance;
there is no hidden TLS verification bypass.

## Session and security model

The browser runs as the dedicated `basaltwater-kiosk` account. It has a locked
password, a nologin shell, no extra groups, and a private persistent home at
`/var/lib/basaltwater-kiosk`. Firefox stores its profile under
`/var/lib/basaltwater-kiosk/profile/`. The installer does not store API or
administrator credentials. Sign in using the same normal web authentication
you would use in Firefox; signed-in sessions can persist in the mode-`0700`
browser data directory.

Firefox ESR runs with normal tabs, navigation, and address bar under Cage on
tty7. This is a flexible local browser, not a locked-down public-terminal
security boundary. Cage's `-s` option allows switching virtual terminals. Use
Ctrl+Alt+F1 to reach the Proxmox host's text login, then Ctrl+Alt+F7 to return
to the kiosk; a Surface keyboard may require its Fn key for function-row
shortcuts. The kiosk uses greetd with PAM/logind, takes the last connected
monitor, and does not change lid or suspend policy.

Guest access is not discovered automatically, and the host UI gains no new
guest switcher. Add guest URLs as Markdown links or bookmarks in your VM Notes
when useful. The kiosk does not scan the network. Browser connectivity follows
the normal LAN bridge and manually configured host addresses.

## Runtime and lifecycle

The runtime installs the APT packages `greetd`, `cage`, `firefox-esr`,
`dbus-user-session`, `libpam-systemd`, `libgl1-mesa-dri`, and
`fonts-dejavu-core`, along with their required dependencies. Distribution
security updates continue through the normal package maintenance path; a full
desktop environment is not installed. Package rollback is not automatic.

Basaltwater owns `basaltwater-kiosk.service` and immutable configuration
generations named `/etc/basaltwater/kiosk/session-<hash>.json` and
`session-<hash>.toml`. Old generations remain until the kiosk is disabled.
The systemd preset Basaltwater owns disables the vendor greetd unit, and the
setup refuses to override an existing foreign display manager. It also refuses
unowned configuration conflicts and other graphical seats. The kiosk service
uses a runtime directory and clears greetd's initial-session marker when a
manual service restart is requested.

An unchanged rerun leaves the browser running. A changed URL or unit restarts
the service in web mode; CLI mode stops and disables it. Use these commands to
inspect or operate the service:

```bash
sudo systemctl status basaltwater-kiosk.service
sudo systemctl start basaltwater-kiosk.service
sudo systemctl stop basaltwater-kiosk.service
sudo systemctl restart basaltwater-kiosk.service
sudo journalctl -u basaltwater-kiosk.service
sudo journalctl -u greetd
```

A compositor startup failure retries at most three times in quick succession.
After repeated failure, or after Firefox closes normally, tty7 shows text
instructing the operator to use Ctrl+Alt+F1 and restart the service. The
journal is useful for service and greetd startup diagnostics; runtime messages
from greetd's child may instead appear on tty7. A network failure leaves the
browser on its error page and does not trigger kiosk restarts.

To remove the managed kiosk, patch with `--no-kiosk`. This removes only the
managed service/configuration/preset and retains the account, browser data,
and packages. Do not manually delete immutable generations while the feature
is enabled.

## Proxmox and hardware qualification

The environment used to prepare this guide has no DRM graphics device or
physical seat. Unit and mocked validation do not demonstrate that a real
Proxmox host, Surface, display, keyboard, or touchpad works. Qualify the exact
hardware and monitor arrangement before relying on a kiosk, especially for
remote VMs that need a local browser. The existing host lid and suspend policy
is unchanged.

Record results for each intended hardware combination, including:

- DRM device and graphics initialization, monitor detection, resolution and
  scaling, and whether the last-connected-monitor behavior is usable.
- Keyboard, touchpad or mouse input, including text entry, pointer movement,
  browser navigation, and Ctrl+Alt+F1/F7 switching while the local console has
  focus (including any Surface Fn-key behavior).
- `web` mode boot to the configured URL and `cli` mode boot to text, followed
  by a manual `systemctl start`.
- Reboot behavior, browser close/restart behavior, and the displayed guidance
  after a compositor failure.
- Network unplug and recovery behavior in the browser, while confirming a
  separate SSH session to the Proxmox host remains available.
- Service status and journals during startup, plus complete removal with
  `--no-kiosk` and preservation of the documented account/browser data.

Keep an independent SSH path available while qualifying the local console.

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
basaltw setup server_proxmox pve.example.lan root --kiosk web
basaltw patch pve.example.lan root --kiosk cli
basaltw patch pve.example.lan root --kiosk web \
  --kiosk-url https://pve.example.lan:8006/
basaltw patch pve.example.lan root --no-kiosk
```

`--kiosk web` installs and enables the local browser session at boot.
`--kiosk cli` installs the kiosk runtime but boots to the normal text console;
start it when needed with `sudo basaltw kiosk start` on the Proxmox host.
The modes are mutually exclusive with `--no-kiosk`.

On a patch, omitted kiosk options inherit the saved selection. `--no-kiosk`
clears the saved URL and destinations and removes the managed unit,
configuration, and systemd preset. A URL-only patch uses the inherited enabled
mode; an explicit URL with `--no-kiosk` is rejected. Disabling the kiosk preserves the dedicated account,
Firefox profile and browser data, and installed packages.

When no URL is supplied, the kiosk opens the Basaltwater web panel using its
configured scheme and port when enabled. Otherwise it opens the native Proxmox interface at
`https://HOST:8006/`. `--kiosk-url` overrides this destination. The URL must
use HTTP or HTTPS and have a valid authority; it cannot contain user
credentials, whitespace, or control characters. When the node is offline or
its hostname cannot resolve locally, use an appropriate reachable address or
hostname and ensure the kiosk account can resolve it. For an internal TLS
service, enroll its CA using [client CA trust](../services/client-ca-trust.md)
or the browser's certificate settings. Certificate verification remains enabled.

## Save destinations

Add named links for guest consoles, coding environments, or a guest's Guacamole
panel. Supply the complete desired list; repeated flags preserve their order:

```bash
basaltw patch pve.example.lan root \
  --kiosk-destination 'Coding VM=https://coding.example.lan/' \
  --kiosk-destination 'Agent console=https://agent.example.lan/console'
```

An enabled kiosk with destinations opens a local start page containing the
configured home page and these links. Links open in new tabs, so the start page
remains available for switching. Normal browser tabs, address bar, bookmarks,
and navigation stay available. The page loads without a network connection; each destination still
requires its normal network and web authentication. It does not test availability
or reload open consoles when connectivity changes.

Names are trimmed when parsed and must be unique ignoring case, contain no
control characters or `=`, and have at most 80 characters. Configure up to 16
destinations; each URL follows the same HTTP(S) validation as `--kiosk-url`.
Avoid URLs with tokens: destination URLs are ordinary setup configuration,
not secret storage.

Omitting destination flags on a patch inherits the saved list. Supplying one or
more `--kiosk-destination` flags replaces it. To return to opening the configured
host URL directly:

```bash
basaltw patch pve.example.lan root --clear-kiosk-destinations
```

Changing destinations restarts a running kiosk in web mode, just like changing
its URL. The local page is regenerated at browser startup under the private
kiosk home as `start.html`; it can be bookmarked in Firefox. Clearing configured
destinations or disabling the kiosk retains browser data and that private file.

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

Guest access is not discovered automatically. Use saved kiosk destinations,
Markdown links in VM Notes for the host panel, or browser bookmarks.
The kiosk does not scan the network. Browser connectivity follows
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

An unchanged rerun leaves the browser running. A changed URL, destination list,
or unit restarts the service in web mode; CLI mode stops and disables it.
Use these commands to inspect or operate the service:

```bash
basaltw kiosk status
basaltw kiosk doctor --json
sudo basaltw kiosk start
sudo basaltw kiosk stop
sudo basaltw kiosk restart
sudo journalctl -u basaltwater-kiosk.service
sudo journalctl -t basaltwater-kiosk
```

These commands operate on the local host. Status and doctor are read-only;
run doctor as root when account or private-file checks require it. Doctor
reports missing prerequisites and recovery suggestions without installing
packages, probing destination URLs, or changing services. Status describes
the service and sessions; an active login manager does not prove Firefox or
a guest console is ready. All five commands accept `--json`. Start, stop, and
restart require root and preserve the configured boot enablement. Stop checks
that the dedicated user's PAM sessions finish cleanup before reporting success.
The equivalent `systemctl` commands remain available for administrator recovery.

A compositor startup failure retries at most three times in quick succession.
After repeated failure, or after Firefox closes normally, tty7 shows text
instructing the operator to use Ctrl+Alt+F1 and restart the service. The
journal's service filter shows greetd startup diagnostics; the
`basaltwater-kiosk` identifier shows browser and compositor output. The stop
hook terminates this dedicated account's logind sessions too, including browser
processes outside greetd's service cgroup, and waits up to 45 seconds for user
and runtime teardown. A matching pre-start check also blocks a new browser
while old sessions remain. Repeated greetd failures, including unexpected
zero-status exits, restart with a three-start limit in ten minutes. Systemd also
counts manual starts, restarts and setup-triggered restarts toward this limit.
If a start is refused with `start-limit-hit`, the CLI reports the required
recovery without clearing the limit or retrying. Inspect the local service
journal, correct any startup problem, then use
`systemctl reset-failed basaltwater-kiosk.service` before explicitly starting
again. A network failure leaves the browser on its error page and does not
trigger kiosk restarts.

When opening a direct homepage, the browser supervisor first waits up to 30
seconds for a TCP connection to that URL's hostname and port. It retries short
connection attempts within one startup budget, then opens Firefox even when
the endpoint is still unavailable. Stopping the kiosk interrupts the wait,
reaps its probe worker, and prevents Firefox from starting. Hostname resolution
runs in that worker so a stalled resolver cannot hold the supervisor indefinitely.

This is a startup connectivity check, not application or browser readiness.
It sends no HTTP requests, URL paths, query strings, or authentication, and
leaves certificate validation to Firefox. An HTTP error or certificate issue
can still appear after the port accepts connections. The offline destination
page opens immediately without checking any saved destination. Once Firefox
opens, the supervisor does not monitor connectivity or reload, reopen, or
restart tabs during a network interruption; use the browser to reconnect.
Compositor crash retries do not repeat the startup wait. Startup messages appear
in `journalctl -t basaltwater-kiosk`; status and doctor keep their existing
read-only behavior without network probes.

To remove the managed kiosk, patch with `--no-kiosk`. This removes only the
managed service/configuration/preset and retains the account, browser data,
and packages. Do not manually delete immutable generations while the feature
is enabled.

If the service's unit file was manually removed while systemd still has it
loaded, active, or enabled, setup refuses to continue. This also applies when
`daemon-reload` changes its load state to `not-found` while processes remain
active. Restore the managed file, or stop and disable
`basaltwater-kiosk.service`, then run `systemctl daemon-reload` before retrying.
This prevents a still-running login manager from recreating sessions during cleanup.

## Proxmox and hardware qualification

Selected Debian 13 QEMU checks exercise the kiosk component directly with
virtio graphics, virtual input and Firefox ESR: installation, CLI/web boot,
offline destinations, opening a link in a new tab, exact keyboard text submission,
VT switching, normal browser quit, bounded compositor-crash retries, explicit
recovery, local commands, unchanged rerun and removal. Account/profile identities
survive lifecycle changes and reboot; checked stops leave no kiosk processes or
user runtime. Removal retains the account, browser data and packages.

These are component checks, not a complete `server_proxmox` installation or
physical-seat qualification. The cloud kernel initially lacked DRM support;
the fixture required Debian's standard kernel. Virtual graphics used software
rendering and produced DRM warnings and a transient blank redraw during VT
switching before Firefox returned. Qualify graphics and switching behavior on
the intended machine rather than inferring it from this VM.

A real Proxmox host, Surface, monitor arrangement, keyboard and touchpad still
need qualification, along with native/guest console access, scaling, network
interruption and delayed direct-homepage startup. The existing host lid and
suspend policy is unchanged. See the [coverage map](../plans/testing-coverage.md)
for the current boundaries.

Record results for each intended hardware combination, including:

- DRM device and graphics initialization, monitor detection, resolution and
  scaling, and whether the last-connected-monitor behavior is usable.
- Keyboard, touchpad or mouse input, including text entry, pointer movement,
  browser navigation, and Ctrl+Alt+F1/F7 switching while the local console has
  focus (including any Surface Fn-key behavior).
- `web` mode boot to the configured URL and `cli` mode boot to text, followed
  by `basaltw kiosk start`; verify status/doctor for a healthy seat and for
  missing devices or unavailable inspection permissions.
- Saved destinations displayed locally while offline, keyboard focus and link
  opening in ordinary Firefox tabs, patch inheritance/replacement/clear, and
  direct host URL startup after clearing the list.
- Reboot behavior, browser close/restart behavior, and the displayed guidance
  after a compositor failure.
- Network unplug and recovery behavior in the browser, with the host console
  available throughout; verify SSH access before and after reconnecting. Cover
  delayed homepage startup, a permanently offline endpoint, and stopping the
  kiosk while it waits; verify existing console tabs remain untouched after
  connectivity changes.
- Service status and journals during startup, plus complete removal with
  `--no-kiosk` and preservation of the documented account/browser data.

Keep an independent SSH path available while qualifying the local console.

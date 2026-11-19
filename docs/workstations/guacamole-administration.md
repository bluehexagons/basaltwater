# Guacamole administration and troubleshooting

[Topic index](guacamole.md)

## Access and lifetime

Nginx authenticates both HTTP requests and the WebSocket upgrade using the
panel's existing Basic Auth. The backend requires the proxy's authenticated
identity, HTTPS transport, a configured host, matching Origin on actions and
WebSocket upgrades, and CSRF on state-changing requests. Desktop responses are
not cached and cannot be framed by another site. JavaScript is served locally;
desktop pages have their own CSP without relaxing other panel pages.

The connect action creates one random attachment capability, valid for 30
seconds and claimable once. The Unix password is discarded after the native
handshake or when the unused attachment expires. The capability appears only
in the initial WebSocket URL; access logs retain only the existing
privacy-preserving authentication-failure records, and no Unix password is
included in a URL. Refreshing releases this page's connection; sign into XRDP
again to reconnect. An active connection in another tab must still be disconnected
explicitly. Logged-out, replaced and expired sessions do not block a new login.

Connections expire after one hour, or ten minutes without accepted control
input or a control action. Network pings and display updates do not extend
user inactivity. An observation-only connection therefore expires after ten
minutes unless a control action occurs. Expiry leaves desktop applications running
and releases browser input while keeping saved agent permission.

The page displays the remaining session and inactivity limits. Select
**Observe only** to deliberately continue observing. Status polling does not
renew inactivity or extend the one-hour limit, and an expired session cannot
be revived with a late control action.

Changing the panel password prevents new connections but does not revoke an
already upgraded WebSocket. Use **Disconnect**, or restart
`basaltwater-guacamole.service`, to revoke it immediately. Removing Guacamole
stops active tunnels. No passwords or screen contents are retained in audit
logs. Screen contents are visible to anyone with the panel credential and a
valid Unix account login.

## Installation and service isolation

Setup downloads a versioned `basaltwater-guacamole` Debian package from
[the dedicated build repository](https://github.com/bluehexagons/basaltwater-guacamole).
That repository pins Apache Guacamole 1.6.0 source archives and builds an RDP/SSH
`guacd` with its JavaScript client in CI. It maintains build configuration rather
than an Apache source fork. amd64 packages are built natively and
checked in fresh Debian containers without development packages.
Arm64 is unsupported and no longer built; older release assets are historical.

Review the amd64 package and its SHA-256 before updating the version and pin in
`lib/guacamole_config.py`.

The installer verifies the package SHA-256 pinned in Basaltwater's source and
checks its name, version and architecture before asking APT to install it.
APT installs the package's declared runtime libraries; Python aiohttp also comes
from Debian APT. Compilers and development headers stay in build CI. No Java,
Tomcat, database, Docker daemon or VNC server is installed. The gateway uses
Debian's FreeRDP 3 packages; upstream labels FreeRDP 3 support experimental.

The current binary release lives at `/opt/basaltwater-guacamole/1.6.0-bw2`.
Unchanged reruns validate and reuse the installed package without downloading
or rebuilding Guacamole. Unsupported distributions or architectures fail
explicitly; there is no automatic source-build fallback. Binary release updates
require a reviewed Basaltwater version/hash change; there is no separate
automatic upstream release updater. Debian runtime libraries continue under
the normal system update policy. An incomplete, unmanaged, writable or unsafe
linked release stops setup, including on reruns.

The gateway and RDP relay run as the configured non-root account with systemd hardening.
`basaltwater-guacamole.service` runs `guacd` and the browser tunnel in a private
network namespace. The namespace contains only local gateway, RDP and SSH relay
ports. It has no route to the host's network or arbitrary remote machines.
`basaltwater-guacamole-rdp.service` provides an account-private Unix socket
which forwards only to the host's `127.0.0.1:3389` XRDP listener. Nginx reaches
the HTTP application through a separate Unix socket restricted to the account
and its web-service group. No raw Guacamole port is exposed on the host.

The private namespace also relays port 2222 to
`/run/basaltwater-guacamole-ssh/ssh.sock`. The
`basaltwater-guacamole-ssh.socket` unit accepts at most two connections on that
account-only Unix socket; it opens no host TCP listener. Each connection starts
OpenSSH in inetd mode through `basaltwater-guacamole-ssh@.service`.
OpenSSH performs the normal PAM account/password/session checks, drops to the
configured user and creates its PTY. This login service begins as root, like
the public SSH daemon, because a normal Unix login must establish the user's
groups and permissions. It does not inherit the gateway's read-only home or
privilege restrictions. It permits only the configured non-root account,
requires a nonempty Unix password, and disables SSH forwarding and user RCs.
Its one-hour service limit also bounds a shell if the gateway fails.

Completed login instances are unloaded even when OpenSSH exits unsuccessfully,
as it does when a client abandons login. This prevents per-connection failures
from accumulating in `systemctl --failed`. Their exit results remain in the
system journal; the persistent socket and gateway retain normal failed-state
reporting.

The endpoint uses `/etc/basaltwater/guacamole-sshd.conf`, independent of
`/etc/ssh/sshd_config`; public key-only SSH policy is unchanged. Setup creates
a dedicated root-private Ed25519 host key under
`/etc/basaltwater/guacamole-ssh/` and pins its public key in the gateway
configuration. Passwords stay in memory for the SSH handshake. The gateway
cannot select another host, port, account, private key or startup command.
Removing Guacamole stops the private socket and active login instances and
removes its configuration; the host key is retained for a later reinstall.
Orderly service stops revoke sessions and release held browser keys/buttons
before terminating native children. Forced termination cannot guarantee input
release, but the supervisor expires browser ownership. Saved agent permission
survives either shutdown path. Disconnect before maintenance when possible.

The gateway and RDP relay report readiness to systemd only after their listeners are
established. The gateway additionally waits for `guacd` to listen. Setup waits
for this notification inside the existing grouped unit transaction, so startup
failure restores prior service definitions and activation state. On activation
failure, setup also restores the prior non-secret gateway configuration and
reloads an active restored gateway. Interrupted or failed rollback still needs
the normal [transaction recovery](../operations/transaction-recovery.md) workflow.

An upgrade can temporarily leave the new runtime rejecting the previous gateway
configuration. Systemd reports that failed service as `activating` with substate
`auto-restart` while waiting to retry. Setup accepts this state and installs the
new configuration and units; it does not require manually stopping the gateway.
Actual startup, shutdown or reload transitions still block a transaction, and
the error includes the observed load, active and substate values.

The gateway pins the configured XRDP certificate's SHA-256 fingerprint.
Certificate verification is not disabled. After changing the XRDP certificate,
rerun the saved setup or patch with `--guacamole` to update the pin.

## Diagnose and remove

```bash
sudo systemctl status basaltwater-guacamole.service basaltwater-guacamole-rdp.service
sudo systemctl status basaltwater-guacamole-ssh.socket
sudo journalctl -u basaltwater-guacamole.service -n 50 --no-pager
sudo nginx -t
basaltw desktop doctor
```

Run desktop commands as the desktop owner. A login failure can mean an incorrect
Unix password, a changed XRDP certificate, or an unhealthy shared desktop.
Console login failures can also mean a locked/expired Unix account, PAM policy,
or an unhealthy private SSH socket. Inspect the socket and login units rather
than enabling password authentication on the public SSH listener.
Missing/private sockets, failed namespace setup, and missing native libraries
are service readiness failures. Native protocol logs are suppressed to keep
credentials and desktop contents private; service errors provide bounded generic
readiness messages. Existing XRDP diagnostics remain available.

For a shareable JSON report, run
`python3 scripts/collect_guacamole_diagnostics.py` from a Basaltwater checkout as
the desktop owner. Run `sudo -v` first to allow the collector's noninteractive
log reads. Redirect the output to a private file (for example, set `umask 077`
before redirecting to `guacamole-diagnostics.json`). The collector records
bounded gateway, private SSH and XRDP logs, desktop state, package versions
(including the native SSH backend) and desktop/console source hashes.
If run as root, it reads the managed desktop owner from root-owned configuration
and queries status as that account. When the orchestration CLI is absent, it
uses the installed desktop runtime directly.
It does not restart services or collect screenshots, password files, environment
dumps or secret configuration. Review the output before sharing; credential
redaction is best effort, and log text may contain private account or host names.

```bash
basaltw patch 192.168.1.50 agent --no-guacamole
```

Removal preserves the native package and desktop; it does not remove runtime
libraries or reset Unix passwords. Stopping browser access releases its control;
saved agent permission is preserved. A successful rerun
reconciles services and browser ingress. Setup refuses unmanaged service files.
It checks managed configuration before changing browser ingress during removal.

Validation covers configuration round trips, pinned binary integrity and package
selection, native builds in release CI, origin/identity/CSRF checks, attachment
replay and expiry,
protocol framing, view-only enforcement and cooperative takeover. Browser
rendering can be tested with a synthetic desktop; that alone does not establish
real XRDP reconnect, keyboard-layout, GPU-editor or native-client coexistence
behavior. Qualify those paths on a disposable desktop VM before relying on them
for interactive work.

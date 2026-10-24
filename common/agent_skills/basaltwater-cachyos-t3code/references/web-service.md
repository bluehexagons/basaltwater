# Managed CachyOS T3 web service

The `agent_cachyos --web-interface t3code` setup uses the current desktop account
and a dedicated user unit, `basaltwater-cachyos-t3.service`. It binds to
`127.0.0.1:3773` by default, or to the explicitly selected private IPv4 address
and port. It runs with the user session; setup does not enable lingering.
Firewall management requires an explicit access flag; see the workstation skill.

Inspect service state and recent logs as the user:

```fish
systemctl --user status basaltwater-cachyos-t3.service --no-pager
journalctl --user -u basaltwater-cachyos-t3.service -n 100 --no-pager
```

Managed web-service output is filtered before systemd records it, including
headless startup tokens, pairing URLs, and QR rows. The explicit `t3 pair`
command still returns a one-time credential to the local caller; do not save
its output in shared logs or support reports. The filter does not sanitize every
application or provider message; review and redact log excerpts before sharing.

The runtime is installed under `~/.local/share/basaltwater/cachyos-t3`, separate
from an existing T3 desktop installation. Its data is explicitly under that
runtime root's `data` directory. Older units used `~/.t3`; rerunning web setup
starts a separate environment and leaves the old data untouched. There is no
automatic history or credential migration. Provider CLIs must work in this account
and be authenticated through their normal local login. If provider discovery
fails, inspect the unit's PATH and the provider binary path in T3 settings.

To connect a browser or desktop client, run:

```fish
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" pair --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

Open the printed `Pairing URL` in the browser, or paste it into the desktop
client. Opening the bare localhost address redirects to T3's pairing page. This
profile binds to loopback by default. Pass `--web-interface-host` a private
IPv4 address during setup to allow clients on the trusted LAN. Add `--lan-access`
or `--access-source` to restrict UFW access, and arrange address stability.

For cloud access through T3 Connect, keep the default loopback bind and run the
following as the desktop user. Complete the browser sign-in, restart the
managed service, and check the saved link:

```fish
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" connect link --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
systemctl --user restart basaltwater-cachyos-t3.service
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" connect status --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

T3 Connect is separate from direct LAN pairing and does not require port
forwarding. Use `connect link` here instead of `connect`: the latter may offer
to install a second upstream `t3code.service`. Use `connect unlink` or
`connect logout` to disable it. The service follows the user session unless the
user deliberately enables systemd lingering.

Rerunning setup with T3 selected stages the latest runtime, validates its CLI,
native PTY shell, and unit, then stops and starts the service after selected
skills install. Saved threads can continue on a new message. Automatic
continuation of interrupted active turns depends on T3's existing **Continue
threads after server update** setting; queued turns still need user Resume, and
approvals may not survive. Finish active work first and retain the chosen host,
port, and workspace options on the command. Use
setup for updates; direct npm installs into the managed root bypass staging.
The stable `bin/t3` link remains the entry point for pairing and Connect.

Failed activation restores the previous runtime and unit, but does not reverse
application database migrations. Incomplete recovery retains private snapshots
in the runtime root's `.activation` directory; resolve the service error and
rerun setup to retry recovery. Preserve that directory until recovery completes.
If the unit or CLI link was changed or removed outside setup, recovery stops and
leaves the snapshots for manual inspection.
The current and previous managed releases are retained after successful updates.
HTTP UI reachability is not proof of a working provider thread; unknown HTTP
routes can return the frontend HTML. Test a thread and terminal in the client.

To stop the service persistently, use
`systemctl --user disable --now basaltwater-cachyos-t3.service`.
Omitting the web-interface flag on a later setup does not uninstall the service.

For deliberate updates and recovery, see the
[CachyOS maintenance reference](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-maintenance.md).

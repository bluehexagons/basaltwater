---
name: infra-tools-cachyos-t3code
description: Operate the optional local or private-LAN T3 Code user service and T3 Connect installed by the CachyOS coding profile.
metadata:
  managed-by: infra_tools
---

# Local T3 Code and T3 Connect

The `agent_cachyos --web-interface t3code` setup uses the current desktop account
and a dedicated user unit, `infra-tools-cachyos-t3.service`. It binds to
`127.0.0.1:3773` by default, or to the explicitly selected private IPv4 address
and port. It runs with the user session; setup does not enable lingering or
configure firewall rules.

Inspect service state and recent logs as the user:

```bash
systemctl --user status infra-tools-cachyos-t3.service
journalctl --user -u infra-tools-cachyos-t3.service -n 100
```

The runtime is installed under `~/.local/share/infra-tools/cachyos-t3`, separate
from an existing T3 desktop installation. Provider CLIs must work in this account
and be authenticated through their normal local login. If provider discovery
fails, inspect the unit's PATH and the provider binary path in T3 settings.

To connect a browser or desktop client, run:

```bash
"$HOME/.local/share/infra-tools/cachyos-t3/bin/t3" pair --base-dir "$HOME/.t3"
```

Open the printed `Pairing URL` in the browser, or paste it into the desktop
client. Opening the bare localhost address redirects to T3's pairing page. This
profile binds to loopback by default. Pass `--web-interface-host` a private
IPv4 address during setup to allow clients on the trusted LAN; firewall policy
and address stability remain the workstation owner's responsibility.

For cloud access through T3 Connect, keep the default loopback bind and run the
following as the desktop user. Complete the browser sign-in, restart the
managed service, and check the saved link:

```bash
"$HOME/.local/share/infra-tools/cachyos-t3/bin/t3" connect link --base-dir "$HOME/.t3"
systemctl --user restart infra-tools-cachyos-t3.service
"$HOME/.local/share/infra-tools/cachyos-t3/bin/t3" connect status --base-dir "$HOME/.t3"
```

T3 Connect is separate from direct LAN pairing and does not require port
forwarding. Use `connect link` here instead of `connect`: the latter may offer
to install a second upstream `t3code.service`. Use `connect unlink` or
`connect logout` to disable it. The service follows the user session unless the
user deliberately enables systemd lingering.

Rerunning setup with T3 selected stages the latest runtime, validates its CLI,
native PTY shell, and unit, then restarts the service. Finish active work first,
and retain the chosen host, port, and workspace options on the command. Use
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
`systemctl --user disable --now infra-tools-cachyos-t3.service`.
Omitting the web-interface flag on a later setup does not uninstall the service.

This profile does not install the VM gateway, device-pairing helpers, managed
Playwright, or KDE automation. Do not use VM-specific T3 repair/update commands
for this service. Read the [CachyOS operator guide](https://github.com/bluehexagons/infra_tools/blob/main/docs/CACHYOS.md)
for deliberate runtime updates (also in the installed checkout at
`~/.local/share/infra_tools/docs/CACHYOS.md`).

---
name: infra-tools-cachyos-t3code
description: Operate the optional localhost T3 Code user service installed by the CachyOS coding profile.
metadata:
  managed-by: infra_tools
---

# Local T3 Code

The `agent_cachyos --web-interface t3code` setup uses the current desktop account
and a dedicated user unit, `infra-tools-cachyos-t3.service`. It binds to
`127.0.0.1:3773`, unless another unprivileged port was selected. It runs with
the user session; setup does not enable lingering or remote exposure.

Inspect service state and recent logs as the user:

```bash
systemctl --user status infra-tools-cachyos-t3.service
journalctl --user -u infra-tools-cachyos-t3.service -n 100
```

The runtime is installed under `~/.local/share/infra-tools/cachyos-t3`, separate
from an existing T3 desktop installation. Provider CLIs must work in this account
and be authenticated through their normal local login. If provider discovery
fails, inspect the unit's PATH and the provider binary path in T3 settings.

Rerunning setup retains the installed runtime and starts the service. An explicit
configuration change may restart it; check for active work first. To stop it
persistently, use `systemctl --user disable --now infra-tools-cachyos-t3.service`.
Omitting the web-interface flag on a later setup does not uninstall the service.

This profile does not install the VM gateway, device-pairing helpers, managed
Playwright, or KDE automation. Do not use VM-specific T3 repair/update commands
for this service. Read the [CachyOS operator guide](https://github.com/bluehexagons/infra_tools/blob/main/docs/CACHYOS.md)
for deliberate runtime updates (also in the installed checkout at
`~/.local/share/infra_tools/docs/CACHYOS.md`).

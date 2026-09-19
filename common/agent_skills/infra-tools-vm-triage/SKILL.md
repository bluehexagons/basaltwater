---
name: infra-tools-vm-triage
description: Diagnose a Basaltwater agent VM that is slow, unhealthy, low on capacity, or needs a shareable support snapshot.
metadata:
  managed-by: infra_tools
---

# Agent VM triage

Start with stable, non-secret diagnostics:

```bash
basaltw agent doctor --capability host --json
```

Add `--capability t3code` when the VM was provisioned for T3 Code. Add explicit
`--tool` checks for the coding agents involved in the failure; the default tool
set may include tools that were intentionally not installed.

Interpret T3 service readiness separately from host warnings. Host warnings
cover memory, swap, disk headroom, bounded agent storage, maintenance timers,
and pending reboots. Only critical disk pressure or failed maintenance makes
the host capability unhealthy.

If the T3 service is unhealthy, inspect its user unit and bounded recent output:

```bash
systemctl --user status t3code.service --no-pager
journalctl --user -u t3code.service -n 100 --no-pager
```

When repair is in scope, `basaltw agent doctor --capability t3code --fix`
can configure GitHub's Git credential helper, rebuild an incomplete active T3
native runtime, enable the managed service at boot, and restart it if inactive.
It is a mutation, not an additional read-only diagnostic.

## Support snapshot

Print a redacted JSON snapshot:

```bash
basaltw agent support-bundle
```

Or save a new private file below the current user's home:

```bash
basaltw agent support-bundle --output ~/agent-support.json
```

The snapshot inventories optional browser configuration without launching a
browser. Add `--browser-smoke` only when Playwright was explicitly installed
and reproducing browser startup is relevant.

The snapshot contains tool versions, Boolean credential health, stable doctor
results, service resource counters, maintenance state, and aggregate T3 log
metadata. It omits credential contents, log contents, repository contents,
changed file names, prompts, sessions, Git identity, and user-home paths.
Review the JSON before sharing it. Do not attach raw agent credentials or
session files as supplemental evidence.

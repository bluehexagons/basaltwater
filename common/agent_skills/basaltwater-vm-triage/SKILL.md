---
name: basaltwater-vm-triage
description: Diagnose a Basaltwater agent VM that is slow, unhealthy, low on capacity, or needs a shareable support snapshot.
metadata:
  managed-by: basaltwater
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

For build/render capacity, inspect `workload_limits` or run
`basaltw agent resources --json`: affinity and inherited visible cgroup quotas
can be tighter than guest capacity. Missing counters are unknown. T3 memory
includes descendant agents/builds; a different service lifetime is not a leak
or improvement measurement. Choose concurrency from workload measurements,
memory and storage demand, rather than host CPU count alone.

Read current availability/pressure separately from historical service peaks.
Ballooned RAM can be smaller than the capacity present when a peak occurred;
peak time/capacity are unknown. `t3_service.memory_accounting` distinguishes
anonymous memory, file cache, limits and cumulative readable OOM events. A
single `oom_kill` counter or exit 137 does not identify a job or prove a leak.
Compare before/after snapshots with the same known service invocation and
`counter_epoch`, respecting local versus hierarchical event scope. Missing
counters stay unknown; no diagnosis changes limits or cleans caches.

When SDK/container copies fail for space, opt into
`basaltw agent doctor --capability host --container-storage --json` or
`basaltw agent containers inspect --engine podman --json`. Check graph-root
free space, including a separate home mount. Reclaimable estimates can overstate
shared-layer savings. Use `agent containers preflight --engine podman
--image-bytes N --json` with an estimated uncompressed image size and explicit
copy/reserve assumptions; it is not a guarantee. Diagnose without pruning.
Cleanup requires task ownership and separate authorization; preserve SDK caches
and all referenced workloads. Larger storage or migration is a separate action.
See [container headroom](https://github.com/bluehexagons/basaltwater/blob/main/docs/PROJECT_TOOLING.md#container-storage-and-sdk-headroom).

If the T3 service is unhealthy, inspect its user unit and bounded recent output:

```bash
systemctl --user status t3code.service --no-pager
journalctl --user -u t3code.service -n 100 --no-pager
```

When repair is in scope, `basaltw agent doctor --capability t3code --fix`
can configure GitHub's Git credential helper, rebuild an incomplete active T3
native runtime, enable the managed service at boot, and restart it if inactive.
It is a mutation, not an additional read-only diagnostic.

For unattended inspection, omit `--fix` and `--record` and print the support
snapshot instead of requesting an output file. If the sandbox blocks a service
socket, log, or diagnostic probe, report that evidence as unavailable; do not
escalate, restart a service, or use another tool to evade the restriction.

Native graphics startup can be checked explicitly with
`basaltw agent graphics doctor --context xvfb --json`. A healthy software GLX
context supports later correctness captures; it does not validate a rendered
frame, desktop, GPU timing, HDR or latency. Use the project's own capture checks
for rendering. Ordinary discovery/support never launches this probe.

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
Add `--container-storage` when container capacity is relevant; only aggregate
storage enters the bundle. Manifest/support provenance separates a recorded
setup snapshot from the observed running checkout; neither invents a receipt.

The snapshot contains tool versions, Boolean credential health, stable doctor
results, service resource counters, maintenance state, and aggregate T3 log
metadata. It omits credential contents, log contents, repository contents,
changed file names, prompts, sessions, Git identity, and user-home paths.
Review the JSON before sharing it. Do not attach raw agent credentials or
session files as supplemental evidence.

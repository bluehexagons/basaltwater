---
name: basaltwater-vm-triage
description: Diagnose an unhealthy or low-capacity agent VM and collect a redacted support snapshot.
metadata:
  managed-by: basaltwater
---

# VM triage

Use read-only diagnostics when a host problem is relevant:

```bash
basaltw agent doctor --capability host --json
```

Read [diagnostics and support evidence](references/diagnostics.md) to select
resource, service, graphics, container, or support checks. Avoid running every
optional probe on an ordinary development request.

For old session or project-data cleanup, read
[the storage cleanup reference](references/storage-cleanup.md) before
classifying or proposing retention.

Separate host capacity from current pressure and T3 service health. Missing
counters are unknown; historical peaks and exit codes alone do not prove leaks.
Compare like service invocations and counter epochs before claiming improvement.

Inspection does not authorize repairs, pruning, limits changes, service restarts,
or browser/graphics launches. Honor execution restrictions and preserve caches
and workloads. Share only reviewed redacted evidence, never credential/session files.

# Hardware acceptance

- Task, tester, observation time and intended acceptance criteria:
- Checkout, tested commit/dirty state and project command:
- OS/kernel, GPU PCI identity/name, driver, actual renderer/backend and tool versions:
- CPU model, allocated cores/vCPUs, available RAM, VM/WSL/native environment and known contention:
- Session/display, monitor geometry/scaling/refresh, streaming path when used:
- Electron/Chromium versions when applicable, launch flags, observed graphics feature/acceleration status:
- Workload/settings/seed, warmup and measured interval, sample count and frame-time results:
- Observed checks (criterion, action, result, command/exit/duration, evidence):
- Input/audio/controller/focus/fullscreen/reconnect observations when required:
- Proposed work / unresolved failures:
- Unverified cases and other platforms (with reason; do not infer a pass):
- Running or uncertain jobs, owning status/cleanup command and checkpoint:

Keep the full operational record and raw diagnostics in private state outside
Git. Tracked acceptance summaries retain relevant platform characteristics and
results, using repository-relative paths and omitting personal host identities,
endpoints, account paths and job handles. Sanitize selected evidence before inclusion.

Use the [platform routes](../../workstations/workflows.md) and its linked native
guidance. Keep game frame timing separate from streaming/encoding/client latency.
Record software-reference and representative target-hardware results separately;
worker elapsed time includes startup/cleanup and does not establish frame rate.

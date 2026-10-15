# Privilege execution qualification

Status: approved command execution, review-window policy, and redacted
maintenance status are implemented. Full refresh and recovery behavior still
needs qualification on a disposable Debian/systemd target. Repository tests
do not establish that live lifecycle.

The [privilege approval guide](../agents/privilege-approvals.md),
[broker reference](../agents/privilege-broker-reference.md), and
[project tooling guide](../agents/project-tooling.md) own current behavior.
This page contains only the remaining live acceptance gate.

## Qualification gate

Run each case on a disposable target using the exact candidate commit and
private job umask. Keep raw commands, logs, credentials, and recovery snapshots
outside the repository. Do not replay uncertain operations automatically.

| Case | Required result | Status |
| --- | --- | --- |
| Source activation | Preserve state links and prior data; keep code/readable receipt available while Git metadata and credentials remain private. | Not qualified |
| Setup failure | A pre-step or later-step failure retains prior successful setup, identifies only its own safe phase and stops dependent work. | Not qualified |
| Explicit recovery | Recovery uses the inspected operation identity, records a new attempt identity, and retains evidence until success. | Not qualified |
| Partial activation | Failed or interrupted source activation preserves or restores actual paths and leaves actionable recovery state without replaying setup. | Not qualified |
| Process and service interruption | Worker loss, broker restart, or a lost dispatch response cannot replay an uncertain request; durable job results survive service restart. | Not qualified |
| Approval and state access | Public approval origin, review policy, credential privacy, and TLS verification remain valid across refresh and recovery. | Not qualified |
| Workload and desktop lifecycle | Active builds, unpushed work, T3, and the shared desktop remain intact through the documented maintenance hold and readiness checks. | Not qualified |
| Process-tree bounds | Output, timeout, cancellation, and cleanup remain bounded when descendants hold pipes, ignore TERM, or replace inventoried paths. | Not qualified |

Complete this gate before claiming end-to-end refresh/recovery qualification.
Use the [release checklist](../release-qualification.md) for candidate-wide
installation and recovery coverage.

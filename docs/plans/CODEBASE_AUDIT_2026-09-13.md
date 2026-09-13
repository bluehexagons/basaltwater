# Open Codebase Audit Findings (2026-09-13)

This continues the [2026-08-21 audit](CODEBASE_AUDIT_2026-08-21.md) and tracks
remaining work only. Resolved findings and implementation history are retained
in Git. Original finding IDs remain stable; gaps indicate resolved findings.

Scope: installation and setup, state integrity, CI/CD, storage, Proxmox,
internal web, and command lifecycles. Automated verification uses mocked system
calls and temporary directories; live deployment qualification is separate.

## P1 — reliability, privileged setup, and operator safety

- **RCF-04 — Medium-High: service replacement is destructive or
  non-atomic.** Several writers delete the existing unit before recreating it;
  others overwrite the live unit with `open(..., 'w')`. A write, reload, or
  start failure can therefore leave the service absent or truncated, without
  restoring prior active/enabled state. Evidence:
  [`cicd_steps.py`](../../web/cicd_steps.py),
  [`gogs_steps.py`](../../web/gogs_steps.py),
  [`systemd_service.py`](../../lib/systemd_service.py),
  [`service_manager.py`](../../lib/service_manager.py),
  [`storage_ops_steps.py`](../../sync/storage_ops_steps.py).
  **Acceptance:** render/validate a same-directory temporary file, atomically
  replace it, preserve the prior unit until activation succeeds, and
  fault-test each failure boundary.

- **RCF-05 — Medium-High: remote setup bypasses bounded
  subprocess execution.** Local and SSH branches use `Popen` followed by
  unbounded `wait()`, unlike the shared runner with descendant cleanup.
  Evidence: [`setup_common.py`](../../lib/setup_common.py),
  [`remote_utils.py`](../../lib/remote_utils.py). **Acceptance:**
  add a setup-specific deadline, process-group termination, streamed output,
  and tests for hangs and descendants holding pipes.

- **RCF-10 — Medium: CI/CD service timeout is shorter than a
  valid pipeline.** `TimeoutStartSec=2h`, but up to four sequential stages can
  each run for one hour. Evidence:
  [`cicd_steps.py`](../../web/cicd_steps.py),
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py).
  **Acceptance:** choose one total-job deadline, pass remaining time to
  stages, align systemd, and report whether termination occurred before or
  during deployment.

- **RCF-12 — Medium: secret payload cleanup is not crash-resistant.**
  Remote agent/pairing/web-panel payloads are removed only by normal `finally`
  paths. Hard kill or power loss can leave credentials under
  `/opt/infra_tools`. Evidence: [`remote_setup.py`](../../remote_setup.py).
  **Acceptance:** use a
  restrictive temporary location, record expiry/owner, scrub stale payloads
  at startup, and test interruption without exposing secret contents.

- **RCF-20 — Medium-High: rolling Proxmox updates lack bounded SSH
  execution and resumable checkpoints.** `_ssh_result` calls
  `subprocess.run` without a timeout; updates then proceed node by node, so a
  later failure leaves earlier nodes changed and later nodes skipped. Evidence:
  [`cluster_update.py`](../../lib/cluster_update.py). **Acceptance:**
  bound each SSH operation, persist per-target phase/result, make resume and
  stop-after-failure explicit, and apply the existing Proxmox maintenance plan's
  HA/Ceph/evacuation policy before mutation.

## P2 — policy and lower-probability operational concerns

- **RCF-14 — Medium: third-party installers use rolling
  network-shell trust.** Codex/Claude/OpenCode, CachyOS, uv, nvm, and the
  Codex updater execute downloaded content; the updater records a digest but
  does not compare it to a pinned expected value. Evidence:
  [`agent_steps.py`](../../common/agent_steps.py),
  [`cachyos_steps.py`](../../common/cachyos_steps.py),
  [`agent_cli.py`](../../lib/agent_cli.py),
  [`common_steps.py`](../../common/common_steps.py). **Acceptance:**
  select signed releases, a maintained digest manifest, or an explicitly
  accepted rolling channel per tool; expose and retain provenance.

- **RCF-18 — High under repository compromise, architecture
  risk: CI/CD scripts remain a trust boundary.** Repository-authored scripts
  execute as `webhook` and can stream deploy commands to an app server. HMAC
  protects ingress, not a compromised repository/configuration or an
  over-privileged deploy key. Evidence:
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py).
  **Acceptance:** separate build/deploy credentials and minimize remote sudo;
  retain protected-branch policy, checkout confinement, and the documented
  trust contract.

## Delivery and ownership

- Prioritize recoverable service/setup execution (RCF-04–05), and CI/CD
  deadlines and credential cleanup (RCF-10, 12).
- RCF-05 uses the shared process contracts in
  [Transactional execution](TRANSACTIONAL_EXECUTION.md).
- RCF-10 and 18 belong with [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md);
  RCF-12 complements [Deploy secrets](DEPLOY_SECRETS.md).
  Script confinement and the CI/CD trust contract are already implemented;
  RCF-18 remains open for build/deploy credential isolation.
- RCF-20 follows the [Proxmox maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md),
  which owns HA/Ceph, evacuation, and live qualification.
- Close findings after focused failure-path tests, relevant operator
  documentation, and `make check`/`git diff --check` pass. Remove resolved
  entries instead of appending progress notes.

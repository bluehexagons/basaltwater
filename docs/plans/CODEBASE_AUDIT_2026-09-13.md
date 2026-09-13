# Open Codebase Audit Findings (2026-09-13)

This continues the [2026-08-21 audit](CODEBASE_AUDIT_2026-08-21.md) and tracks
remaining work only. Resolved findings and implementation history are retained
in Git. Original finding IDs remain stable; gaps indicate resolved findings.

Scope: installation and setup, state integrity, CI/CD, storage, Proxmox,
internal web, and command lifecycles. Automated verification uses mocked system
calls and temporary directories; live deployment qualification is separate.

## P1 — reliability, privileged setup, and operator safety

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

- RCF-18 belongs with [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md).
  Script confinement and the CI/CD trust contract are already implemented;
  RCF-18 remains open for build/deploy credential isolation.
- RCF-20 follows the [Proxmox maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md),
  which owns HA/Ceph, evacuation, and live qualification.
- Close findings after focused failure-path tests, relevant operator
  documentation, and `make check`/`git diff --check` pass. Remove resolved
  entries instead of appending progress notes.

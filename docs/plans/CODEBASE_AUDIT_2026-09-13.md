# Open Codebase Audit Findings (2026-09-13)

This continues the [2026-08-21 audit](CODEBASE_AUDIT_2026-08-21.md) and tracks
remaining work only. Resolved findings and implementation history are retained
in Git. Original finding IDs remain stable; gaps indicate resolved findings.

Scope: installation and setup, state integrity, CI/CD, storage, Proxmox,
internal web, and command lifecycles. Automated verification uses mocked system
calls and temporary directories; live deployment qualification is separate.

## P2 — policy and lower-probability operational concerns

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
- Close findings after focused failure-path tests, relevant operator
  documentation, and `make check`/`git diff --check` pass. Remove resolved
  entries instead of appending progress notes.

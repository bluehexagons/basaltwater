---
name: infra-tools-deploy-smoke
description: Prepare and verify a test deployment from a Basaltwater-managed coding VM.
metadata:
  managed-by: infra_tools
---

# Deployment smoke checks

Use this workflow to prepare and verify the requested test deployment. Run the
application's required checks; run Basaltwater tests when infrastructure code
changes are part of the task.

## Preflight

Identify the exact revision or build artifact to deploy. Inspect each checkout
involved and preserve unrelated changes; use an isolated worktree if the build
would otherwise include them. Keep application and infrastructure changes in
their owning repositories:

```bash
git status --short --branch
git log -1 --oneline
basaltw agent doctor --capability host --json
```

Check `--capability t3code` separately when the deployment workflow actually
depends on the managed T3 service. Do not make T3 readiness a prerequisite for
an SSH-only agent VM.

Inspect the application's deployment manifest and the saved Basaltwater setup
before mutation. Use the repository's documented test command and deployment
command; do not invent production secrets, hostnames, or database paths. Run a
dry run when the selected Basaltwater command provides one.

## Verify the deployed service

Check the service manager, loopback health endpoint, and public HTTPS endpoint
as separate layers. Use the capability-specific browser skill installed on the
VM. Prefer managed Playwright for repeatable VM-origin checks when live
collaboration is unnecessary; prefer T3 preview when shared client-visible
evidence or participation matters. Before using managed Playwright, run:

```bash
basaltw agent doctor --capability browser
```

If only T3 preview is installed and the application is closed or its client
does not trust the VM certificate, skip that browser layer and continue with
the remaining deployment checks. Do not make client certificate enrollment a
deployment prerequisite.

Capture the deployed commit identifier, HTTP status, and a small set of
user-visible smoke interactions. Never dump response bodies that may contain
credentials or private user data. For a live development preview, use the
`infra-tools-web-gateway` skill instead of opening a direct firewall port.

## Failure handoff

On failure, preserve the last known-good release and collect:

```bash
basaltw agent support-bundle --output ~/agent-support.json
```

Add only bounded service logs relevant to the failed deployment after
reviewing them for secrets. Do not reset a dirty checkout, delete an unmerged
worktree, rotate credentials, or retry a stateful migration destructively
without explicit authorization.

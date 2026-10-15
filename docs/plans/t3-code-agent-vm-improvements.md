# T3 Code VM qualification

Status: stable T3 v0.0.45 is the deployment target. Current setup, service,
authentication, maintenance, workspace, and skill behavior is documented in
the [T3 Code guide](../agents/t3-code.md),
[project tooling](../agents/project-tooling.md), and
[maintenance guide](../operations/maintenance.md). No known Basaltwater
qualification result should be inferred from the retired host-specific notes.

## Remaining qualification

| Area | Current boundary | Required next check |
| --- | --- | --- |
| Provider setup | Codex, Claude Code, and OpenCode use the managed capability catalog; Claude Playwright registration is not enabled. | Rerun setup on a disposable Debian VM with Claude-only and mixed providers, restart the agent session, and verify refreshed skill discovery. |
| Preview access | The private HTTPS gateway is the supported shared path; upstream native port navigation and public relays are separate boundaries. | With an attached desktop client, check direct local/private environment-port rendering and relay behavior without weakening gateway TLS checks. |
| Service readiness | Readiness is recorded explicitly after deliberate updates and reboot; an automatic post-boot audit is not enabled. | Complete the disposable-VM service-lifetime test before adding automatic post-boot work. |
| T3 V2 migration | The deployed stable target is v0.0.45; V2 migration was inspected only with a nightly artifact. | When V2 becomes stable, test the published forward migration with a matching client, archived threads, attachments, preview, and provider continuation. |

Keep unattended scheduling with upstream T3 task behavior and Basaltwater's
existing maintenance holds. Do not add another task scheduler. Evaluate Claude
browser registration only when a concrete standalone Claude workflow needs it.

Android setup remains deferred. Potential Android and operator-provided Mac
workflows are scoped in the [mobile proposal](mobile-agent-support.md); this
plan does not enable device setup or agent access.

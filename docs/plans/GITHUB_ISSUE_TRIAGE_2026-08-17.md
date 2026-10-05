# GitHub issue triage (reverified 2026-10-05)

Status: current map of the 14 open issues against `main` on 2026-10-05.
Repository implementation, unit tests, and live-host qualification are separate
states. The [planning index](README.md) and [roadmap](ROADMAP.md) own priority;
this page records issue scope and evidence.

## Immediate defects

| Issue | Current evidence | Next check |
| --- | --- | --- |
| [#103 — Incorrect certificate message](https://github.com/bluehexagons/basaltwater/issues/103) | The CA command previously treated a missing configured CA file as proof of public trust. The implementation now verifies the live endpoint with the configured CA or public trust store and reports unknown trust when the CA is missing, TLS fails, or a purported public endpoint still chains to the VM-local CA. | Run on the affected self-signed host after deployment; confirm the panel shows accurate CA instructions or an explicit unverified state. |
| [#102 — “ausearch is unavailable”](https://github.com/bluehexagons/basaltwater/issues/102) | The reported host should have audit coverage. A restricted PATH can hide packaged `/usr/sbin/ausearch`; the exporter and security monitor now use system audit paths. VM/hardware setup now fails if auditd cannot start or load rules, and the required-profile monitor checks daemon and rule health before reporting a clean scan. The current agent VM has active auditd and a healthy snapshot. | Deploy to the affected host and verify `auditctl -s`, loaded managed rules, a fresh healthy panel snapshot, and security-monitor collection. |
| [#108 — Oversized security event backlogs](https://github.com/bluehexagons/basaltwater/issues/108) | Audit and SSH collection cap command output at 2 MiB and retain the cursor on failure. An oversized interval can therefore fail repeatedly without progress; this is separate from #102's tool discovery and audit coverage. | Add complete bounded pagination or interval splitting, advance only through fully parsed intervals, deduplicate boundaries, and expose irrecoverable gaps. Qualify high-volume, restart, timeout and repeated-failure behavior with mocked commands. |

## Release and live qualification

| Issue | Current evidence | Remaining work |
| --- | --- | --- |
| [#104 — New stable release](https://github.com/bluehexagons/basaltwater/issues/104) | `v0.2.0` is the latest published GitHub release. The Basaltwater v2 namespace and migration are in `main`, with mocked tests and one post-setup Debian agent VM audit. | Complete the disposable-host, migration/recovery, storage, Proxmox, and CI/CD qualification in [release checklist](../BASALTWATER_RELEASE.md), then tag and publish `v2.0.0`. |
| [#106 — CachyOS Software](https://github.com/bluehexagons/basaltwater/issues/106) | All twelve requested application selections are implemented with saved refresh selection, doctor checks, documentation, and mocked tests. See [software matrix](../CACHYOS_SOFTWARE.md). | Run the documented live AUR, GUI/GPU, streaming, flashing, and authenticated publishing checks on an appropriate CachyOS machine; package presence is not workflow qualification. |

## Partial implementation

| Issue | Current slice | Remaining work |
| --- | --- | --- |
| [#91 — Interactive orchestration](https://github.com/bluehexagons/basaltwater/issues/91) | Interactive Proxmox shell supports registered-host and guest operations. | Create, run, and monitor setup jobs using the existing parser, workspace, and transactional execution; write a project brief after P0. |
| [#63 — Secrets/credential management](https://github.com/bluehexagons/basaltwater/issues/63) | Validated, private workspace credentials exist for selected services. | Add versioned deployment secret references, optional components, rotation and consumer inventory through [deploy secrets](DEPLOY_SECRETS.md), after P0 recovery safeguards. |
| [#28 — Scheduling system](https://github.com/bluehexagons/basaltwater/issues/28) | Storage sync/scrub share an hourly timer, per-spec cadence, atomic state, and an operation lock. The panel also runs saved Codex tasks serially on elapsed schedules with catch-up and failure pausing. | Fleet coordination, maintenance windows, calendar/timezone policy, budgets and resource ordering remain proposed in the [background agent brief](BACKGROUND_AGENT_CAPABILITIES.md); retain the existing storage job owner. |
| [#25 — Multimedia packages](https://github.com/bluehexagons/basaltwater/issues/25) | Debian and CachyOS provide native game tooling with `--game-dev`, individual media editors and `--av-tools`; CachyOS also includes `--obs`. Debian retains managed Godot bundles. | The requested broader `--gamedev`, `--image-editing`, `--video-editing`, `--audio-editing`, `--codecs`, and `--multimedia` bundles are not implemented under those names. The narrower `--game-dev` is host tooling, not a combined game engine/media bundle. Resolve remaining profile scope and package ownership before scheduling. |

## Proposals and unscheduled backlog

| Issue | Disposition | Decision needed |
| --- | --- | --- |
| [#107 — Core system agent](https://github.com/bluehexagons/basaltwater/issues/107) | Panel host/data task preparation and Codex prompt execution exist; a persistent fleet managing agent and agent chat interface remain proposed. | Define fleet authority, evidence, operator interface, and dependency on the [background agent brief](BACKGROUND_AGENT_CAPABILITIES.md) and audit contracts. |
| [#105 — Agent automation systems](https://github.com/bluehexagons/basaltwater/issues/105) | The [panel runner](../WEB_PANEL.md#agent-prompt-tasks) executes bounded, saved and scheduled Codex prompts without T3 Code. | Fixed daily operations briefs, durable fleet evidence, provider token/spend budgets and incident/recovery recipes remain proposed; retain existing maintenance/storage schedule owners. |
| [#100 — Akaunting support](https://github.com/bluehexagons/basaltwater/issues/100) | Not implemented. [Coolify evaluation](COOLIFY_INTEGRATION.md) is deferred and does not establish application support. | Choose a specific application version and optional isolated deployment owner, then qualify initialization, upgrades, business checks, backups, and restore. |
| [#87 — More default config options](https://github.com/bluehexagons/basaltwater/issues/87) | Current profiles select installation choices; panel position and widgets are not modeled. | Define a versioned desktop-preference owner and user override policy only for a supported desktop profile. |
| [#83 — Apt cache support](https://github.com/bluehexagons/basaltwater/issues/83) | APT cache *cleanup* exists, but no cache server or client proxy configuration does. | Specify server package/service, validated endpoint, client configuration, failure behavior, and rollback. |

## Triage policy

- Close an issue when its requested operator behavior is implemented, documented,
  tested where practical, and any required live qualification is recorded.
- Keep partial features and proposed designs open with their remaining acceptance
  work stated on the GitHub issue. Do not treat a plan or a package query as a
  working live workflow.
- The 2026-08-24 reconciliation closed #79, #81, #90, #92, and #93 with
  implementation evidence. #38, #58, #85, and #97 were subsequently closed;
  their broader residual themes remain in the roadmap, not in this open count.
- When implementation changes, update the owning plan and operator docs, then
  refresh this map and the corresponding GitHub issue.

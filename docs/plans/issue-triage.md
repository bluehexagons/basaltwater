# Open GitHub issue triage

Status: the 14 open issues were checked against GitHub on 2026-10-15.
This map records each issue's current scope and next acceptance step; project
priority remains in the [planning index](README.md) and [roadmap](roadmap.md).

## Active monitoring

| Issue | Current state | Remaining acceptance |
| --- | --- | --- |
| [#108 — Recover security-monitor scans from oversized event backlogs](https://github.com/bluehexagons/basaltwater/issues/108) | Audit and SSH output is streamed and bounded; scans cover a complete 15-minute interval and advance the cursor only after all sources succeed. | Detect when source retention has passed an old cursor and report the missing interval. Qualify catch-up against retained logs. |

## Release and platform support

| Issue | Current state | Remaining acceptance |
| --- | --- | --- |
| [#104 — New stable release](https://github.com/bluehexagons/basaltwater/issues/104) | v0.2.0 is the latest final release. Package metadata declares 2.0.0rc1; no v2.0.0-rc.1 tag is published. | Publish an opt-in candidate after its gate, then complete the install, upgrade, rename, security, storage, Proxmox, HomeBox, and CI/CD matrix before stable v2.0.0. |
| [#106 — CachyOS Software](https://github.com/bluehexagons/basaltwater/issues/106) | The requested application selections are documented and implemented. | No more CachyOS enhancements are in the current release pass; the experimental profile is separate from Debian release qualification. |

## Partial implementation

| Issue | Current state | Remaining scope |
| --- | --- | --- |
| [#91 — Interactive orchestration](https://github.com/bluehexagons/basaltwater/issues/91) | Interactive Proxmox host and guest operations are available. | Create, run, and monitor setup jobs using existing parsing, workspaces, and transaction recovery. |
| [#63 — Secrets and credential management](https://github.com/bluehexagons/basaltwater/issues/63) | Private workspace credentials exist for selected services. | Add versioned deployment secret references, rotation, optional components, and consumer inventory after P0. |
| [#28 — Scheduling system](https://github.com/bluehexagons/basaltwater/issues/28) | Storage jobs and saved Codex panel tasks have separate schedules and owners. | Fleet coordination, maintenance windows, calendar policy, budgets, and resource ordering remain unscheduled. |
| [#25 — Multimedia packages](https://github.com/bluehexagons/basaltwater/issues/25) | Native game tooling and individual media editors are available; Debian has managed Godot bundles. | Decide whether to add the broader requested bundles and define their profile/package ownership. |

## Unscheduled proposals

| Issue | Current state | Decision needed |
| --- | --- | --- |
| [#107 — Core system agent](https://github.com/bluehexagons/basaltwater/issues/107) | Panel host/data task preparation and Codex prompt execution exist; a fleet manager and agent chat are not implemented. | Define fleet authority, evidence, operator interface, and dependencies. |
| [#105 — Agent automation systems](https://github.com/bluehexagons/basaltwater/issues/105) | Bounded, saved, scheduled Codex tasks run in the panel. | Define fixed recipes, durable fleet evidence, provider budgets, and incident/recovery workflows. |
| [#100 — Akaunting support](https://github.com/bluehexagons/basaltwater/issues/100) | Not implemented; Coolify evaluation is deferred. | Select a concrete application version and isolated deployment/recovery owner. |
| [#87 — More default config options](https://github.com/bluehexagons/basaltwater/issues/87) | Setup profiles select installation choices; panel position and widgets are not modeled. | Define versioned desktop preferences and user override policy for a supported desktop. |
| [#83 — Apt cache support](https://github.com/bluehexagons/basaltwater/issues/83) | APT cache cleanup exists; no cache server or client proxy is configured. | Specify server ownership, validated endpoint, failure behavior, and rollback. |

## Reports needing more detail

| Issue | Current evidence | Next step |
| --- | --- | --- |
| [#111 — Certificate installation script fails on Arch](https://github.com/bluehexagons/basaltwater/issues/111) | The report says it is untested on other systems and has no command, host details, or reproduction. | Request the exact command and certificate source; reproduce on the named supported system before changing support claims. |
| [#112 — Hide UI for tools that are not installed](https://github.com/bluehexagons/basaltwater/issues/112) | The issue has no description or acceptance details. | Clarify which tools/panels should be hidden, how installation state is determined, and the expected unavailable state. |

## Triage policy

- Close an issue when the requested behavior is implemented and documented,
  practical tests pass, and any required live qualification is recorded.
- Keep partial features open with concrete acceptance work in the issue.
  A plan or package query is not evidence of a working live workflow.
- Update the owning guide or project plan, this map, and the GitHub issue when
  current support or acceptance status changes.

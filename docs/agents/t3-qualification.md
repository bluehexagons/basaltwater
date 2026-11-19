# Release compatibility and qualification

[Documentation index](t3-code.md)

## Stable feature baseline

As checked on **2026-11-19 UTC**, GitHub's latest stable release is **0.1.10**.
Check AUR/WinGet availability and the installed desktop version separately. It retains
the stable Orchestrator V2
introduced in 0.1.0 and advances the service launcher to protocol 4, with
protocol 3 launchers still supported for remote updates. Keep the
server and every connected desktop client on compatible V2 releases; mobile
requires the 2.0 app. See the [upstream release notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.10)
and [V2 migration notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.0).

The release's recommended provider versions are Codex **0.159+**, Claude Code
**2.1.301+**, Grok **1.0.13+**, OpenCode 2 **2.0.18+**, and Pi **1.0+**.
The [provider compatibility manifest](https://github.com/pingdotgg/t3code/blob/v0.1.10/apps/server/src/provider/model-manifest.json)
also marks Codex 0.156+, Claude Code 2.1.280+ and Pi 0.84.3+ supported; distinguish recommended
versions from minimum support. Inspect **Settings → Providers** for the selected account,
binary, version, and readiness. Setup manages only selected Basaltwater
providers; T3's additional Pi, Muse Code, Cursor SDK, and ACP Registry choices
do not imply that Basaltwater installed, authenticated, or qualified them.
Antigravity recommends **1.5.1 to below 2**, retaining selected older versions;
0.1.5 adds Claude/GPT model discovery and thinking controls to that integration.
This does not qualify it as a Basaltwater-managed provider.

On a host with Codex 0.169.1 and T3 0.1.10, the live
`orchestrator_capabilities` catalog showed `gpt-6.2-sol` and `gpt-6.2-astra`
on November 17. Both advertised `low` through `xhigh` alongside higher-cost
options. The bundled T3 manifest
predates these IDs. Use the selected environment and provider instance's live
catalog to check which models it lists. This check exercised model discovery only.
See [model selection](agent-subagents.md) for Standard service and context
guidance.

A T3 session on November 19 exposed `gpt-6-luna` with medium reasoning and
Standard service. That setting ran bounded documentation review workers.
This verifies availability for that session. Check the selected environment
again before assigning work.

### Agent workflow integration

The [0.1.10 integration](t3-releases/0.1.10.md)
records the protocol-4 review and disposable fresh/0.1.9-upgrade checks. HTTP
startup, embedded native PTY, administrative pairing/revocation, idle detection,
signal forwarding and SQLite integrity passed; migrations advanced from 81 to
96. Live service lifecycle and native-client workflows remain unqualified.
Earlier component evidence remains in the [0.1.9](t3-releases/0.1.9.md)
and [0.1.8](t3-releases/0.1.8.md) integration records. These operator records
live outside the installed skill bundles. Component evidence does not
establish end-to-end qualification.

Use the relevant shared reference instead of copying upstream UI/tool details:

| Work | Integration boundary |
| --- | --- |
| Delegation, linked environments and worktrees | Follow injected session instructions and the [orchestration reference](../../common/agent_skills/basaltwater-subagents/references/t3-orchestration.md). T3 owns thread/worktree binding; shell directory changes do not rebind a thread. Linked environments require deliberate enrollment and apply caller/peer access ceilings. |
| Schedules and PR monitoring | Use [native scheduling](../../common/agent_skills/basaltwater-subagents/references/t3-scheduling.md) and PR watches for T3-owned work. Independent Basaltwater tasks do not inherit T3 tools, preview or conversation. Choose one scheduler per job. |
| Goals, resume, project actions and visual replies | Use the [shared workflow](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md). T3 supports goals for Codex/Claude/OpenCode and native stopped-thread continuation. Keep project commands canonical and inspect settle actions before cleanup. |
| Pairing, Connect device approval and outside agents | Use [operations](../../common/agent_skills/basaltwater-t3code/references/operations.md) and upstream [device approval](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/remote-access.md#approve-new-devices). Setup does not enable public access, enroll outside agents or change owner approval settings. |
| Additional providers and guest collaboration | Review [ACP Registry and local commands](../../common/agent_skills/basaltwater-t3code/references/operations.md#additional-providers) for provider ownership, or [guest links](../../common/agent_skills/basaltwater-t3code/references/operations.md#guest-links) for owner-approved collaboration on one thread. |
| Updates, policy and service recovery | Use [managed updates](../../common/agent_skills/basaltwater-t3code/references/updates.md). Preserve downgrade guards, administrator policy and selected provider/package ownership. |

OpenCode 2 uses the separate `@opencode/cli` package and converts its shared
database on first run. A T3 update does not migrate Basaltwater's managed
OpenCode installation. Qualify that provider migration separately with a private
recovery copy; native goals require OpenCode 2.2.5+.

T3 owns its agent cgroup/resource priorities and supported keep-awake behavior.
For deliberate Debian memory protection, use upstream `t3 service protect`
only within an authorized host-tuning operation; it may require sudo and a
service restart. It targets the upstream service, not CachyOS's separate web
unit or desktop. See [upstream service protection](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/background-service.md#keep-the-server-responsive-when-agents-use-all-memory).

Native Windows 0.1.10 adds an upstream background service. Basaltwater continues
to manage the packaged desktop; read the [platform boundary](../../common/agent_skills/basaltwater-project-development/references/t3-platforms.md#windows-background-service)
before a deliberate migration. Setup does not create a second service.

Refresh delivers shared skills through each platform's existing bundles.
Updating this repository does not refresh other machines or reload an active
provider session. Outside setup, finish the current turn and use **Restart
agent session** to reload one thread without restarting the whole server.

### Feature ownership and reduction candidates

Use T3 as the owner of coding-session behavior and Basaltwater as the owner of
machine preparation, host operations and recovery. Existing independent tasks,
worktrees and history remain supported; this review does not migrate or delete
them. The web panel now links to T3’s discovered HTTPS workspace, removes its duplicate
updater, and limits its starter catalog to host operations. Existing saved tasks
remain supported. Follow the [panel handoff and schedule migration](../services/web-panel.md#t3-code-workspace).
Consider the remaining reductions below when evolving the integration:

| Area | Preferred owner and next step | Retain in Basaltwater |
| --- | --- | --- |
| Interactive goals and continuation | T3's native Codex/Claude/OpenCode goals and Resume; avoid a second continuation loop around a T3 thread | Independent Codex tasks with lifetime limits, checkpoints and receipts |
| Ordinary prompts, schedules and PR monitoring | T3 schedules/webhooks and native PR watches; the panel repository starters have been removed | Host jobs that must operate independently of T3 and fixed storage operations |
| Worktrees, scratch, captures and logs | T3's opt-in retention for its own data; native numbered-log expiry has no aggregate byte cap, so it does not fully replace Basaltwater cleanup | Basaltwater-owned worktrees, the 256 MiB rotated-log cap, runtime rollback protection, credential redaction, host pressure inventory and recovery |
| Pairing and remote access | T3's native account linking, relay controls and Connect device approval; the duplicate portal console and root restart bridge have been removed | Direct/private-LAN enrollment, gateway TLS and source restrictions, other hosted services |
| Browser and power management | T3 preview and native keep-awake behavior for T3 work | Explicit terminal-agent Playwright, application test suites, desktop access and host readiness |
| Updates and client documentation | T3's updater and tagged manuals; the panel updater has been removed | Initial installation, package ownership, setup reconciliation, security policy and failed-start recovery |

Before retiring a duplicate, inventory its users and saved state, establish the
replacement's required controls, and supply migration/rollback instructions.
In particular, T3 goals do not establish Basaltwater's lifetime attempt/deadline/
token accounting, and Connect device approval is not a root-execution broker.
Do not enable cleanup, change access policy, or install a Windows service as
part of an ordinary integration refresh.

### Stable feature qualification

The shared [client workflow reference](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md#shared-0110-client-workflows)
ships in the existing project-development bundle on Debian, CachyOS, native
Windows, and Ubuntu WSL. The shared scheduling reference also covers 0.1.7's
one-time triggers and 0.1.8's fresh-session mode. No additional skill catalog entry is needed.

On Debian, rerun the saved setup or use `sudo basaltw refresh` to reconcile
stable T3 and deliver updated skills; inspect the independent refresh and
completion results, then run the T3/host doctor and verify a provider turn and
terminal. The upstream unit supplies 0.1.6's resource priorities. Service
protection remains an explicit host-tuning operation, separate from refresh.

Native Windows refresh delivers the same shared workflows through its existing
bundle catalog; WinGet availability and the running desktop version must be
checked separately. Ubuntu WSL refresh must advance the pinned Basaltwater
source to refresh Linux guidance, independently of the Windows client update.
See [Windows refresh](../workstations/windows.md#review-selections-from-a-checkout)
and [WSL source refresh](../workstations/windows-wsl.md#manage-and-recover).
These repository changes do not remotely refresh other machines or establish
native Windows/WSL or Debian systemd lifecycle qualification.

These are required live checks, **not completed qualification evidence**. Run
them on disposable systems with the exact candidate commit and compatible stable
client/server. Record the provider executable/version and model, runtime and
permission modes, service versus desktop environment, checked result, and any
blocked step. Mocked tests and service HTTP readiness cannot qualify these paths.

| Case | Required evidence |
| --- | --- |
| Providers and migration | Fresh 0.1.10 setup, 0.1.9-to-0.1.10 upgrade, and V1-to-V2 copy migration with matching client, selected Codex/Claude combinations, private recovery copy, pairing permissions, authenticated provider turn, and terminal. Qualify OpenCode 2 separately before claiming support. |
| 0.1.10 workflows | Qualify protocol-3 launcher remote update and protocol-4 local reconciliation, native goals/stop/resume per provider, native continuation without an extra prompt, Connect device approval/revocation and older-client refusal, Windows service login/logout and remote updates, power inhibition/release, and opt-in worktree/scratch/capture/log retention with ignored-file protection. |
| 0.1.6 workflows | Side-chat permission modes and idle Bring back, project pin/archive/unarchive across connected checkouts, local/merge commit review, optional 1Password resolution with missing/locked CLI failures, and server/agent priorities under pressure. Test service protection/undo separately on a disposable Debian host. |
| 0.1.7 workflows | Parent-visible local/linked-child approvals reach the correct request; copied thread links open only for authorized recipients; one-time schedules respect offsets and do not repeat; usage filters apply before model mapping per environment; model/effort markers match changes; tagged-thread hiding does not stop work. Test managed-policy defaults/locks, cap enforcement, invalid-policy startup refusal and loaded-policy freshness on disposable hosts. Test direct preview-open targets only when exposed. |
| 0.1.8 workflows | Verify `/clear` resets provider context while retaining visible history, and Restart agent session reloads guidance without that reset. Check inherited first-reply side-chat settings, fresh-session schedules and two-save task moves, existing schedule migration/run history, private secret-card/reference/file delivery with synthetic secrets and failure/cancellation paths, API generator routing/billing consent, HTML file input, delayed child-result wakeups and actual model reporting. Check `service.env` precedence, custom base-dir retention, offline Reclaim/disk refusal, and opt-in `stopOnSettle` with automatic settlement. Qualify outside-agent wizard grants/revocation and matching mobile/desktop clients separately. |
| 0.1.9 workflows | Verify Connect account approval, environment-local code/browser fallback, selected grants and revocation. Check ordinary send during uploads plus failure/reload recovery and question/approval attachment blocking. Verify Claude MCP session grants and fresh-session behavior, OpenCode 2.2.5+ goal status/pause/resume/clear and cross-client synchronization, anchored PR links, an explicitly requested GitLab revert, background command results and screen-reader announcements. Qualify provider status checks without mise downloads and safe default-branch PR creation separately. |
| Project promotion and release tracks | Promote a stopped No project folder in place; retain its files, all associated threads and schedules, then initialize Git. Exercise a deliberate Stable/Nightly switch on disposable installations, including offline/unsupported machines, incompatible or unreadable databases, and an incomplete switch whose requested track differs from its running version. Preserve database compatibility refusals. |
| Delegation | Same-provider and cross-provider child results, explicit model/options, fresh review rounds, async wake, nested completion state, pending question, cancellation, and restart interruption without duplicated work. |
| Linked environments | On deliberately linked disposable peers, verify beta gates on both sides, server reachability/trust, read-only and narrower-mode denials, repository matching, missing/ambiguous project selection, remote result/restart/cancel, rejected worktree isolation/attachments, thread links, launch retry deduplication, expiry and peer revocation. Confirm forgetting a link does not claim to stop remote edits. |
| Workspace/context | Child worktree starts from committed local base; blocking setup completes before the first turn, while async setup is checked before dependent work. Uncommitted edits stay in parent. Explicit launch/handoff binding works, historical context stays distinguishable from native tool state, and cleanup/settle actions preserve unrelated work. |
| Queues and limits | Queue order and attachments survive restart, queue awaits user resume, steering reaches the intended turn, auto-continuation is controlled, and usage-limit/reset recovery does not duplicate edits. |
| Scheduling | Interval and server-time fixed schedules, current-thread and requested fresh-thread binding, one-thread fresh sessions, safe model/options, unified update/pause/resume/delete, UI and MCP run-now results/permission denials, and restart recovery. Confirm bound runs follow current thread settings and MCP-created unbound runs resolve the primary remote's default branch with `main`/`master` fallbacks, using stored task settings until bound. Check local-only and nonstandard-default repositories too. For webhooks, signature rejection, private secret entry, filters, replay/coalescing and missing keys, stale deliveries, and caller/Connect URL eligibility. |
| PR workflow | Link all stack layers, receive checks/comment/conflict wakeups without polling, unwatch on handoff, and read review-guide results/staleness. Do not merge as a side effect of monitoring. |
| Client and gateway | Update/reboot readiness, direct/private and Connect access, gateway TLS, preview attachment and recording transfer, and optional offline webhook storage only when deliberately selected. |
| Visual and outside-agent workflows | T3 Chrome cold-start/host diagnostics, narrow/light/dark HTML preview and local image embedding, readable text, private file secret delivery/expiry, deliberate OAuth grant/revocation, and opt-in device alerts. Record any browser validation gap separately from successful HTML publication. |

Retain the overall status in [release qualification](../release-qualification.md).
Native mobile/device SDK provisioning remains a [deferred plan](../plans/mobile-agent-support.md).

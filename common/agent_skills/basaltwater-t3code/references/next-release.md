# T3 Code 0.1.8 preparation

Reviewed **2026-11-09**. Stable remains
[0.1.7](https://github.com/pingdotgg/t3code/releases/tag/v0.1.7).
The reviewed candidate is
[0.1.8-nightly.20261109.4429](https://github.com/pingdotgg/t3code/releases/tag/v0.1.8-nightly.20261109.4429),
commit `dc7270581800518b9d7d4db6c8e13852067c8c2d`.
Nightly features can change or miss the next stable release; no release date is
established by this review. Recheck the eventual tag before updating guidance.

## Decision

Keep stable deployment and existing provider defaults. No runtime adapter change
is justified by the reviewed source alone. Prepare release acceptance checks and
update the relevant skills when the features ship. This review did not run the
nightly archive, migrate a database, or qualify native clients/provider turns.

The candidate retains service-state protocol **3**, `activeVersion`, and the
`__service-launcher` entry point. Basaltwater already recognizes that contract;
its log wrapper reads the upstream unit's actual command. The launcher now avoids
loading the full server dependency graph, which upstream describes as reducing
launcher memory. Do not turn that into a whole-VM capacity claim.
Sources: [protocol](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/apps/server/src/cloud/serviceProtocol.ts),
[unit construction](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/apps/server/src/cloud/bootService.ts),
and [launcher change](https://github.com/pingdotgg/t3code/commit/6577c6b6185167c0e502fa01f9e8e8f2dee6ff25).

## Changes to qualify before adoption

- **Scheduling and fresh sessions:** `threadMode` distinguishes `new_thread`,
  `continue`, and `fresh_session`. Migration 080 maps existing bound tasks to
  `continue`; omitted modes retain legacy behavior. Test existing schedules,
  fresh sessions in one thread, task moves, workspace/model settings and retained
  run history. Update the subagent scheduling reference after checking the live
  MCP schema. A fresh session needs a self-contained prompt and durable task
  context. Sources: [contract](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/packages/contracts/src/scheduledTask.ts)
  and [migration](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/apps/server/src/persistence/Migrations/080_ScheduledTaskThreadMode.ts).
- **Context reset:** `/clear` starts a new provider session in the same thread;
  earlier messages and diffs remain visible but are not supplied to that session.
  Distinguish it from compaction and Restart agent session in context/handoff
  guidance. Preserve a current checkpoint before a deliberate reset. Source:
  [composer behavior](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/docs/user/composer.md).
- **Secrets and sudo:** private secret cards can accept a secret-provider
  reference; command delivery uses a private file. Headless 1Password needs
  server-side service-account or Connect authentication. Qualify cancellation,
  unavailable/locked providers and file handling with synthetic secrets.
  A private sudo prompt transports a credential; it does not grant permission
  beyond Basaltwater's existing privilege broker and session boundaries. Do not
  install `op`, configure credentials or expand sudo access as release prep.
  Source: [secret workflow](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/docs/user/secrets.md)
  and [nightly sudo change](https://github.com/pingdotgg/t3code/releases/tag/v0.1.8-nightly.20261109.4399).
- **Text generation and cost:** optional direct model APIs can write titles,
  commit/PR text, branch names and transcript cleanup. They require their own
  API route and can send a staged diff to that endpoint. Subscription access
  does not imply those requests are included. Keep the configured agent route
  unless the user selects a generator; preserve Sol/Opus recommendations and
  standard service. Review guides still need a coding agent. Source:
  [text generators](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/docs/user/text-generation.md).
- **Maintenance and service boundaries:** native database Reclaim temporarily
  stops the server and needs temporary/WAL disk headroom. Treat it as explicit
  maintenance, not routine cache cleanup. Verify custom `--base-dir` retention,
  pending-restart detection, shutdown/signal forwarding and log filtering.
  Check `service.env` precedence against managed bind/port settings: explicit
  `T3CODE_HOST` overrides the new LAN setting. Do not loosen managed networking.
  Source: [candidate service guide](https://github.com/pingdotgg/t3code/blob/dc7270581800518b9d7d4db6c8e13852067c8c2d/docs/user/background-service.md).
- **Provider updates and tools:** candidate updates mise-installed providers
  through mise. Check this against Basaltwater's updater ownership and model
  discovery. HTML preview/render tools can accept existing file paths; use only
  when the live schema exposes them. Recheck delegated result delivery to idle
  parents and the actual returned model. Sources:
  [provider update nightly](https://github.com/pingdotgg/t3code/releases/tag/v0.1.8-nightly.20261108.4380),
  [HTML file support](https://github.com/pingdotgg/t3code/releases/tag/v0.1.8-nightly.20261108.4367),
  and [delegated result recovery](https://github.com/pingdotgg/t3code/releases/tag/v0.1.8-nightly.20261109.4392).

Before stable rollout, use a disposable home with the release's verified archive
for fresh startup and a 0.1.7 upgrade. Check SQLite integrity, terminal/native-PTY
loading, pairing grants/revocation, managed policy, and an authenticated provider
turn. Verify matching desktop/mobile clients separately. Preserve a private
recovery copy and upstream downgrade blocks; do not test against live user data.
Existing offline wrapper/runtime tests cover local contracts only.

## Watch, without implementing yet

Open proposals are weaker evidence than tagged nightlies. At review time,
[Boat.dev sandboxes](https://github.com/pingdotgg/t3code/pull/33116),
[ACP skill/model reporting](https://github.com/pingdotgg/t3code/pull/33394),
and [NVIDIA Wayland GPU handling](https://github.com/pingdotgg/t3code/pull/30919)
remain open. Reassess if merged; do not provision cloud accounts, add a provider,
or remove desktop workarounds based on an open PR. The sandbox proposal alone
does not justify a new Basaltwater deployment integration.

# T3 Code 0.1.9 integration

Reviewed **2026-11-11** against [stable 0.1.9](https://github.com/pingdotgg/t3code/releases/tag/v0.1.9),
commit `24e77473ecf41bc1f7142529c7911c1c1aae1d76`; stable release and integration
contracts rechecked **2026-11-15**. This is historical evidence; the current
stable review is [0.1.10](release-0.1.10.md). Basaltwater setup resolves stable.

## Compatibility

Service-state protocol **3**, `activeVersion`, `__service-launcher`, the
standalone archive layout and all 16 administrative pairing permissions remain
compatible. No runtime adapter change is required. Preserve the complete
archive, including `lib/libatomic.so.1`, and use its embedded Node for the native
PTY probe. The database adds migration **81**, a projection-message search-text
change; do not edit migration records or bypass downgrade guards. Sources:
[service protocol](https://github.com/pingdotgg/t3code/blob/v0.1.9/apps/server/src/cloud/serviceProtocol.ts),
[launcher](https://github.com/pingdotgg/t3code/blob/v0.1.9/apps/server/src/cloud/bootService.ts),
[auth](https://github.com/pingdotgg/t3code/blob/v0.1.9/packages/contracts/src/auth.ts),
and [migrations](https://github.com/pingdotgg/t3code/blob/v0.1.9/apps/server/src/persistence/Migrations.ts).

The [provider manifest](https://github.com/pingdotgg/t3code/blob/v0.1.9/apps/server/src/provider/model-manifest.json)
retains recommendations of Codex **0.159+**, Claude Code **2.1.301+**,
Grok **1.0.13+**, OpenCode 2 **2.0.18+** and Pi **1.0+**. OpenCode native goals
separately require **2.2.5+**. Discovery/catalog additions do not authorize
changing a provider, model, billing route or release track. Basaltwater's
existing OpenCode installation does not establish OpenCode 2 qualification.

## Changed workflows

The shared [0.1.9 client workflows](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-project-development/references/t3-workflows.md#shared-019-client-workflows)
cover attachment sending, Claude MCP session grants, native OpenCode goals,
anchored PR links, GitLab revert, background command details and accessibility.
[Outside-agent operations](operations.md#outside-agents) now distinguish hosted
Connect account approval from the environment-local code/browser route.
Neither enrolls outside agents automatically.

The [scheduling reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-scheduling.md)
now covers 0.1.9's workspace preservation: updating an already-unbound schedule
with `bindToCurrentThread: false` retains its saved workspace. Only a change
between bound and unbound recomputes that strategy. The shared project workflow
also covers action previews in web clients; `autoOpenPreview` and `previewUrl`
require Preview availability for the selected environment. The
[orchestration reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-orchestration.md#pull-requests-and-guides)
covers retained PR approval holds: missing approval reads do not make checks
green, and rewatching does not clear a hold.

Preview snapshots now preserve page text better beside large accessibility
trees, while still bounding metadata. Inspect `omitted` before inferring that
content is absent. `preview_evaluate` also accepts top-level `await` for bounded,
read-only asynchronous checks. The shared browser references cover both features;
use them when browser work is requested. Sources:
[snapshot metadata](https://github.com/pingdotgg/t3code/pull/33745) and
[async evaluation](https://github.com/pingdotgg/t3code/pull/31925).

Debian's readiness doctor now detects `service.env` bind overrides that conflict
with its managed drop-in, using T3's Node dotenv parser and reporting only key
names. It preserves owner settings and skips automatic repair/restart and the
long startup wait while those settings remain unverified. See
[service environment](updates.md#018-service-environment-and-maintenance) and the
[tagged launcher](https://github.com/pingdotgg/t3code/blob/v0.1.9/apps/server/src/serviceLauncher.ts).

Download failures now include their step, HTTP status and host; distinguish
network/download failures from native-runtime faults. Provider status probes
no longer cause mise to install missing CLIs. The release also fixes default
branch handling in **Commit, push & PR**, queued model selection, completion
wakes, desktop tab recovery and shutdown of the Connect relay. These fixes do
not establish live qualification of those paths. See the [release notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.9).

## Verification and limits

The GitHub-digest-verified Linux x64 archive passed disposable fresh-home
version/runtime selection, embedded native-PTY loading, HTTP 200 startup through
Basaltwater's output filter, and administrative pairing with all 16 permissions.
The helper completed its bootstrap-session revocation. Signal forwarding stopped
the fixture server; SQLite integrity checks passed afterward. A second check
started a digest-verified 0.1.8 archive in another disposable home, then ran
0.1.9 against the retained data. Startup, native PTY, pairing, shutdown and
integrity checks passed; migration count advanced from **80 to 81**. The focused
Debian/CachyOS integration suite passed **246 tests** on the 2026-11-15 recheck;
**513 smoke tests**, syntax, documentation links, package metadata and the three
affected skill validators also passed. That recheck did not repeat the archive
startup/upgrade trials or expand the live qualification below. No live shared service,
user database, provider account or release-track setting was changed.

A follow-up audit on **2026-11-15** added service-environment conflict and
privacy regression checks. `make check-ci` completed with **9,729 tests run,
5 skipped**, including coverage, syntax, documentation and wheel checks. The
updated wheel requirement and focused readiness tests also passed separately.
Temporary dotenv fixtures passed under host Node and T3's embedded Node;
the read-only live doctor passed all required checks. All three affected skill
bundles passed validation. Preview changes were reviewed against upstream
source; this audit did not exercise a browser or restart a shared service.

These checks do not qualify systemd installation/update/reboot, authenticated
provider turns or terminals, native clients, hosted Connect approval, or new
UI/goal/approval workflows. Use the [live qualification matrix](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/t3-code.md#stable-feature-qualification)
on disposable systems before claiming those paths. Refresh delivers the shared
skills through the existing platform bundles; updating this repository does not
refresh another machine or reload an already running provider session.

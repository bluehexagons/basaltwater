# Windows runner support

Status: runner-only project scope, revised 2026-10-20. The minimal panel and
native setup below are proposed work. The current Windows runner code is not
in use and may be replaced without backwards compatibility. Windows remains
outside the current [v2.0.0 stable-release gate](../release-qualification.md).

## Purpose and limits

Deliver a small Windows GitHub runner setup, automated as far as practical:

- Install and configure the runner and its necessary tools.
- Provide a minimal remote web panel for current system/runner status,
  allowed-user rules, and outgoing webhook notifications.
- Qualify the setup with Antistatic's project-owned Windows build and tests.

The tools must also be installable on an ordinary Windows desktop without
enabling the runner or panel. Optional T3 Code and software available through
WinGet or comparable package/vendor installers are conveniences only. Do not
add managed desktop workflows, agent integration, or custom application support.

Windows is not a Basaltwater spoke. Exclude notification ingest, spoke
enrollment, fleet coordination, agent pages, job approval, and remote job
orchestration. General Windows development/debugging support, engine bundles,
desktop automation, GPU qualification, signing/publishing, VM provisioning,
and historical Windows compatibility matrices are also outside this phase.
Implement features necessary for the runner or inexpensive reuse of existing
components; anything larger needs a separately justified scope.

## Decisions

| Area | Direction |
| --- | --- |
| Baseline | Use the latest publicly available stable Windows 11 x64 build on one manually prepared Proxmox VM. Record the exact build, project revision, and test date. |
| Workload | Antistatic is the required first runner workload. Its repository owns dependencies, toolchain versions, build commands, and test coverage. |
| Execution | Target one native Windows implementation. Prove the gate and minimal panel can stay small before committing to their adapters; revisit scope if necessary instead of adding a second backend. WSL is not a required feature. |
| Startup | Owner-login startup is sufficient. Pre-login CI and service mode are deferred. |
| Panel | One small runner panel with current metrics, allowed-user editing, and outgoing webhook settings/status. Preserve the Linux-style authenticated remote-access experience. |
| Desktop tools | Optional package installation, including T3 Code, can run independently. No desktop use or additional T3/agent features are deliverables. |
| Compatibility | No migration, old Windows runner configuration/flag compatibility, or continued WSL-backed runner support is required. Preserve shared Debian runner behavior and policy safeguards. |

## Reuse selectively

The [Windows/WSL guide](../workstations/windows-wsl.md) and
[Windows runner guide](../workstations/windows-github-runner.md) describe the
current implementation, not requirements to preserve. Native jobs currently
depend on WSL setup state and invoke the policy gate through WSL in
[`GitHubRunner.Common.ps1`](../../windows/GitHubRunner.Common.ps1). A native
panel alone does not remove that dependency.

Reuse useful source pinning, owner checks, verified downloads, registration,
worker cancellation, and cleanup code. Extract portable policy/validation logic
from POSIX-only imports and add only necessary Windows state/permissions
adapters. Avoid a full CLI/runtime port or a second authorization implementation.
Replace unused Windows paths, flags, and state formats directly where simpler;
a clean reinstall is acceptable. Remove obsolete paths and tests when replaced,
while retaining meaningful coverage of supported behavior.

## Small automated setup

Build on [`install.ps1`](../../install.ps1) where useful. Support a tools-only
selection independently of runner/panel setup; avoid a new package framework.

- Install Git, PowerShell, GitHub CLI, and the chosen backend runtime only as
  needed. Offer T3 Code as an optional package. Add project tools through a small
  explicit package selection or the project's own bootstrap.
- Use exact WinGet package identities, deliberate install scope, and supported
  vendor installers where required. Verify tools under the intended Windows
  account and record versions. Do not add custom integrations for installed apps.
- Handle repeated setup, installation failure, and required restart/resume.
  Verify downloads, bound/redact logs, and avoid disrupting an active job.
- Keep enrollment, authentication, and privilege consent explicit; report
  remaining manual steps. Do not turn setup into a general software updater.

Use native Windows checkouts and build directories. Run the CI instance as a
dedicated non-administrator account without unrelated desktop/T3 credentials.
Tools-only desktop installation does not require a separate desktop test matrix.
The current `-Node`, `-Python`, and `-Go` options configure Ubuntu; the new
interface may replace them with an explicitly native selection.

### Antistatic workload

Use the sibling `../antistatic` repository. Its
[setup guide](https://github.com/bluehexagons/antistatic/blob/main/docs/development/setup.md),
[dependency policy](https://github.com/bluehexagons/antistatic/blob/main/docs/development/dependencies.md),
and [Windows CI](https://github.com/bluehexagons/antistatic/blob/main/.github/workflows/ci.yml)
own the prerequisites and commands. Prepare native Node/npm, compiler/CMake
prerequisites, and Go when its netplay fixture needs it. Antistatic owns the
pinned vcpkg/SDL dependencies and fixture preparation.

Run its documented `npm run bootstrap:windows`, `npm ci`, `npm run doctor`,
`npm run build`, and `npm run test:windows` workflow through the runner.
Keep build/test scripts and toolchain pins in Antistatic. Interactive debugging,
gameplay, and desktop compatibility checks are not Basaltwater acceptance gates.

## Minimal runner panel and notifications

Reuse portable parts of the existing [panel](../services/web-panel.md),
[runner view](../../common/service_tools/web_panel_github_runner.py), and
[outgoing notification contract](../services/notifications.md). The full Debian
panel and its Linux service/metrics collectors are not a porting target.

- **Current status:** uptime, CPU/memory use, free disk space, runner registration,
  repository/organization, labels/version, local task/process state, and bounded
  recent gate outcomes. Cache/bound probes and show unavailable data clearly.
  Link to GitHub for authoritative runner/job status.
- **Runner rules:** edit only the allowed-user list for existing registrations.
  Setup, forms, and the gate share one authoritative configuration and validator.
  Authenticate writes, protect against CSRF, serialize/atomically apply changes,
  and reject invalid input without losing working policy. Keep supported event,
  private-repository, fork, and identity restrictions fixed.
- **Outgoing webhooks:** configure/test a delivery target and show bounded recent
  local delivery outcomes. Reuse existing payload/redaction and bounded retry
  behavior where available. No notification receiver, inbox, ingest token,
  spoke routing, or general notification history service is needed.

Start the runner and panel automatically at owner login and provide an
authenticated HTTPS URL with Windows-appropriate wiring. Choose one LAN/tunnel
access arrangement for initial qualification. Keep backend access private, protect
configuration/credentials with native account permissions, and never display
tokens or raw job payloads. Test delivery failure and stopped/unavailable states.

Keep registration in the CLI. Exclude metrics history/charts, general service
inventory, a browser shell, new job/artifact interfaces, and advanced dashboard
features. Installed T3 Code needs no integration with this panel.

## Delivery and acceptance

| Step | Completion condition |
| --- | --- |
| WIN-01: Native proof and setup | Demonstrate the native policy gate, one protected status page, and one outgoing webhook on current Windows 11. Automate the required tools and login startup; tools-only installation works without runner enrollment. Record the runtime/access choice. |
| WIN-02: Runner and small panel | Register/run Windows jobs; expose current status, allowed-user editing, and webhook configuration/test/outcomes. Retain shared policy, host-wide serialization, cancellation, and worker cleanup safeguards. |
| WIN-03: Runner qualification | Build/test Antistatic on the VM; check setup failures, rule enforcement, remote access, webhook failures, and session lifecycle. Record the baseline and remaining limitations. |

Use focused offline checks for policy, Windows adapters, configuration, and
panel behavior, plus the real VM for installation and GitHub-connected evidence:

- Fresh/repeated setup, failed installation, restart/resume, tools-only setup,
  and native execution without WSL.
- Antistatic build/tests at a recorded revision; no independent desktop or
  engine compatibility suite.
- An allowed job and denied/malformed identities, including `if: always()`
  canaries; shared-slot/cancellation cleanup and protected credentials/state.
- Authenticated access and rule changes taking effect on the next gated job;
  invalid updates, CSRF protection, unavailable probes, and webhook failure.
- Login, session lock, RDP disconnect, logoff, and reboot/login behavior. An
  owner-login runner is not expected to provide unattended pre-login CI.

Adapt/remove tests for retired Windows paths; WSL runner regression or migration
qualification is not required. Preserve shared Debian policy tests. Update
operator guides for the delivered implementation, not the proposed interface.
Rerun focused qualification after relevant Windows or runner changes.

## Remaining choices and suggested further cuts

- Prefer one small Python backend for shared gate/panel/webhook logic, with
  PowerShell for installation and Windows queries. Confirm native imports and
  permissions in WIN-01 before selecting dependencies; avoid a general port.
- Choose one remote-access arrangement and one webhook target for version one.
  Which local runner failures need alerts? Start with runner failure and failed
  delivery status; leave build-result notifications with GitHub.
- Keep registration in the CLI and rules limited to allowed users. Add no other
  editable policy fields without a concrete runner need.
- Keep runner execution independent of the panel: a panel restart or webhook
  outage must not stop jobs. Reuse GitHub workflow commands, logs, and artifacts
  rather than introducing a separate Windows job protocol or build dashboard.

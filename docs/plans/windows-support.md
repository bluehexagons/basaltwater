# Windows debugging and runner support

Status: narrowed project scope, revised 2026-10-20. This is a small extension
of the existing Windows/WSL setup for Windows issue reproduction and CI. The
development panel below is proposed work, not an available feature. Windows
remains outside the current [v2.0.0 stable-release gate](../release-qualification.md).

## Purpose and limits

Use a Windows VM when a project needs a Windows bug investigated or its Windows
build checked. Regular development stays on the existing development systems.
The Windows desktop needs only the tools necessary to reproduce, inspect,
debug, and verify those issues.

Deliver three things:

- A small, repeatable setup for the selected project's Windows tools, using
  WinGet and the existing PowerShell installer where practical.
- A working native Windows GitHub runner with the existing execution policy.
- A minimal Basaltwater web panel for system monitoring, runner rules, and
  notifications, with the same remote-access experience as Linux.

An existing coding tool or agent may help investigate a Windows issue; a new
native agent platform or general development workstation is not required.
Godot, Electron, and custom engines are examples of projects to investigate,
not separate tool bundles or compatibility matrices to maintain.

## Decisions

| Question | Agreed direction |
| --- | --- |
| Q01: Windows and projects | Generally use the latest publicly available stable Windows 11 build, keeping the VM current. Record the exact build and test date for evidence rather than maintaining a historical build matrix. Antistatic is a required starting workload; other real projects can follow as needed. |
| Q02: Panel and remote access | Include basic host monitoring, runner rule configuration, and notifications. Use the existing Linux-style authenticated remote panel experience. Agent tooling and most other dashboard features are excluded. |
| Q03: WSL | Prefer a small native Windows implementation if the reduced panel and runner can work without substantial compatibility code. WSL exists to support Basaltwater tooling, not the Windows applications under test. Decide from a bounded feasibility check, retaining WSL only if it makes the selected features materially simpler. |
| Q04: Startup | Owner-login startup is sufficient for the first version. Unattended pre-login CI and service mode are not required. |

Exclude general Windows workstation support, Debian feature parity, a broad
Windows version/hardware matrix, generalized language/engine setup, native
desktop automation, and additional coding-provider ports. Automated Windows
installation/templates, Proxmox fleet management, GPU/migration infrastructure,
and signing/publishing systems are also outside this project.

## Reuse the current implementation

The [Windows/WSL guide](../workstations/windows-wsl.md) and
[Windows runner guide](../workstations/windows-github-runner.md) describe current
behavior. Keep their setup, source pinning, owner checks, resume, native job
execution, and runner registration/cleanup paths.

Jobs already run on Windows. The runner still uses completed WSL setup state
and calls its policy gate through WSL in
[`GitHubRunner.Common.ps1`](../../windows/GitHubRunner.Common.ps1). Evaluate a
native panel and runner together: a native panel alone does not remove this
dependency. Check the small host-metrics, policy/state, notification, and access
adapters before choosing the implementation. Reuse portable logic and extract
it from POSIX-only imports as necessary; do not port the entire runtime or
duplicate runner authorization rules. Keep existing Windows/WSL installations
working, without building two new panel implementations.

Use a manually prepared, current Windows 11 x64 Proxmox VM. Record the working
configuration; WSL/nested-virtualization prerequisites apply only if WSL is
retained. Basaltwater does not need a Windows VM provisioner, image builder, or
template lifecycle to finish this work.

## Minimal automated setup

Build on [`install.ps1`](../../install.ps1); avoid a new package/configuration
framework. Automate tool installation and necessary configuration after the
operator has supplied the Windows VM.

- Install Git, PowerShell, and GitHub CLI as needed for the selected workflow.
  Add only the project's required runtime, compiler/SDK, debugger, or editor.
  Reuse existing T3/agent setup if needed for investigation.
- Use exact WinGet package identities and deliberate install scope. Use a
  supported vendor installer for required components that WinGet alone does
  not configure. Record selected versions and verify the tool works under the
  actual Windows account; package presence alone is insufficient.
- Reuse the existing source pin and resume flow. Handle already installed
  tools, failed installation, and required restart without silently declaring
  success or interrupting an active job. Keep downloads verified and logs
  bounded and free of credentials.
- Keep authentication/enrollment and any necessary privilege consent explicit.
  Report remaining manual steps. Do not install unrelated packages or upgrade
  the whole machine's software as part of setup.

Use a native Windows checkout and build directory for Windows work. Project
instructions own the build/debug/test commands and dependencies; Basaltwater
only prepares what the selected workflow needs. Current `-Node`, `-Python`,
and `-Go` installer options configure Ubuntu, so native tools must be identified
explicitly without changing those options' meaning accidentally.

### Antistatic qualification workload

Use the sibling `../antistatic` repository for the first real build and Windows
debug/check workflow. Its [setup guide](https://github.com/bluehexagons/antistatic/blob/main/docs/development/setup.md),
[dependency policy](https://github.com/bluehexagons/antistatic/blob/main/docs/development/dependencies.md),
and [Windows CI](https://github.com/bluehexagons/antistatic/blob/main/.github/workflows/ci.yml)
own the prerequisites and commands. Provision compatible native Node/npm,
the native compiler/CMake prerequisites, and Go for the netplay test fixture
when running that suite. Antistatic owns its pinned vcpkg/SDL dependencies.

Follow the repository's `npm run bootstrap:windows`, `npm ci`, `npm run doctor`,
and `npm run build` workflow, then `npm run test:windows` with its documented
lobby fixture preparation. Check interactive behavior relevant to a real issue.
Keep versions and build logic in Antistatic; Basaltwater should prepare the host
and run the project's commands rather than copy its dependency/build machinery.

## Minimal development web panel

Reuse the existing [panel](../services/web-panel.md),
[runner view](../../common/service_tools/web_panel_github_runner.py), and
[notification contracts](../services/notifications.md) where practical, with
narrow Windows adapters. Current collectors and service wiring use Linux paths
and systemd; importing the complete Debian panel service is not the plan.

The first version includes:

- **System monitoring:** uptime, CPU use, memory use, disk capacity, and local
  runner health. Collect Windows readings, bound/cache probes, and mark missing
  data unavailable rather than copying Linux-specific metrics or audit systems.
- **Runner status:** configured repository/organization, labels, version,
  registration, local task/process state, and recent allowed/denied gate
  outcomes. Link to GitHub's runner settings; local state cannot prove Online/Idle.
- **Runner rules:** configure the allowed-user list for existing registrations
  through authenticated forms. Use the same validation and authoritative policy
  as setup and the gate. Serialize/atomically apply changes, reject invalid
  updates without losing working rules, and record bounded change outcomes.
  Preserve the existing private-repository, event, fork, and identity limits.
- **Notifications:** show recent development/system/runner alerts and configure
  and test the selected delivery target/level, reusing the existing event format
  and bounded history. Settle the first event/target set before implementation;
  a new notification platform is unnecessary.

Setup should configure and start the panel automatically for the chosen login
model. Remote access should work like the Linux panel: a configured URL,
authenticated HTTPS, and the normal configured LAN/tunnel access route. Match
the existing access and CSRF requirements with Windows-appropriate wiring;
do not require Nginx, systemd, Unix sockets, or WSL merely to copy deployment
details. Keep the application listener private behind the authenticated entry
point and protect policy/notification credentials with native account access.

Show pending, stopped, invalid/unavailable, and delivery-failure states clearly.
Never expose tokens, credential files, or raw job payloads. Agent pages,
scheduling, prompts, a browser shell, job approval, publishing, and the rest of
the Debian dashboard remain excluded. Runner registration can retain its
existing CLI workflow; a web token form is not required.

## Delivery and acceptance

| Step | Completion condition |
| --- | --- |
| WIN-01: Feasibility and small setup | On current Windows 11, check the reduced native panel/runner adapters and choose native or WSL-backed execution. Prepare Antistatic's necessary tools, verify them as the intended user, and handle repeat setup and restart/resume. Record the choice and its remaining dependencies. |
| WIN-02: Runner and development panel | Run native Windows jobs and deliver monitoring, allowed-user rule editing, and notifications through authenticated remote access. Start at owner login. Retain the existing runner policy, shared slot, cancellation, and cleanup behavior. |
| WIN-03: Focused qualification | Build/test Antistatic through the runner, investigate/check its relevant Windows behavior, exercise monitoring/rule/notification failure states, and document the tested configuration and limitations. Rerun focused checks after Windows updates; no wider historical platform matrix is required. |

Qualify on the selected Windows 11 Proxmox VM, with focused checks for:

- Fresh/repeated setup, failed installation, and restart/resume without broken
  owner state or leaked credentials.
- Antistatic's Windows build/tests and a chosen issue-reproduction/debug or
  compatibility check. Interactive testing covers only what that project
  requires on this VM; record the exact Windows build and project revision.
- Runner registration, an allowed job, denied/malformed identity cases including
  `if: always()` canaries, shared-slot/cancellation cleanup, and login/reboot
  behavior. Preserve existing private-repository and identity restrictions.
- Monitoring/status for running, stopped, pending, and invalid/unavailable state;
  authenticated rule changes reflected in the next gated job, rejected invalid
  changes, CSRF protection, and consistency with setup.
- Notification display/configuration, test delivery and failure reporting,
  bounded outcomes, remote access, login startup, and no secret disclosure.

Retain existing offline Windows checks and add focused adapter/panel checks for
new code. Use the real VM for installation, login, and GitHub-connected evidence.
If a native-only path is selected, check it without WSL and retain the
existing WSL regression checks. Otherwise qualify the existing WSL-backed path;
do not require two implementations. Update operator guides only for delivered
behavior, with the exact baseline and limitations recorded.

## Remaining implementation choices

- Which small native server/runtime and HTTPS/authentication/tunnel adapters
  best reuse the current code? Include the runner gate, not just panel rendering,
  in the native feasibility result before deciding whether WSL adds value.
- Which notification events and first delivery target are needed? Start with
  useful system/runner failures and a tested target; decide whether inbound
  notification ingest is required initially.
- Are runner rule forms beyond the allowed-user list needed initially? Keep
  supported trigger/identity restrictions fixed and scope any additional fields
  through the existing runner configuration model.

Finish the selected debug/check workflow, runner, and minimal panel before
considering any further Windows scope.

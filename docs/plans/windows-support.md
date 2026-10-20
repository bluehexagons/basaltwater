# Windows debugging and runner support

Status: narrowed project scope, revised 2026-10-20. This is a small extension
of the existing Windows/WSL setup for Windows issue reproduction and CI. The
runner panel below is proposed work, not an available feature. Windows remains
outside the current [v2.0.0 stable-release gate](../release-qualification.md).

## Purpose and limits

Use a Windows VM when a project needs a Windows bug investigated or its Windows
build checked. Regular development stays on the existing development systems.
The Windows desktop needs only the tools necessary to reproduce, inspect,
debug, and verify those issues.

Deliver three things:

- A small, repeatable setup for the selected project's Windows tools, using
  WinGet and the existing PowerShell installer where practical.
- A working native Windows GitHub runner with the existing execution policy.
- A minimal Basaltwater web panel for that runner.

An existing coding tool or agent may help investigate a Windows issue; a new
native agent platform or general development workstation is not required.
Godot, Electron, and custom engines are examples of projects to investigate,
not separate tool bundles or compatibility matrices to maintain.

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
[`GitHubRunner.Common.ps1`](../../windows/GitHubRunner.Common.ps1). Keeping that
dependency is acceptable for this project. Native-only operation is worthwhile
only if it needs a small, maintainable adapter; it is not an acceptance gate or
a reason to port Basaltwater's POSIX runtime. Any such change must retain the
shared policy and existing WSL behavior.

Use a manually prepared Windows 11 x64 Proxmox VM with a known OS build and
the existing guest prerequisites. Record the working configuration and any
WSL/nested-virtualization requirements. Basaltwater does not need a Windows VM
provisioner, image builder, or template lifecycle to finish this work.

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

## Minimal runner web panel

Include a small runner page in this project. Reuse the existing
[runner panel](../../common/service_tools/web_panel_github_runner.py) presentation
and state concepts where practical, with a narrow Windows status adapter.
Its current collector uses Linux paths and systemd; importing the complete
Debian panel service is not the implementation plan.

The first version is read-only and shows:

- Configured repository/organization, labels, runner version, and registration
  state.
- Local runner task/process state and relevant setup/WSL failures. Distinguish
  local state from GitHub's Online/Idle status, which local task state cannot
  prove; link to the configured GitHub runner settings.
- Allowed users and a bounded list of recent allowed/denied gate outcomes.
- Clear pending, stopped, unavailable, or invalid-state messages, plus the
  existing commands needed to register or start/stop the runner.

Setup should configure and start the panel automatically for the chosen login
model. Choose its host and access method under Q02. Default to loopback and
use an authenticated private access path for remote viewing. Read only the
required runner metadata; never expose tokens, credential files, or raw job
payloads. Allowlist editing, token submission, job approval, a browser shell,
and other Debian dashboard features are outside the initial panel.

## Delivery and acceptance

| Step | Completion condition |
| --- | --- |
| WIN-01: Small setup | Select one Windows VM baseline, one real project, and its necessary tools. Setup installs/configures them, verifies them as the intended user, and handles repeat invocation and restart/resume. Resolve Q01 and the bounded native-only choice in Q03. |
| WIN-02: Runner and panel | Register and run native Windows jobs through the existing runner. Deliver the status page above with automatic startup and the access method selected in Q02. Retain the current policy, shared slot, cancellation, and cleanup behavior. |
| WIN-03: Focused qualification | Reproduce/debug or check a real project's Windows behavior, run a real Windows build through the runner, exercise panel failure states, and document the tested setup and remaining limitations. No wider platform support claim follows from these checks. |

Qualify on the selected Windows 11 Proxmox VM, with focused checks for:

- Fresh/repeated setup, failed installation, and restart/resume without broken
  owner state or leaked credentials.
- The chosen issue-reproduction/debug workflow and its project-specific Windows
  build or compatibility check. Interactive testing covers only what that
  project requires on this VM.
- Runner registration, an allowed job, denied/malformed identity cases including
  `if: always()` canaries, shared-slot/cancellation cleanup, and login/reboot
  behavior. Preserve existing private-repository and identity restrictions.
- Panel status for running, stopped, pending, and invalid/unavailable state;
  bounded outcomes, protected access, startup, and no secret disclosure.

Retain existing offline Windows checks and add focused adapter/panel checks for
new code. Use the real VM for installation, login, and GitHub-connected evidence.
If a small native-only path is implemented, check it without WSL and retain the
existing WSL regression checks. Otherwise qualify the existing WSL-backed path;
do not require two implementations. Update operator guides only for delivered
behavior, with the exact baseline and limitations recorded.

## Outstanding questions

| ID | Question | Starting point |
| --- | --- | --- |
| Q01 | Which Windows 11 build, real project, and minimum tools define the test setup? | Choose the actual Windows issue/build workload. Install its compiler/runtime/debugger as needed; avoid a catalog of engine and language bundles. |
| Q02 | Should the small panel run in the existing WSL environment or directly on Windows, and how will it be reached remotely? | Choose the smallest option that can read Windows runner state and start reliably. Reuse existing presentation/authentication where feasible, without a full Debian service port or a new gateway stack. |
| Q03 | Can WSL be removed with a small change that reduces maintenance? | Briefly assess the policy/state adapter. Keep the existing WSL-backed path if removal needs a substantial runtime port; do not duplicate authorization rules. |
| Q04 | Is the runner's existing owner-login lifecycle sufficient for the runner and new panel? | Keep it for the first qualified setup unless unattended pre-login CI is an actual requirement. Document logout/reboot effects; add service mode only if that requirement is confirmed. |

Finish the selected debug/check workflow, runner, and minimal panel before
considering any further Windows scope.

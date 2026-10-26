# Native Windows build hosts

Status: proposed scope, revised 2026-10-26. Windows primarily supplies native
builds and packages for software, games and self-hostable game servers. Replace
the unused WSL-backed runner paths without migration or compatibility shims.
Preserve standard build-on-target behavior elsewhere. Windows remains outside
the current [v2.0.0 stable-release gate](../release-qualification.md).

## Workloads and composition

Use the [shared build-host design](cicd-manifest-reuse.md): select on-demand
controller builds, signed webhooks and GitHub Runner independently, with an
optional web panel and selected native tools. Implement native execution and
permissions adapters rather than routing policy through WSL or porting the
full Debian setup/runtime. WSL can be a separately selected Linux environment;
it does not establish Windows build qualification.

- **Software:** native compile/test/package commands, with explicit Windows
  runtime dependencies and distributable output directories.
- **Games:** project-owned engine/toolchain pins, headless build/export when
  supported, assets and required runtime files packaged together. Graphical
  testing, GPU access and interactive/licensed tools are separate capabilities.
- **Game servers:** build/package dedicated-server executables, configuration
  templates and runtime dependencies. A bounded smoke may start a disposable
  server on loopback and verify readiness and teardown. Persistent hosting,
  public port/firewall configuration and production player data are separate
  deployment work, not consequences of installing a builder.

Artifacts use the common platform/checksum/component contract. A Windows binary
cannot be activated as a Linux service merely because a controller can reach
both machines. Initially export/download Windows packages; automatic Windows
application/game-server activation needs its own runtime/recovery adapter.
Code signing, installer distribution and store publication require separately
authorized credentials and remain outside initial build qualification.

Add named package-only build targets before the Windows workload phase. They
declare platform, project-owned commands and exported files without requiring
an HTTP domain, port or service component. Select a target explicitly; native
commands must not be inferred by translating a Linux shell command. Deployment
components may consume those outputs later. Keep this manifest extension
separate from the first Debian application-build slice.

## Native setup and execution

Begin with one manually prepared, current stable Windows 11 x64 Proxmox VM.
Record the exact OS build and tool/project versions; do not add Windows VM
provisioning or a historical platform matrix. Reuse `install.ps1` where useful.
Tools-only installation remains independent of runner, build service and panel.
Optional T3 Code is a package convenience, not an agent/desktop integration.

Use a small native Python 3 backend for shared protocol, policy and job records,
with PowerShell for installation and Windows process/ACL adapters. Use the same
typed SSH transport as Debian through Windows OpenSSH; scope its dispatcher to
configured operations and projects. The native proof must verify portable
imports and this account/transport boundary before expanding the backend.
The dispatcher submits to the managed worker and reports unavailable while it
is stopped; an SSH connection must not become an unsupervised inline worker.

Git and PowerShell accompany the backend; Node, project Python versions, Go,
MSVC Build Tools/Windows SDK, CMake/Ninja and engine/export templates are explicit
optional requirements. Projects own versions and commands; verify selected
tools under the actual worker account. Use exact WinGet identities or verified
vendor installers, handle restart/resume and report manual license/enrollment
steps. Initialize MSVC through its supported [developer environment](https://learn.microsoft.com/en-us/visualstudio/ide/reference/command-prompt-powershell)
rather than assuming the operator's interactive PATH applies to workers.

Use native checkouts and literal argv or explicit PowerShell/script commands;
never assume Bash, POSIX quoting or WSL path conversion. Validate drive/UNC
paths, case-insensitive collisions, reserved names and reparse points before
packaging/extraction. Record target architecture and required DLL/runtime files;
do not copy arbitrary toolchain directories into artifacts.

Run jobs as dedicated non-administrator identities without unrelated desktop,
panel, deployment or signing credentials. Protect policy, executables and
credentials with Windows ACLs; POSIX modes are not a substitute. Share capacity
across entry points without sharing their credentials or writable workspaces.
Use supervised process trees, evaluating native [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
and qualify nesting/breakaway behavior with runner workers and project tools.
Do not release a slot or report cancellation complete while owned work may
still run. Preserve interrupted state rather than silently replaying jobs.

Login startup under the dedicated non-admin build account is sufficient for
the first proof; the human operator uses a separate account. Do not reuse the
operator's desktop owner or saved logins. That initial worker stops at build-
account logoff and starts after its next login; expose the availability limit.
Qualify locked sessions and RDP disconnect separately from logoff and the next
login after reboot. Prefer unattended service/task mode for headless workloads
after account permissions, startup and cleanup are proven. Interactive tools
must explicitly require a suitable session; a service is not GUI qualification.

## Optional build panel

Reuse the shared panel's job/artifact schema, authorization and backend actions,
with native collectors and lifecycle adapters. The full Debian panel is not a
porting target. Offer current host/capacity/tool readiness, bounded build status,
logs and package downloads, runner registration/rules and outgoing notifications.
Add start/cancel/retry/drain controls for local on-demand/webhook jobs only after
the same operations work through the authenticated controller interface. Runner
jobs show GitHub links; GitHub keeps workflow, cancellation and log authority.

Use authenticated HTTPS with a private backend, native ACLs, CSRF protection,
bounded forms/results and one-time token handoff. Policy edits share validators
and atomic persistence with setup and take effect at a defined job boundary.
Never display credentials or add a browser shell. The panel can restart without
interrupting jobs. No agent pages, notification ingest, spoke enrollment or
general fleet/desktop management is required. Keep outgoing notification failure
separate from build success; reuse the [notification contract](../services/notifications.md).

## Delivery and qualification

1. **Native proof:** qualify the shared Python/PowerShell backend and SSH transport;
   demonstrate protected status, typed build submission, version detection,
   packaging, Windows ACLs and process-tree cancellation without WSL.
2. **First real workload:** use [Antistatic's project-owned Windows setup](https://github.com/bluehexagons/antistatic/blob/main/docs/development/setup.md)
   and its dependency policy/CI. Run the documented bootstrap, doctor, build and
   Windows tests at a recorded revision. Keep pins and build scripts in the
   project; do not turn Basaltwater into an engine/dependency manager.
3. **Entry points and panel:** compose GitHub Runner and webhook/on-demand jobs
   under shared capacity, then expose inspection and bounded actions. Qualify
   allowed/denied/malformed identities, duplicate requests, waiting, drain,
   registration failure, controller disconnect and unavailable panel states.
4. **Package breadth and recovery:** add a Windows-native software package and
   a dedicated-server build with bounded loopback smoke when suitable projects
   exist. Test missing DLLs/tools, corrupt artifacts, interrupted writes, full
   disks, child-process survival and login/reboot recovery. Verify no job needs
   administrator privileges or silently substitutes WSL execution.

Use offline adapter/policy/panel tests plus real Windows and GitHub evidence.
Adapt or remove obsolete Windows tests while retaining behavioral safeguards.
Update [Windows runner operations](../workstations/windows-github-runner.md)
only when the replacement ships. Linux build success, a running listener and
generated configuration are not Windows-native qualification.

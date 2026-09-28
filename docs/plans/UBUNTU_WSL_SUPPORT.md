# Ubuntu WSL support on Windows 11

Status: proposed project; implementation and Windows qualification have not
started. Decisions incorporate the project discussion through 2026-09-28.
This document owns scope, delivery order, and acceptance criteria. It does not
declare Windows or WSL supported by the current release. Examples below are
proposed interfaces, not working installation instructions.

## Outcome

Make an existing, fully updated Windows 11 x86-64 machine useful for in-house
CI/CD, builds, testing, and optional agentic development. Start from a logged-in
user account with administrator permissions and one pasted PowerShell command.
Install the prerequisites, prepare Ubuntu under WSL 2, add selected native
Windows software, and provide basic status, maintenance, and recovery.

Follow the focused composition approach of [CachyOS support](../CACHYOS.md).
Reuse Linux implementations when they fit; implement Windows tasks in
PowerShell or other appropriate Windows code. The project is not a port of
every Debian server setup step to Windows.

## Settled scope

| Area | Decision |
| --- | --- |
| Host OS | Fully updated Windows 11 in desktop or server roles; Windows Server editions are outside this project. |
| Architecture | x86-64 only; Windows ARM support is not planned. |
| Linux | Latest stable WSL 2 and latest stable Ubuntu release available through the supported WSL distribution channel, with current package updates. No preview releases or WSL 1. |
| Initial access | Logged-in administrator account; UAC, Linux account setup, passwords, and provider login may require interaction. |
| Runtime access | Login-dependent operation is sufficient. Boot-before-login operation is optional future work. |
| Windows packages | WinGet only. Do not install Scoop as a fallback. |
| T3 Code | Optional full native Windows desktop application. No Basaltwater-managed T3 server/service in WSL and no Linux T3 desktop installation. |
| Agents | Optional independently selected tools, in Windows or Ubuntu according to their supported execution environment. |
| CI trust | Primarily in-house code on machines isolated by purpose; a separate agent/build identity is not mandatory. Retain inexpensive credential protections. |
| Test installations | Include unactivated and official evaluation installations as separate qualification cases; activation is not a Basaltwater prerequisite. |
| Future provisioning | Keep setup reusable from a future Windows VM provisioner. ISO installation, unattended Windows setup, and hypervisor provisioning are deferred. |

The latest-stable policy replaces the earlier suggestion to fix support to
Ubuntu 24.04 LTS. Resolve an official available Ubuntu image and record its exact
release and provenance; do not assume that the generic `Ubuntu` alias always
means the newest stable release. If an expected release has no suitable WSL
image, report that limitation rather than substitute a development image.
Retain the resolved versions throughout an interrupted setup. Moving an
existing installation to a new Ubuntu release is explicit maintenance, not a
side effect of rerunning setup.

Evaluation and unactivated installation are different states. Record edition,
build, and relevant expiry information when available without collecting product
keys. Do not change activation or evaluation timers. Qualification must describe
the image actually tested rather than promise indefinite availability.

## Fresh-install experience

### Entry point and proposed command

Add a root `install.ps1` compatible with Windows PowerShell 5.1. No preinstalled
Git, Python, PowerShell 7, WSL, Ubuntu, or Basaltwater is required. Internet access
and working Windows Update are prerequisites. Check firmware virtualization,
disk capacity, Windows architecture/build, and pending restart conditions early.
Explain any firmware setting that cannot be fixed by the installer.

The eventual quick start should instruct the user to open PowerShell in their
normal logged-in account and paste a small download-and-run block. Request UAC
elevation for the required machine operations while retaining the original
user's identity for WSL registration, desktop software, and credentials.

Illustrative example only: `install.ps1` and these switches do not exist yet.
Publish a tested release URL in the operator guide when implementation lands.

```powershell
$installer = Join-Path $env:TEMP ("basaltwater-" + [guid]::NewGuid() + ".ps1")
Invoke-WebRequest `
  "https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1" `
  -UseBasicParsing -OutFile $installer -ErrorAction Stop

powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer `
  -Profile server_wsl -BuildServer -T3CodeDesktop -Node -Python
```

The process execution-policy option must not change the user's persistent
execution policy. Support a CMD wrapper invoking the same PowerShell entry
point; it must not introduce a second installer. Qualification must paste both
examples into their actual shells, including profiles and paths with spaces.
If the entry point is already elevated, do not launch desktop applications or
jobs with that token. Complete the machine phase and request a normal-user
continuation when a reliable handoff is unavailable.

Proposed options include `-Plan`, `-Resume`, `-Channel`, `-Version`,
`-DistroName`, `-LinuxUser`, `-InstallLocation`, and explicit capability switches.
Use separately named Windows and WSL agent/tool selections where a flag would
otherwise be ambiguous. Windows PowerShell parameters map to validated shared
configuration; the Linux CLI can retain its existing `--option` conventions.
Final spellings are an implementation decision, not an existing API promise.
Never put passwords, tokens, or private keys in the pasted command.

### Setup phases and resumption

1. Validate selections and show the plan. A plan may perform bounded read-only
   probes; it must not install dependencies, elevate, create accounts/tasks,
   rewrite configuration, or run update/repair commands.
2. Resolve and stage a coherent installer/source revision. Verify available
   artifact checksums and retain provenance. Protect elevated helpers from
   replacement by other users; validate resumed state before privileged use.
3. Ensure WinGet/App Installer and stable WSL are available. Enable required
   Windows features using Windows-owned operations.
4. If a restart is necessary, save progress, stop cleanly, and print the next
   action. After reboot and login, `-Resume` continues with the same selections
   and source revision. Do not force a restart or configure Windows auto-login.
5. Create or explicitly adopt the selected Ubuntu distribution under the
   intended Windows account. Complete Linux user setup interactively when
   needed; verify WSL 2, Ubuntu release, and systemd readiness.
6. Stage and invoke the Linux setup boundary with validated structured options.
   Install selected packages/runtimes and Windows applications under their
   correct owners. Linux mutations remain owned by `remote_setup.py` and the
   plugin registry; Windows mutations remain owned by the Windows companion.
7. Configure the selected job startup/access mode and check readiness. Report
   authentication or desktop pairing as a pending human step when necessary.
8. Save successful setup only after required checks pass. Preserve recoverable
   phase state and diagnostics on failure.

Use a versioned record containing the Windows owner SID, distribution identity,
Linux user, selections, source revision, completed phases, and restart reason.
Separate that operation record from last-successful configuration. Lock against
concurrent setup, reject corrupt or mismatched state, and make repeat runs
idempotent. Do not retain passwords or provider tokens in either record.
Downloads and state must survive reboot in a managed location; the original
temporary download is not the durable resume mechanism. Supply a stable resume
launcher before requesting a reboot and remove obsolete staging after success.

Check native process exit codes explicitly; PowerShell error handling alone
does not turn every failed executable into a terminating error. Validate and
serialize arguments across elevation, PowerShell, and WSL boundaries instead
of concatenating command strings. Establish bounded execution and clear
success, restart-required, waiting-for-user, and failed results.

## Platform responsibilities

| Operation | Owner and behavior |
| --- | --- |
| Windows features, WinGet, Windows applications | Windows companion; elevate only the necessary setup step. |
| WSL install/update, distribution registration, location | Windows companion under the recorded owning account. |
| Ubuntu packages, Linux tools, Linux service configuration | Existing Linux infrastructure through a dedicated WSL composition and capability checks. |
| Windows job execution and process cancellation | Native Windows implementation with an explicit shell and working directory. |
| Linux job execution and process cancellation | Linux implementation where its assumptions are satisfied. |
| Windows firewall, sleep policy, login startup | Explicit Windows capabilities. No implication that guest UFW configures the Windows host. |
| Windows/WSL/Ubuntu release upgrades and restarts | Explicit maintenance, coordinated with jobs and any affected distributions. |
| T3 desktop connection/runtime | T3 desktop owns its upstream WSL integration. |

Add a distinct `wsl` machine type: current
[`detect_machine_type()`](../../lib/machine_state.py) maps any recognized
non-container virtualization to `vm`, which grants inappropriate generic VM
capabilities. Detect WSL before that fallback while preserving detection of an
OCI container running inside WSL. Validate WSL 2 and systemd separately.

Use a small `server_wsl` composition in `plugins/`, with optional agents and
build capabilities. Do not run the full server hardening plan and hope that
individual steps fail harmlessly. Review each relevant capability:

- APT and ordinary userspace tools: supported after Ubuntu preflight.
- Linux system services: available when systemd is actually running; service
  installation is not proof of persistent Windows/WSL availability.
- Generic kernel hardening, bootloader/kernel package management, Linux swap,
  time-sync daemons, and Linux host reboot: omitted from the initial profile.
- Firewall enforcement: explicit Windows policy; any future guest policy must
  account for both layers and the selected networking mode.
- CPU, memory, and swap settings: Windows-side options. `.wslconfig` can affect
  other distributions owned by the user, so preserve existing settings and
  disclose the shared impact before a requested change.

This conservative policy does not claim that WSL lacks all kernel/firewall
features. It limits what Basaltwater initially manages and qualifies.

Implementation must cover config serialization and validation in `lib/config.py`,
CLI parsing, the plugin registry, installer packaging, and saved-state readers
alongside machine detection. Audit shared setup/bootstrap calls before reuse,
especially `lib/orchestrator_bootstrap.py`, APT source management, third-party
repository suites, package names, and systemd unit restrictions. Ubuntu is
currently a best-effort target; accepting its OS ID is not qualification. Never
install Debian repository definitions into Ubuntu. Add regressions ensuring the
new profile does not change existing Debian or CachyOS plans.

## Distribution, storage, and lifecycle

Prefer a clearly named managed Ubuntu distribution, such as
`Basaltwater-Ubuntu`, using an official image and a qualified install/import
path. Preserve the user's existing default distribution. Adoption of an
existing distribution requires explicit selection and validation; never
unregister, reset, rename, or overwrite a distribution as automatic recovery.
If the qualified image path cannot assign a custom name directly, document
the supported import procedure rather than invent WSL flags.

Keep Linux workspaces and caches in the Linux filesystem. Use native Windows
paths for Windows builds, with distinct checkouts and explicit artifact handoff
when a pipeline spans both. Do not share a mutable Git checkout or dependency
directory between Windows and Linux jobs. Validate artifact paths, links,
Windows reparse points, case collisions, and size limits at transfer boundaries.
Support a selected distribution storage location and report free space on the
backing Windows volume as well as inside Ubuntu. See Microsoft's
[filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

For v1, provide explicit start/stop and optional startup at user login using
the normal user token. Password/UAC prompts may pause maintenance. Closing a
terminal should not silently interrupt a worker advertised as available, but
logout, sleep, Windows restart, or WSL shutdown may make it unavailable. Record
interrupted jobs and require an explicit retry for work with side effects.
Do not promise boot-before-login availability or store a Windows password to
approximate it. Systemd alone does not keep WSL alive; qualify the Windows-side
worker lifetime. See Microsoft's
[systemd lifecycle note](https://learn.microsoft.com/en-us/windows/wsl/systemd).

Make AC sleep behavior an explicit server option; preserve desktop defaults and
battery behavior otherwise. Keep Windows Updates enabled. Basaltwater's own
updates and restarts should wait for active jobs, with a visible pending state;
external Windows updates can still interrupt work. Restart only the selected
distribution where possible, and explain when an operation affects all WSL
instances.

## Windows packages, T3, and agents

Use exact WinGet package IDs and explicit sources. Record package ownership,
installation scope, selected version, and observed version. Preflight installer
scope, restart requests, silent-install support, and availability per package.
Preserve existing installations and settings; refresh selected managed software
without a blanket `winget upgrade --all`. Assign a single update policy when
an application also has its own updater.

WinGet registration may be incomplete after a fresh login. Bootstrap or repair
it using Microsoft's supported path, then verify functionality. Prefer the
community `winget` source for the initial catalog rather than depend on Store
sign-in. Do not assume every installer supports every scope. The WinGet CLI is
not supported under `SYSTEM`; its PowerShell module supports machine-wide
packages in that context, which is relevant to future provisioning rather than
a v1 requirement. See [WinGet installation](https://learn.microsoft.com/en-us/windows/package-manager/winget/)
and [scope/context limitations](https://learn.microsoft.com/en-us/windows/package-manager/winget/troubleshooting).

Install optional T3 desktop via `T3Tools.T3Code` and preserve its state. The
operator selects Ubuntu in T3's connection settings; T3 installs its own WSL
runtime. Basaltwater installs selected provider CLIs in the chosen execution
environment and checks discovery there. Desktop use must not create an extra
managed T3 service or depend on a separate Basaltwater T3 login/pairing flow.
See [T3's installation and WSL guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md).

Agent capabilities are independent of T3. Reuse qualified Linux installers in
Ubuntu. Native Windows agents need a small explicit catalog of supported tools,
package IDs or documented vendor installation mechanisms, update ownership,
and readiness checks. WinGet remains the sole Windows package manager; a
provider unavailable through it needs a deliberate vendor-specific capability
or a clear unsupported result, not automatic Scoop installation. Do not
preinstall every agent. Provider authentication remains an interactive step
where appropriate and is preserved on rerun.

## CI/CD, testing, and basic management

Support both Linux and native Windows command-line jobs in staged delivery.
Choose the execution environment explicitly per job. Reuse source revision
validation, job identity, status, logs, artifact metadata, and deployment policy
where portable; use native execution adapters for shell invocation, process
trees, cancellation, environment, and filesystem behavior. Avoid routing
Windows-specific work through Linux merely to avoid PowerShell code.

The existing [CI/CD topology](../CICD.md) is a local webhook receiver/executor,
not a distributed Windows worker service. Its
[`lib/cicd_build.py`](../../lib/cicd_build.py) uses POSIX identities and `setpriv`.
Do not claim a Windows executor exists by installing PowerShell or replacing
`bash` in that code. Keep current Debian CI behavior and its credential
separation intact. Reuse that separation in WSL when using the existing
executor; sharing an account in a new trusted local runner does not require
weakening the existing broker.

Before implementing remote native jobs, qualify one small authenticated
submission path under the intended Windows user. Prefer an existing suitable
transport over a new always-on web service. Compare Windows OpenSSH with a
login-owned job launcher against a narrowly scoped local handoff from the WSL
executor. Test account/session identity, request validation, replay/duplicate
handling, results, artifact transfer, cancellation, and availability after
login. Choose one path in the implementation record; this is a bounded technical
spike, not permission to build a general distributed scheduler. Do not expose
an unauthenticated PowerShell command endpoint.

Linux CI can retain the existing webhook model once its WSL service and network
assumptions pass qualification. For inbound access, configure an explicit LAN
address/port and allowed sources with matching Windows firewall policy; prefer
loopback for local handoff. Choose NAT or mirrored networking based on the
qualified path, checking DNS and VPN behavior rather than silently changing the
user's global mode. See [WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking).

Each supported job path must handle immutable source checkout, command failure,
timeout, child-process cancellation, logs, exit status, bounded artifacts,
interruption, and retry. PowerShell scripts must propagate native tool failures.
Keep deployment as an explicit pipeline step using existing artifact/deploy
contracts; a build failure must never trigger deployment. Broader manifest
unification remains owned by [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md).

The selected native qualification workflow is a PowerShell job plus artifact
upload. Use an authenticated test destination and verify the uploaded bytes and
checksum. Include a separate deliberate failure that must not upload/deploy,
an upload failure, and cancellation; never target production for this fixture.
Linux qualification includes a real small build/test and artifact delivery.
GUI automation, unlocked desktop requirements, Windows driver/GPU tests, and
application-specific toolchains need separate acceptance cases; they are not
implied by command-line execution support.

Provide read-only status/doctor, setup recall, refresh, logs, and worker
start/stop. Report Windows/WSL/Ubuntu versions, owner/distro, systemd readiness,
package/agent provenance, auth readiness without secret values, disk capacity,
network reachability, job state, pending restart, and pending user actions.
Distinguish an unselected optional capability from a broken selected one.
Remote management must use the qualified authenticated access path; local
management remains available while remote access is unconfigured.

## Basic credential protection

These machines generally run trusted in-house work; sharing the ordinary agent
and build account is acceptable where it simplifies operation. File permissions
do not isolate credentials from code running as that same account. Document
that fact without introducing a mandatory vault or additional account system.

Run ordinary agents/builds without an elevated Windows token or Linux root.
Elevated setup must hand off to the original normal user context before work
starts. Use private Linux file modes and Windows ACLs, provider-owned credential
storage, redacted logs, and minimal child environments. Exclude credentials
from artifacts, caches, setup state, diagnostics, and command-line options.
Avoid automatic copying between Windows and Ubuntu homes. WSL/Windows interop
and mounted drives make the host part of the same trust boundary; this is not
an untrusted public-PR sandbox.

## Delivery sequence

1. **Capability and bootstrap foundation:** WSL detection, dedicated composition,
   config validation, read-only planning, Windows preflight, resumable installer,
   WinGet/WSL/Ubuntu installation. Demonstrate fresh install, reboot/resume, and
   a second run without duplicate accounts/distributions or destructive changes.
2. **Tools and agent desktop:** selected runtimes, agent catalog, native T3,
   credential hygiene, status/doctor, local maintenance. Demonstrate a T3 thread
   in Ubuntu and independent agent operation where selected.
3. **Linux build participation:** qualify existing CI pieces in WSL, login-owned
   startup/lifetime, authenticated access, artifact delivery, and interrupted-job
   behavior. Do not require a desktop T3 process for CI.
4. **Native Windows jobs:** finish the submission/identity spike, implement one
   Windows adapter, and prove success/failure/cancellation/artifact behavior.
   Native Windows pipeline support remains unqualified until this phase passes.
5. **Release qualification:** publish tested pasteable PowerShell/CMD commands,
   the support/version matrix, recovery instructions, and management examples.
   Update installation, machine types, CLI, CI/CD, agent, and packaging docs.

Commit coherent implementation slices with focused validation. These phases
must not silently grow into full Windows fleet management or a new CI service.
Future VM provisioning should call the same versioned setup/resume interface.
It must account for nested virtualization; see the
[WSL VM requirements](https://learn.microsoft.com/en-us/windows/wsl/faq#can-i-run-wsl-2-in-a-virtual-machine).

## Validation and acceptance

Automated tests mock system calls and use temporary directories. Python changes
follow the repository validators and `from __future__ import annotations` rule;
register new test modules in `run_tests.py`. Windows tests use isolated fixtures
for native command results, state, quoting, exit codes, and ACL decisions.
Host-changing qualification runs only on explicitly designated test machines.

| Case | Required evidence |
| --- | --- |
| Fresh Windows 11 | No developer prerequisites; PowerShell and CMD quick starts install selected capabilities after Windows Updates. |
| Accounts and editions | Local and Microsoft accounts; paths with spaces/non-ASCII; Home/Pro where selected features apply; separate unactivated and official evaluation images. Record exact editions/builds. |
| Bootstrap failures | Missing/unregistered WinGet, pending reboot, unavailable virtualization, failed download, insufficient disk, and interrupted installer give bounded, actionable results. |
| Recovery | Resume uses original options/revision; corrupt or mismatched state stops safely; reruns preserve credentials, packages, unrelated distros, and user configuration. |
| Capability policy | WSL is not ordinary VM/OCI; container-in-WSL detection remains correct; unsupported host tuning and reboot steps are absent. |
| Agent/T3 | Native desktop selects intended distro; agent discovered/authenticated there; a real thread works; no extra managed T3 service. |
| Linux/native jobs | Each implemented environment succeeds, reports deliberate failure, times out/cancels children, and delivers only intended artifacts. |
| Login lifecycle | Worker can start after login, survive terminal closure as documented, report offline/interrupted state after logout/reboot, and recover without claiming pre-login availability. |
| Credentials | No setup token leakage in state/logs/artifacts; ordinary job token is not elevated; existing Linux broker isolation remains intact. |
| Network/storage | Test the selected inbound or local handoff path, firewall scope, DNS, representative VPN, backing-volume exhaustion, path semantics, and interrupted transfers. |
| Maintenance | Selected updates preserve state, respect active jobs, and report pending restarts; existing other distributions remain usable. |

Record live evidence separately from mocked test results. A healthy service,
installed package, or successful dry run alone does not qualify the full flow.

## Review findings and remaining decisions

The initial design review identified and addressed these gaps:

- **Too much unattended scope:** boot-before-login and stored Windows passwords
  are removed from v1 requirements. Login and human prompts are acceptable.
- **Overextended Linux reuse:** native Windows execution needs its own adapter;
  POSIX broker assumptions and desktop session requirements are explicit.
- **Unnecessary identity complexity:** shared trusted-work accounts are allowed;
  preserve existing cheap Linux isolation and protect against accidental leaks.
- **Fresh-install dependencies:** bootstrap includes WinGet registration/repair,
  built-in PowerShell compatibility, elevation ownership, and reboot resumption.
- **Moving release target:** record resolved stable versions and qualify Ubuntu
  image availability; upgrades are separate from setup replay.
- **Hidden global effects:** `.wslconfig`, Windows sleep/firewall policy, shared
  WSL restarts, and existing distributions require deliberate handling.
- **T3 ownership conflict:** desktop owns its runtime; Basaltwater does not
  install a competing service.
- **Elevated entry point:** an administrator PowerShell window must not cause
  agents or builds to inherit an elevated token; normal-user continuation is
  an acceptable fallback.
- **Ubuntu compatibility:** audit shared APT sources, package assumptions, and
  service restrictions before reuse; OS detection alone is insufficient.

The user selected **a PowerShell job plus artifact upload** for first-release
native Windows qualification. No application-specific compiler or GUI test
framework is required. There are no unanswered product questions blocking this
plan. Resolve the native submission transport and official named-distro
installation path through the early technical spikes, and update this document
with the evidence. Runtime implementation is separate from this planning change.

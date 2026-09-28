# Ubuntu WSL support on Windows 11

Status: ready for implementation. Windows support and live qualification have
not yet been delivered.

## Scope

Prepare a fresh, fully updated Windows 11 machine for in-house CI/CD, builds,
testing, and optional agentic development from one pasted installer command.

| Area | Requirement |
| --- | --- |
| Platform | Windows 11 x86-64 in desktop or server roles; latest stable WSL 2 and Ubuntu available through the official WSL distribution channel, fully updated. |
| Starting point | Logged-in administrator account with internet access; no Git, Python, PowerShell 7, WSL, or Ubuntu prerequisites. Interactive passwords and UAC are acceptable. |
| Availability | Operation after user login is sufficient; provide explicit start/stop and optional startup at login. |
| Software | WinGet for native Windows packages; reuse Linux tooling in Ubuntu. Select agents and runtimes independently. |
| T3 Code | Optional native Windows desktop app. Let T3 own its WSL runtime; install no separate managed T3 service. |
| Workloads | Linux jobs and native Windows command-line jobs. Native qualification uses a PowerShell job plus artifact upload. |
| Trust | In-house code on machines isolated by purpose. Agent and build accounts may be shared, with basic credential protection. |
| Test images | Include unactivated and official evaluation installations; activation is not a setup prerequisite. |

Outside this release: Windows ARM, Windows Server editions, pre-login operation,
GUI test automation, and Windows VM provisioning. Keep the installer reusable
by a future VM provisioner.

## Installer contract

Add `install.ps1` compatible with built-in Windows PowerShell 5.1. Provide
pasteable PowerShell and CMD examples using the same installer.

Proposed interface, not yet runnable:

```powershell
$installer = Join-Path $env:TEMP ("basaltwater-" + [guid]::NewGuid() + ".ps1")
Invoke-WebRequest `
  "https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1" `
  -UseBasicParsing -OutFile $installer -ErrorAction Stop

powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer `
  -Profile server_wsl -BuildServer -T3CodeDesktop -Node -Python
```

Support read-only `-Plan`, `-Resume`, release/channel selection, distribution
name/location, Linux username, and capability options. Make Windows versus WSL
tool selections explicit. Keep execution-policy changes process-scoped.

The installer must:

1. Check Windows version/architecture, virtualization, disk capacity, and pending
   restarts; bootstrap or repair WinGet and install stable WSL.
2. Stage a verified source revision and durable resume launcher. Save validated,
   non-secret options, owner SID, distro identity, and completed phases.
3. Request elevation only for machine changes. Register WSL and install desktop
   software for the intended user; run agents/builds without elevation. An
   already-elevated invocation may require normal-user continuation.
4. Stop cleanly for required reboots and resume after login with the same options
   and source revision. Do not force reboot or configure auto-login.
5. Create a named Ubuntu distribution from an official image, or adopt one
   explicitly selected by the user. Preserve other distributions and defaults.
6. Apply selected Windows and Linux capabilities, check readiness, and record
   success separately from incomplete operation state.

Reruns must be idempotent. Reject concurrent setup and invalid resume state;
preserve credentials and existing data after failure. Check native exit codes
and validate arguments across PowerShell, elevation, and WSL boundaries.
Resolve exact stable versions at setup; Ubuntu release upgrades are explicit
maintenance, not an incidental effect of resuming or rerunning setup.

## Implementation sequence

### 1. WSL capabilities and bootstrap

Add a `wsl` machine type in [machine state](../../lib/machine_state.py) and a
focused `server_wsl` plugin composition, following [CachyOS](../CACHYOS.md).
Update configuration, CLI parsing, serialization, saved-state validation, and
packaging together. Preserve OCI detection for containers running inside WSL.

Keep Linux mutations behind `remote_setup.py` and the plugin registry; Windows
features, applications, firewall, power settings, and WSL lifecycle belong in
the Windows companion. Reuse Ubuntu-compatible package/runtime steps after
auditing APT sources, repository suites, package names, and service restrictions.

Require WSL 2 and working systemd. Omit generic VM kernel/bootloader hardening,
Linux swap, time-sync daemons, and Linux host reboot steps. Preserve existing
`.wslconfig`; resource or network changes must disclose their effect on other
distributions. Qualify the official named-distribution installation path as
part of bootstrap implementation.

**Complete when:** a fresh host installs, reboots/resumes, and reruns successfully
without damaging existing state; Debian and CachyOS setup plans remain unchanged.

### 2. Tools, agents, and T3 desktop

Install selected Windows software through exact WinGet IDs and sources,
recording scope/version and update ownership. Update only selected managed
software. Install T3 through `T3Tools.T3Code`; preserve its settings and let the
user select Ubuntu in its connection settings.

Reuse qualified Linux agent installers in WSL. Native Windows agents need
explicit installation and readiness checks. Preserve provider authentication
and report required login steps without storing secrets in setup state.

**Complete when:** selected tools work in the intended environment, an independent
agent runs, and a real T3 thread operates in Ubuntu without an extra T3 service.

### 3. CI/CD and management

Reuse the [Linux CI/CD flow](../CICD.md) where compatible. Implement native
Windows execution separately from the POSIX identity handling in
[the existing executor](../../lib/cicd_build.py). Choose and qualify one
authenticated job-submission path under the intended Windows user as the first
task in this phase; retain shared source, result, and artifact contracts.

Specify the execution environment, shell, working directory, and source revision
per job. Handle native exit codes, bounded logs/artifacts, timeout, process-tree
cancellation, interrupted work, and explicit retries. Verify upload checksums;
failed builds must not upload release artifacts or deploy.

Keep Windows and Linux checkouts/caches separate on their native filesystems.
Validate transferred paths, links/reparse points, and artifact size. Configure
remote access with explicit authentication and Windows firewall scope; preserve
the user's networking mode unless a change is requested.

Provide status/doctor, setup recall, refresh, logs, and worker start/stop.
Support login-owned startup and worker survival after terminal closure;
logout, sleep, reboot, or WSL shutdown may interrupt availability. Report that
state accurately. Keep Windows Updates enabled, make AC sleep changes explicit,
and defer Basaltwater maintenance while jobs are active.

**Complete when:** a Linux build/test and a native PowerShell job each upload a
verified artifact, report failures correctly, and recover after login.

### 4. Qualification and operator documentation

Test on designated Windows machines and publish exact OS/WSL/Ubuntu/tool
versions with the results. Replace the illustrative installer command with
tested PowerShell and CMD examples. Update installation, machine types, CLI,
CI/CD, agent, and recovery documentation.

| Test | Required result |
| --- | --- |
| Fresh installation | Works without developer prerequisites, including absent/unregistered WinGet. |
| Host variants | Local and Microsoft accounts; Home/Pro where features apply; unactivated and evaluation images; paths with spaces/non-ASCII. |
| Failure and resume | Downloads, disk exhaustion, missing virtualization, reboot, and interrupted setup produce actionable results; reruns preserve data. |
| Tools and T3 | Correct environment, account, tool discovery, authentication, and desktop connection. |
| Jobs and upload | Success, deliberate command failure, upload failure, cancellation, and checksum verification against an authenticated test destination. |
| Lifecycle and access | Login startup, terminal closure, logout/reboot interruption, firewall scope, DNS, and representative VPN behavior. |
| Maintenance | Updates preserve settings/credentials, respect active jobs, and report pending restart. |

Automated tests must mock host changes and use temporary directories. Follow
repository validators and register Python test modules in `run_tests.py`.
Keep live qualification distinct from unit tests and dry runs.

## Credential protections

Run ordinary agents/builds as non-root, non-elevated users. Use private Windows
ACLs/Linux file modes and provider-owned credential storage. Exclude secrets
from commands, saved options, logs, diagnostics, caches, and artifacts; do not
automatically copy credentials between Windows and Ubuntu.

Shared-account jobs can access that account's credentials; this is a trusted
workload environment. Retain existing Linux broker isolation when reusing it
without adding mandatory identity infrastructure for native Windows jobs.

## Implementation references

- [WinGet installation](https://learn.microsoft.com/en-us/windows/package-manager/winget/)
  and [scope/context limitations](https://learn.microsoft.com/en-us/windows/package-manager/winget/troubleshooting)
- [WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands),
  [systemd lifecycle](https://learn.microsoft.com/en-us/windows/wsl/systemd),
  [networking](https://learn.microsoft.com/en-us/windows/wsl/networking), and
  [filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems)
- [T3 desktop and WSL integration](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md)

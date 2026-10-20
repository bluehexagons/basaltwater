# Next phase: native Windows development and runners

Status: proposed implementation scope, based on the Windows foundation available
on 2026-10-19. Native-only setup and the qualification described here are not yet
implemented. Windows remains outside the current v2.0.0 stable-release gate;
changing that requires an explicit update to the
[release matrix](../release-qualification.md).

## Direction and intended result

Make WSL optional for a managed Windows target. Prioritize native Windows
application development and builds: custom game engines, Godot, Electron, and
ordinary desktop applications. The applications themselves need no WSL
dependency. Keep the existing Debian/Linux orchestration host and reuse shared
policy and declarations where practical; a complete native port of the
`basaltw` CLI and Debian services is separate work.

Qualify two roles independently:

- **Development workstation:** native coding tools and an interactive desktop,
  with a native checkout, build, launch, debugging, and test loop.
- **GitHub runner:** native Windows jobs on a dedicated CI identity, with the
  existing author policy, bounded execution, and documented restart behavior.

Use the same setup components for both roles, with explicit tool selections.
Prefer separate VMs for continuing use. One disposable VM may exercise both
roles during qualification, but shared user credentials are then accessible to
trusted job code. Add WSL only when Linux tooling or Linux builds are wanted.

## Existing foundation

The [Windows/WSL guide](../workstations/windows-wsl.md) and
[Windows runner guide](../workstations/windows-github-runner.md) remain the
references for current operator behavior.

| Area | Implemented today | Next boundary |
| --- | --- | --- |
| Bootstrap | [`install.ps1`](../../install.ps1) supports Windows 11 x64, WinGet repair/install, a pinned source revision, Ubuntu WSL 2, owner state, and restart/resume. | Split native prerequisites from optional WSL installation. Current `-Node`, `-Python`, and `-Go` selections install inside Ubuntu. |
| Coding | Native T3 Code desktop installation and a managed WSL tool environment. | Install and qualify selected providers against native Windows shells, paths, and workspaces. |
| Runner | Official Windows runner, repository/organization registration, login tasks, shared job slot, process cleanup, and native workflow execution. | [`GitHubRunner.Common.ps1`](../../windows/GitHubRunner.Common.ps1) still requires completed WSL state and invokes its Python gate through `wsl.exe`. Remove that dependency. |
| Native build jobs | [`Invoke-BasaltwaterJob.ps1`](../../windows/Invoke-BasaltwaterJob.ps1) supplies bounded execution, artifacts, receipts, and Windows-user credential protection. | Reuse its contracts for CI; retain an ordinary local development loop for uncommitted edits. |
| Windows checks | Hosted PowerShell checks and a manual self-hosted WSL lane in [`windows-manual.yml`](../../.github/workflows/windows-manual.yml). | Add native policy/runtime checks and real Windows 11 qualification without WSL. Hosted checks do not establish desktop readiness. |
| Proxmox | Existing VM provisioning is Linux cloud-init oriented and selects Linux guest settings. | Introduce a distinct Windows template/provisioning path; `--vm-image` is not a Windows installer contract. |

## Support boundary and code reuse

Start with Windows 11 x64 on Proxmox. Select the supported edition, serviced
build, and Proxmox baseline before live qualification. Windows Server and ARM64
need separate commitments and test cases.

| Debian capability | Native Windows treatment in this phase |
| --- | --- |
| Package/runtime setup | PowerShell adapters with WinGet first, plus supported vendor installers where component selection requires them. |
| Git, coding tools, project builds | Native tools and NTFS workspaces; project configuration owns dependency and compiler requirements. |
| GitHub identity policy | Share pure policy/schema logic and test vectors; supply Windows state, ACL, locking, and process adapters. |
| Job logs and artifact evidence | Reuse the native job helper and common receipt expectations. |
| Workspace and memory helpers | Native Git/worktrees initially. Existing helpers use POSIX ownership and locks; do not claim native parity until adapted and tested. |
| Background agents and privileged actions | Use qualified provider capabilities initially. A native Basaltwater task daemon, approval executor, and desktop automation interface require separate designs. |
| Linux services and host configuration | Keep Debian hardening, systemd, XRDP, Linux storage mounts, and deployment gateway behavior on their existing platforms. WSL gets only its explicitly supported subset. |

Extract the runner's portable policy from modules that currently pull in `pwd`,
POSIX file flags, or ownership checks. Keep one implementation of authorization
rules, with native Python execution on Windows and the same behavioral tests
on Debian. Determine runtime packaging under Q04. Windows adapters must handle
ACLs, reparse points, atomic replacement, locking, and process lifetimes
explicitly; replacing Unix calls with no-ops is not a port.

Preserve the current private-repository, allowlisted-human, event/identity, and
first-attempt restrictions. Preserve denial before workflow steps, including
`if: always()` steps, and slot ownership through worker cleanup. The hook is a
local guard on a trusted persistent runner, not a sandbox for hostile jobs.
Removing WSL must not weaken these contracts.

## Automation contract

The target is one declarative selection and one resumable setup invocation
after Windows installation. Setup should discover prerequisites, install and
configure selected tools, verify them in the intended account, and report any
remaining action. Profile/flag names are undecided; examples must not imply
that proposed options already exist.

### Bootstrap and package management

1. Preflight the OS/build, architecture, account SID, elevation needs, disk
   budget, pending restart, and selected role. Produce a read-only plan before
   changes, including package identities and reboot boundaries.
2. Establish App Installer/WinGet for the intended setup user. WinGet may be
   unavailable until first login and registration completes; retain the repair
   path and qualify fresh-user behavior. See Microsoft's
   [WinGet installation guidance](https://learn.microsoft.com/en-us/windows/package-manager/winget/#install-winget).
3. Select exact package IDs and sources, architecture, supported installation
   scope, and a recorded version/channel policy. Use silent installation and
   disabled interactivity where supported, with configured source/package
   agreement acceptance. See the
   [WinGet install contract](https://learn.microsoft.com/en-us/windows/package-manager/winget/install).
4. Detect existing installations and verify executable location, version, and
   required features. Map installer exit codes explicitly, including restart
   required and already satisfied. Package presence alone must not mask a
   failed or incompatible install. Retain installer signature/hash checks and
   pinned runner download verification; fail on a verification mismatch.
5. Refresh/discover the environment for new processes, then run functional
   checks as the development or runner identity. An administrator's PATH or
   per-user installation does not prove a service account can use a tool.
6. Save versioned state and bounded, redacted logs after verified steps. Resume
   against the same source revision and selections after interruption/reboot;
   prevent concurrent setup and reject invalid state or account changes.

Separate privileged machine installation from user configuration. Do not run
the coding agent or workflow worker as administrator to simplify setup.
Do not reboot an active workstation or runner implicitly: record the pending
boundary, drain work, and resume through an agreed maintenance action.

Manage only selected tools. Updates should show proposed changes and verify
the resulting toolchain; avoid global `winget upgrade --all`, automatic project
dependency upgrades, or removal of unrelated installations. Record which
changes setup owns and how to repair or remove them. Package installation is
not a fully reversible transaction.

### Native tool bundles

| Selection | Automated setup and verification |
| --- | --- |
| Base development/CI | Git, Git LFS where selected, PowerShell, and GitHub CLI when registration requires it; verify under the actual account. |
| Coding system | Selected native provider CLI/application and T3 Code; configure native shell/workspace use and expose a separate authentication/enrollment step. |
| C/C++ and custom engines | Selected MSVC Build Tools, Windows SDK, CMake/Ninja, and optional LLVM. Discover the developer environment and compile/link/run a small native executable. |
| Node/Electron | Selected native Node version and project package manager; install from the project lockfile, [rebuild native dependencies for the Electron ABI](https://www.electronjs.org/docs/latest/tutorial/using-native-node-modules), package and launch the app. |
| Godot | Project-matched Windows editor and [export templates](https://docs.godotengine.org/en/stable/tutorials/export/exporting_projects.html); optional .NET tooling for C# projects; headless export plus an interactive Windows launch. |
| Additional languages | Native Python, Go, .NET, or Rust only as selected; distinguish project runtimes from any private runtime needed by Basaltwater itself. |

Use Visual Studio's supported component/workload configuration and unattended
installer options, through WinGet overrides or the vendor installer as
appropriate. Verify the required compiler and SDK rather than the product's
presence. The vendor supports additive `.vsconfig` installation and controlled
restart behavior; see
[Visual Studio installation parameters](https://learn.microsoft.com/en-us/visualstudio/install/use-command-line-parameters-to-install-visual-studio).
Choose exact package/component versions during implementation, not by treating
the latest engine or compiler as compatible with every project.

Keep Windows source and build outputs on NTFS, for example `C:\work`. If WSL
is selected, use a separate Linux checkout and dependencies for Linux builds.
Do not share `node_modules`, virtual environments, or compiler caches between
operating systems. Define path conversion only at explicit handoff points.
Test spaces, Unicode, long paths, Git line endings, symlinks, and Git LFS.

### Credentials and unavoidable interaction

Automate configuration around credentials without baking credentials into
templates or setup declarations. Provider sign-in, Git access, runner
registration, and optional code signing remain explicit enrollment inputs.
Keep secrets out of command lines, logs, receipts, and ordinary state; retain
protected references or account-bound storage where appropriate.

Document every remaining manual action: Windows licensing/enrollment, initial
privilege consent, authentication, and any selected reboot. Qualification must
measure this list and identify which steps can become unattended with a
pre-authorized deployment environment. Do not reduce automation to a package
list followed by manual SDK, task, or PATH repair.

## Proxmox preparation and lifecycle

Automate Windows guest preparation as a separate work package. The first
native-role prototype can use a manually installed VM; the phase's automation
target is a reproducible template and clone/bootstrap procedure.

| Concern | Proposed baseline and qualification requirement |
| --- | --- |
| Virtual hardware | Windows guest type, Q35, OVMF/UEFI, persistent EFI variables, Secure Boot configuration, TPM 2.0 state, and a Windows-supported CPU. Verify signed boot and device drivers. |
| Storage/network | VirtIO drivers available during OS installation; install guest tools and QEMU Guest Agent, enable its Proxmox channel, and verify shutdown and address reporting. |
| Resources | Start CI trials around 4–8 vCPU, 16 GiB RAM, 160 GiB SSD; development around 8 vCPU, 32 GiB RAM, 256 GiB SSD. These are estimates to measure, not product minima. Budget build caches and artifacts. |
| WSL option | Enable nested virtualization only for roles needing it, expose the required CPU extensions, and qualify WSL 2 boot/restart. Native-only roles must work with WSL and its optional features absent. |
| CPU/migration | Choose host CPU exposure for performance or a tested common baseline for migration. Qualify the actual hosts; avoid accidentally shipping binaries restricted to the build VM's CPU. |
| Graphics | Separate build/headless qualification from real Direct3D/Vulkan playtesting. Qualify the selected GPU/driver and remote-display path, including input, audio, capture, and disconnected sessions. |
| Updates/restarts | Coordinate Windows, drivers, toolchains, and runner updates with job drain and recovery. Keep Defender and Windows Update enabled; scope any necessary exclusions to measured problems. |
| Backup/restore | Include required OS/data, EFI, and TPM volumes; retain BitLocker recovery material separately if enabled. Test restore, boot, credentials, and runner re-registration. |

Use the installed Proxmox release's
[QEMU/Windows guest guidance](https://github.com/proxmox/pve-docs/blob/master/qm.adoc)
and Microsoft's
[Windows 11 requirements](https://learn.microsoft.com/en-us/windows/whats-new/windows-11-requirements)
to finalize the template. Ordinary GPU passthrough constrains host placement
and migration; Proxmox console display does not capture the passed-through
GPU's framebuffer. Select an in-guest remote display or physical output and
qualify it using the
[PCI passthrough guidance](https://github.com/proxmox/pve-docs/blob/master/qm-pci-passthrough.adoc).

Choose an unattended installation/template builder under Q06. Generalize
reusable Windows images using the supported
[Sysprep workflow](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/sysprep-command-line-options?view=windows-11).
Keep templates free of runner registrations, provider/Git credentials, signing
keys, and user-bound protected secrets. Assign clone identities and enroll
each clone independently. Test WinGet/App Installer provisioning after
generalization and first login.

Separate the headless runner lifecycle from interactive desktop use. Current
runners start at owner login. A future service mode must be explicitly selected
and tested before promising operation at boot without login. Windows services
run in session 0 and cannot directly provide an interactive user desktop; see
[interactive service limitations](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).
Use an explicit interactive mode for GUI tests; test login, lock, RDP
disconnect, logout, and restart. Avoid making automatic login a hidden
prerequisite. Confirm edition-specific remote desktop capability and licensing
rights for the actual VM and access arrangement before template rollout.

## Delivery sequence

| Milestone | Work and exit condition |
| --- | --- |
| WIN-01: Native setup contract | Resolve Q01–Q04. Add native role/state selection, package contracts, plan/resume and functional verification. A fresh Windows 11 VM completes base setup without installing WSL; repeat setup makes no unintended changes. Existing WSL setup and resume still work. |
| WIN-02: Native runner | Extract/test shared policy, provision its native runtime, and remove the required WSL state/path/gate calls. Preserve registration, account checks, denial, slot, cleanup, and credential contracts. Qualify interactive mode; add boot service mode only after its account/lifecycle decision and tests. |
| WIN-03: Coding and build bundles | Qualify a selected native coding provider plus T3 workflow and native C/C++, Godot, and Electron examples. Document supported versions, native checkouts, and project-owned commands. WSL is an independently selectable addition. |
| WIN-04: Reproducible Proxmox deployment | Resolve template and remote-access decisions; automate OS/template preparation, drivers, guest agent, clone identity, and role bootstrap. Recreate both roles from a clean clone with only documented enrollment inputs. |
| WIN-05: Support qualification | Complete the matrix below, record remaining manual steps and limitations, and update operator guides. Claim only the tested role, OS/build, toolchain, and graphics combinations. |

WIN-02 and WIN-03 depend on the native setup contract. Template work can begin
alongside them after its design decision, then consume the same bootstrap.
Keep Windows-specific code in the target adapters; reuse Linux orchestration
and portable policy instead of importing the entire POSIX runtime on Windows.
At WIN-02, review the amount of Windows-specific code and ongoing qualification
cost before expanding parity. If the two initial roles require a broad runtime
rewrite, revisit that scope and document the tradeoff before proceeding.

## Required qualification

| Case | Required evidence |
| --- | --- |
| Native-only clean install | Both roles on Windows 11 x64 with no WSL distribution or enabled WSL dependency; package versions, functional checks, and remaining enrollment actions recorded. |
| Setup recovery | Already-installed and incompatible packages, failed downloads/installers, pending reboot, interrupted setup, resume, repeat invocation, account mismatch, and invalid state. No success from presence-only checks. |
| Agent development loop | Agent edits a native checkout, builds and tests, launches the Windows application, and inspects relevant results. Authentication and child tools work under the intended user; local dirty-tree development works. |
| Representative applications | C/C++ compile/link/run, Godot export and native launch, Electron package/launch and a native dependency. Separate automated/headless results from GUI/rendering results. |
| Runner policy | Existing supported triggers and allowed/denied/malformed identities on real GitHub jobs; denial executes no workflow step, including `if: always()`. Shared vectors pass on native Python and Debian. |
| Runner lifecycle | Repository and organization registration, multiple repositories sharing one slot, cancellation, worker/guardian failure, cleanup, task/service restart, tool visibility, upgrade and re-registration. Test chosen boot/login mode. |
| Desktop and GPU | Lock, RDP disconnect, reconnect, logout, resolution changes, selected graphics backend, driver and remote display. A successful build does not qualify interactive game testing. |
| WSL compatibility | Existing fresh install/resume, distro ownership, native jobs, and runner behavior; adding optional WSL to a native target cannot invalidate its state or silently change tool selection. |
| Proxmox lifecycle | Clean clone, guest agent, graceful shutdown, backup/restore including EFI/TPM state, selected CPU baseline, and the migration restrictions of the actual GPU/nested-virtualization configuration. |
| Evidence and secrets | Bounded logs/artifacts and verified receipts; no credential leakage into state, arguments, templates, or reports. Record source revision, OS/build, role, tools, and test disposition. |

Extend hosted Windows CI with native Python policy and PowerShell adapter tests;
mock system mutations there. Use disposable Windows 11 Proxmox guests for real
installation/reboot and workload checks. Keep native-only and WSL lanes
distinct so a hidden `wsl.exe` dependency cannot pass unnoticed. Windows runner
labels and workflow examples must reflect actual capabilities; optional WSL
does not make GitHub's Linux container-job semantics available on a Windows
runner.

## Outstanding questions

Defaults below are proposals, not completed decisions. Resolve each question
before the milestone that depends on it; record the decision in its
implementation issue and update this plan when scope changes.

| ID | Question | Proposed starting point / decision boundary |
| --- | --- | --- |
| Q01 | What is the supported Windows edition/build, and what are the native role and runtime option names? | Windows 11 x64; confirm Pro or another suitable edition for remote access. Preserve current WSL option semantics and saved-state compatibility. Decide before WIN-01. |
| Q02 | Which native coding providers and T3 workflow must qualify first? What native context/workspace features are required? | Select one complete provider/edit/build/test loop first; list provider-specific gaps rather than promising Debian agent-helper parity. Decide before WIN-01; qualify in WIN-03. |
| Q03 | Which package versions, sources, scopes, and update rules are supported? Is PowerShell orchestration sufficient or is WinGet Configuration useful? | Reuse the existing PowerShell entry point with declarative package data. Add another configuration engine only if it reduces ownership and resume code. Set the initial contract before WIN-01. |
| Q04 | How is the shared gate's native Python runtime installed, isolated, and updated? | Keep policy in Python; compare a managed private runtime with a qualified native installation. Do not depend on a project venv or the runner user's incidental PATH. Decide before WIN-01. |
| Q05 | Must headless runners operate before login in the first supported native release? Which account owns them? | Prefer a dedicated unprivileged CI identity and explicit service mode for headless work; retain interactive mode for GUI jobs. Qualify credential storage, task/service ACLs, drain, and restart before WIN-02's support claim. |
| Q06 | How far does Proxmox automation extend: ISO installation, template build, clone, and guest enrollment? Which transport delivers the first setup? | Deliver a reproducible unattended/template procedure and clone bootstrap in WIN-04. Evaluate Windows answer files and an appropriate template tool; evaluate Cloudbase-Init only for an explicit Windows path. Keep existing Linux provisioning unchanged. |
| Q07 | Which hosts need live migration, and which have usable GPU passthrough or another qualified graphics solution? | Keep a migratable build VM and a separately qualified graphics workstation where practical. Select CPU, GPU, drivers, and remote-access method before WIN-04. |
| Q08 | Which sample projects and exact compiler/SDK, Godot, Electron, and native-addon versions define support? | Use small independent fixtures representative of general Windows applications, plus a real workload where available. Decide before WIN-03. |
| Q09 | Are packaging/signing, native GUI automation, or unattended agent scheduling required for the first phase? | Include unsigned build/package smoke tests and documented human GUI qualification first. Scope signing credentials and additional automation independently before claiming those features in WIN-05. |
| Q10 | What constitutes normal support, and when does Windows join a release gate? | Require both roles' native-only evidence and WSL regression evidence for the declared scope. Assign an owner and repeat cadence for live tests; amend the release matrix only through an explicit scope decision. Decide before WIN-05. |

The immediate implementation brief should cover WIN-01 and WIN-02, with resolved
Q01–Q05 and explicit acceptance cases. Broader native service/desktop parity,
Windows Server, ARM64, disposable untrusted runners, and fleet-wide Windows
management remain separate scopes.

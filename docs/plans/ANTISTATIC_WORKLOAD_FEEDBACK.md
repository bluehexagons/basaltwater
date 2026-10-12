# Antistatic workload handoff

Updated 2026-10-12. Implement remaining feedback through the existing owners
linked below. Start with read-only discovery; qualify refresh/recovery before
further live host mutation.

## Verified baseline

Debian's refreshed setup supports Antistatic native builds, full tests and
rendered agent-play. Source/setup receipts matched `443c844` at inspection;
this does not identify code loaded by long-running services. No current VM
development blocker or new OOM event was observed.

CachyOS builds and native/graphics smoke checks also pass. The Antistatic owner
resolved the isolated Clang-Tidy fixture failure with explicit checks for newer
LLVM tools. CachyOS intentionally supplies distribution LLVM; its doctor reports
observed unversioned tool versions without asserting project pin compatibility.
Both hosts' observed llvmpipe captures verify software rendering for those
captures; this does not qualify every native fixture. SDL offscreen/EGL on
CachyOS can choose hardware despite software environment flags. Physical GPU,
HDR and latency remain unqualified.
Local follow-up passed all seven C lint script fixtures and the god-ray X11
fixture, which reported llvmpipe (LLVM 23.1.1). This invocation's god-ray
offscreen test also reported llvmpipe while emitting the EGL hardware-device
warning; neither warning nor environment flags alone qualify its renderer.
Other forced-offscreen fixtures remain outside that software qualification.

Delivered: broker execution/approval fixes, memory/OOM context (`009395f`),
bounded maintenance captures and identity-checked cleanup (`8be57cd`), GTK
runtime selection (`443c844`), project-aware workstation discovery (`59acb12`)
and native tool version inventory (`b0b962a`). Preserve these contracts.

## Remaining Basaltwater actions

### 1. Expose project-selected runtime in discovery

Owner: [project tooling](../PROJECT_TOOLING.md).

Delivered through `agent manifest --project-runtime`: optional selection context
reuses the Node doctor and keeps PATH inventory distinct from project-selected
Node/npm. It includes metadata source, observed versions, compatibility issues
and explicit pin status. Missing runtimes retain declarations with a null
selection. Version output/process lifetime and installed-runtime discovery are
bounded; no installs, project execution or PATH/default-alias changes occur.
Fixtures cover differing PATH/project selections, missing pins/runtimes, failed
probes and oversized inventories. CachyOS validation is local; Debian live
qualification belongs to the separate development system.

### 2. Complete disposable refresh/recovery qualification

Owners: [privilege qualification](PRIVILEGE_EXECUTION_QUALIFICATION.md) and
[T3 lifecycle](T3_CODE_AGENT_VM_IMPROVEMENTS.md).

Run the existing matrix on an allocated disposable Debian/systemd target:
private-umask source activation, interrupted/failed steps, policy/authentication
permissions, approval origin, service restart and recovery. Include maintenance
holds around builds, captures, unpushed work and disconnected clients; qualify
descendants retaining pipes after their leader exits. Verify T3/desktop reconnect
and a native capture afterwards. Keep uncertain jobs unreplayed and retain the
original recovery snapshot until the matrix passes. Historical refresh failure
is separate from the restored development baseline.

### 3. Summarize CLI versus managed/service provenance

Owner: [CLI host audit](AGENT_CLI_MAINTENANCE_AUDIT_2026-08-09.md).

Delivered through optional `agent manifest --installation-context`, with an
explicit `--managed-source` override for custom installations. It compares
invoked/managed revisions and activation/snapshot receipts; CachyOS additionally
provides its last successful setup source and observed managed-web unit identity.
Developer launchers are preserved, and unavailable revisions remain unknown.
Fixtures cover older/missing receipts, differing revisions, unsafe/bounded
metadata and unavailable services. Existing job-correlated failure categories
remain with their owner. Loaded service revision receipts and the interrupted
activation/recovery matrix still need qualification on the separate system.

### 4. Provide optional private artifact storage

Owners: [T3 storage](T3_CODE_AGENT_VM_IMPROVEMENTS.md) and
[VM storage](VM_MANAGEMENT_AND_LIGHTWEIGHT_GIT_HOSTING.md).

Provision/discover an explicit private artifact root. Validate ownership, path
components, mount identity, bytes/inodes and collisions with public, credential
or temporary roots. Test reruns, ownership conflicts, absent mounts and full
destinations. Report source/destination filesystem capacity separately.
Projects retain archive selection, active-work exclusion, integrity checks,
links and recovery; host retention must not delete project evidence by age.

Lower priority: define/test literal IPv6 approval-origin support; investigate
SQLite fixture ResourceWarnings only if reproduced. Keep cache cleanup bounded
and owner-scoped, preserving useful SDK/dependency caches.

## Return to the Antistatic owner

- Preserve the explicit Clang-Tidy fixture checks and reviewed lint/format
  baseline. LLVM 19 preference and distribution LLVM readiness are separate.
- Verify each graphics fixture's actual driver/renderer. The god-ray fixture
  honors X11 for Xvfb/llvmpipe coverage; forced-offscreen fixtures need their own
  evidence. Basaltwater's GLX doctor now supports `--require-software`, which
  qualifies only its probe context and cannot qualify an application's EGL path.
- Use project-selected `npm ci` to correct checkout dependency drift; Basaltwater
  provisioning intentionally does not install repository dependencies.
- Measure ccache benefit before choosing a CMake compiler-launcher default.
- Consider a graphics recipe in `basaltwater-agent.json`; CI/deploy metadata in
  `basaltwater.json` is separate. Projects own renderer/settings/assertions and
  must build/publish independently of Basaltwater.

## Evidence

Antistatic's ignored `local-artifacts/projects/` contains the detailed reports:
`2026-10-12-environment-enhancements` (Debian) and
`2026-10-12-basaltwater-workstation-review` (CachyOS). Update the owning plans
with implementation and qualification outcomes; keep raw logs/captures private.

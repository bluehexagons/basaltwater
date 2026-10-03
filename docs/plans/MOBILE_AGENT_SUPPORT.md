# Potential Android and iOS support for coding agents

Status: proposal, 2026-10-02. Android implementation is explicitly deferred.
iOS is an unscheduled design proposal. This plan adds no setup flags, SDK
downloads, device hosts, simulator launches, or agent-device permissions.

## Objective and current boundary

Let a coding agent build and verify a mobile application through T3's shared
Device panel while keeping toolchain provisioning, device authority, project
builds, and evidence separate. Reuse the managed T3 service, host diagnostics,
SSH enrollment, workspaces, and gateway contracts.

T3 already owns device discovery, streaming, control, and pinned device tools.
Opening its Device panel alone does not install or start tools; after access
is enabled, the first listing of a configured SSH host can install those tools.
Its **Test connection** and **Check device tool versions** controls are the
read-only preflight paths. Closing a device tab or disabling the hub leaves
simulators running. Builds, app installation, and development-server routes
are separate. See the [T3 v0.0.45 device guide](https://github.com/pingdotgg/t3code/blob/v0.0.45/docs/user/devices.md).

Basaltwater's existing `--device-pairing` enrolls T3 clients. It is unrelated
to emulator provisioning or permission to control a simulator. Browser viewport
presets test responsive layout and do not establish native mobile coverage.

## Host and toolchain model

| Target | Proposed execution host | Qualification boundary |
| --- | --- | --- |
| Android Emulator | Explicit local Linux device host, or an enrolled Linux/macOS SSH host | SDK tools, system image, AVD, acceleration, rendering, capacity, and user access must all be qualified |
| iOS Simulator | Operator-provided Mac reached from the environment server over SSH, or an existing macOS T3 environment | Xcode, selected developer directory, simulator runtime, architecture, non-interactive SSH, and capacity |
| Physical Android/iOS devices | Later independent proposal | USB/network pairing, signing, trust, and reset behavior differ from disposable simulators |

For Android, T3 currently expects Platform-Tools, Android Emulator, command-line
tools, and a configured virtual device. A custom SDK needs `ANDROID_HOME`.
For SSH hosts, T3 v0.0.45 requires Node 22 or newer and npm available to
non-interactive SSH commands. Treat these as pinned upstream requirements to
recheck when implementation begins, not a permanent minimum-version promise.

Android's documented Linux accelerator is KVM. Its upstream documentation
does not support VM-accelerated emulators inside another VM. Use an eligible
bare-metal SSH device host as the baseline; nested Proxmox guests are a separate
experiment requiring measured qualification and must not become an implicit
agent-VM default. Inspect acceleration support without changing the hypervisor
or granting device access. See [Android acceleration requirements](https://developer.android.com/studio/run/emulator-acceleration).

iOS simulation needs macOS and Xcode. A Linux VM can coordinate a Mac but
cannot host an iOS simulator. The operator supplies Xcode, accepts its license,
and installs the chosen runtime. A Mac adapter would observe those prerequisites
and arrange project-specific jobs; it would not imply general macOS setup
support. See [Xcode](https://developer.apple.com/xcode/).

## Ownership and authority

Basaltwater would own explicit prerequisite selections, capability diagnostics,
and optional project recipes. T3 remains the sole owner of its device hub and
`agent-device` installation, version reconciliation, and agent-access setting.
Do not add a competing npm updater, background hub, or automatic device boot.

The environment server's account resolves SSH aliases, keys, and host files.
Require independently verified host keys and key-based non-interactive access;
do not use password prompts, disable host checking, or copy a private key into
a repository. Configure only the environments authorized to use that host.
T3 can install tools on first discovery, so a future Basaltwater doctor must
not call `device_list` merely to inventory capabilities.

Starting or powering off a device and changing location, accessibility, network,
or app permissions are explicit test actions. Keep the selected host/device
identity stable across commands and restore settings changed by a test.
Installing an app, wiping simulator data, removing an AVD, or replacing an SDK
requires scope that explicitly covers that operation. Preserve personal devices,
SDK installations, and simulator data on reruns and capability removal.

## Delivery slices, all pending

1. **Observation contract:** add a read-only, versioned capability result for
   platform, host reachability, tool versions, acceleration availability, capacity,
   and unmet prerequisites. Missing optional hosts remain an inventory gap.
   Unreachable hosts fail requested mobile checks without failing unrelated work.
   Never include SSH secrets, signing material, app data, or full device listings
   in support bundles. Reuse existing machine-capability and input validators.
2. **Android, deferred:** implement only after a concrete Android project and
   eligible device host are selected. Define SDK/system-image versions,
   architecture, download verification, license handling, bounded staging,
   rollback, retention, and an explicit AVD ownership record before adding setup
   options. Integrate through the owning package and plugin capability builder.
   Start with one disposable AVD; never adopt or delete a personal AVD silently.
3. **iOS via SSH, unscheduled:** qualify an operator-provided Mac using T3's
   connection test before enabling device discovery. Verify developer tools and
   simulator availability without building a runner or booting a device during
   doctor. Plan a clean, explicit simulator build/install workflow for one
   example app, with architecture and Xcode/runtime compatibility recorded.
   Signing, physical devices, and App Store distribution remain separate scope.
4. **Project connectivity and guidance:** keep build/install/launch recipes in
   the application repository. A simulator on a remote Mac cannot reach Metro
   on the Linux environment's localhost; qualify a narrow reachable route or
   explicit forwarding in both directions. Use HTTPS or localhost for the T3
   viewer's secure-context requirement; preserve access sources and TLS trust.
   Distinguish viewer streaming, device-to-app traffic, and SSH reachability.
   Add a capability-selected mobile skill only after these workflows work.

Android and iOS host work can proceed independently after the observation
contract. No implementation timeline is assigned by this proposal.

## Acceptance and rollout

Use mocked commands and temporary directories for unit coverage, then run a
deliberate qualification on a disposable Android host/AVD and a separate Mac
with a disposable simulator. Record exact T3, SDK/Xcode, runtime, architecture,
and host versions, with bounded resource use and logs.

Qualification must prove no implicit downloads or launches during observation,
safe reruns, recovery after interrupted installation, offline/version-mismatch
behavior, host disconnect/reconnect, explicit host selection, build/install,
semantic input, visible state, evidence delivery, and cleanup of only test-owned
resources. Verify closing the viewer leaves the device running and deliberate
power-off actually stops it. Existing agents and T3 sessions must continue when
an optional mobile prerequisite fails.

Before release, update capability manifests, CLI/reference documentation,
managed skills, and this plan with delivered versus still-proposed behavior.
Link recordings only after confirming where the attached tool delivered them.
Responsive web tests remain available now; report them as browser coverage.

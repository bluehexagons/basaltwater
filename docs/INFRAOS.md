# InfraOS: initial project concept

**Status:** discussion draft
**Working name:** InfraOS
**Last reviewed:** September 2026

## Purpose

InfraOS is a proposed Debian-based operating-system product built on the
principles and feature set of basaltwater. Its purpose is to make a machine's
role, configuration, security posture, operations, and recovery path explicit
and repeatable from first boot through its supported life.

It is not initially a new Linux distribution with independently maintained
packages. It is a curated Debian derivative: signed images and packages plus an
basaltwater-powered configuration and operations layer. Debian remains the
supplier of the kernel, base packages, security fixes, and package ecosystem.

This distinction is deliberate. The valuable part of Basaltwater is
machine-aware composition, safe operations, and an understandable lifecycle—not
the cost of maintaining a kernel, package archive, installer, and hardware
certification program from scratch.

## Product thesis

A productive Linux machine should be able to answer five questions clearly:

1. What role is this machine intended to perform?
2. What configuration and security policy produced its current state?
3. Which parts may safely run on this hardware, VM, or container?
4. How can an operator inspect, change, update, or repair it?
5. How can the same result be reproduced on a replacement machine?

InfraOS would answer these with a versioned desired-state manifest, profiles
composed from Basaltwater plugins, capability-aware planning, redacted local
state, and ordinary Debian administration where that remains the clearest
interface.

```text
                     signed InfraOS artifacts
                               |
       +-----------------------+-----------------------+
       |                       |                       |
  installer ISO           cloud/Proxmox image      existing Debian
       |                       |                       |
       +-----------------------+-----------------------+
                               |
                   enrollment and capability scan
                               |
                 profile + desired-state manifest
                               |
               Basaltwater planner and reconciler
                               |
  Debian packages, systemd services, data, diagnostics, and operations
```

## Principles inherited from Basaltwater

### Machine-aware configuration

The same requested feature must not assume that a bare-metal workstation, VM,
unprivileged LXC container, or cloud guest has identical capabilities. Plans
must state which steps will apply, which will be skipped, and why. Kernel,
firmware, firewall, desktop, and hardware steps must use specific capability
checks rather than a broad “safe to modify” flag.

### Secure, conservative defaults

The default profile should enable a defensible baseline: SSH hardening,
appropriate firewall policy, automatic security updates, service boundaries,
journal retention limits, failure-ban controls, and health visibility. More
invasive actions—firmware changes, remote exposure, non-security language
upgrades, release transitions, and destructive cleanup—remain deliberate,
reviewable choices.

### Desired state with targeted operations

Each system keeps a redacted record of its selected profile and declarative
configuration. A change should reconcile the smallest relevant subsystem when
possible: a share change should not rerun desktop setup. Every operation must
provide a readable plan or dry run before it changes a machine.

### State safety and conventional Linux

Credentials never enter the manifest or command history. Persistent service
data stays outside replaceable release artifacts. InfraOS v1 remains compatible
with normal Debian administration: `apt`, `systemctl`, SSH, normal
filesystems, and local troubleshooting. It reports unmanaged drift rather than
silently destroying it.

## Initial users and profiles

The first release should serve technically capable individuals and small teams
who want a dependable personal or small-fleet operating environment. It should
not claim to be a general desktop replacement, mobile OS, or enterprise endpoint
management system.

| Profile | Intended use | Initial capabilities |
| --- | --- | --- |
| `server_minimal` | SSH-managed small server | baseline security, updates, diagnostics, storage, and backup options |
| `server_web` | web/deployment node | server baseline plus Nginx/TLS, deployments, tunnels, and service options |
| `server_dev` | headless development VM | server tooling, language/runtime choices, agent and browser-test options |
| `workstation_dev` | human-operated developer desktop | desktop, editor, browser, RDP, and developer tools |
| `agent_workstation` | graphical coding/automation workstation | workstation profile plus bounded agent, browser, and shared-desktop tooling |
| `control_plane` | administers other systems | SSH/rsync, inventory, saved host configuration, VM, and Proxmox operations |

Profiles select a reviewed collection of feature modules. They are not
permanently different operating systems; the manifest records intent and
supports subsequent additions or removals.

## Architecture proposal

### Artifact layer

InfraOS would publish a small, signed set of artifacts:

- UEFI-capable installer ISO for bare metal and local virtualization;
- cloud images for generic QEMU/KVM and supported providers;
- Proxmox VM templates;
- an installable `infraos-base` Debian package and repository for enrollment
  of an existing supported Debian machine;
- checksums, signatures, SBOMs, release notes, and a support-lifetime policy.

The initial image should be thin: Debian base, boot and network support, the
enrollment/recovery components, Basaltwater, repository trust material, and the
security baseline. Desktops, browsers, coding agents, and services are profile
features, not universal image contents.

Debian’s `live-build` is a suitable early ISO mechanism. A dedicated image
repository becomes appropriate only when artifact retention, signing controls,
and a different release cadence justify it.

### Enrollment and first boot

The first-boot service must be idempotent and resumable. It should:

1. identify architecture, boot mode, network availability, storage layout, and
   machine capabilities;
2. establish the local administrative user and SSH access without placing
   secrets in the manifest;
3. accept a local manifest, removable-media manifest, cloud-supplied source,
   or minimal interactive selection;
4. validate the requested role and feature combination;
5. display and record the plan, then apply it;
6. emit a local setup report and leave a recovery/retry entry point.

First boot must work without a central service. An optional control plane may
distribute approved manifests, collect explicitly permitted health data, and
coordinate fleet updates, but it must not be required to operate or recover one
machine.

### Manifest and reconciliation

The canonical configuration should be data, not a reconstructed command line.
It needs a versioned schema, validation, feature ownership rules, migrations,
and a redaction model. YAML is appropriate for human-authored manifests if it
is parsed into a strict typed model; JSON is equally valid for APIs.

Illustrative—not final—manifest:

```yaml
apiVersion: infraos/v1alpha1
machine:
  profile: agent_workstation
  update_channel: stable
features:
  desktop:
    environment: xfce
    rdp: true
  agents:
    coding: true
    browser_testing: true
security:
  ssh:
    public_key_only: true
  automatic_security_updates: true
```

The reconciler compiles this to the current plugin and step model. A plan should
name the feature responsible, package/service/file impacts, capability
constraints, prerequisites, and rollback or recovery advice.

Secrets are references only: a root-owned credential file, interactive prompt,
TPM-backed secret, or external secret provider. They must never appear in the
manifest, state snapshot, process arguments, logs, or support bundle.

### Runtime and operations layer

The local `infraos` command can initially be an intentional façade over
Basaltwater rather than a separate implementation:

```text
infraos plan manifest.yaml
infraos apply manifest.yaml
infraos status
infraos diagnose
infraos update check
infraos recover
```

The underlying system continues to use ordinary Debian mechanisms. Basaltwater
owns setup composition and targeted operations; systemd owns services; APT owns
package transactions; application-specific tools own their data. This avoids a
parallel package manager or opaque configuration daemon.

### Update and recovery model

There are three distinct update domains:

| Domain | v1 mechanism | Required safety behavior |
| --- | --- | --- |
| Debian base and security packages | APT, stable Debian release | automatic security updates; transaction status and reboot visibility |
| InfraOS packages and profile definitions | signed InfraOS APT repository | tested channels, pinned compatibility metadata, staged rollout, and release notes |
| application/runtime deployments | current Basaltwater deployment mechanisms | state outside releases, verified backup where required, application-specific rollback |

An OS-release upgrade is separately planned, never an incidental consequence of
profile reconciliation. The initial recovery model is bootable rescue media,
manifest export/import, package repair guidance, backups of managed
configuration, and reapplication of a known manifest. Full atomic system
rollback is a future capability, not a v1 promise.

## Delivery modes

### Mode A: conventional Debian derivative (recommended v1)

Use a writable Debian root filesystem, APT, systemd, and Basaltwater
reconciliation. Publish images and packages, but do not fork Debian packages
unless a specific InfraOS component requires one.

This maximizes compatibility with the existing project’s SSH setup, Debian
package installation, desktop support, Proxmox workflow, services, and
operator-focused diagnostics. It also supports normal package installation and
hands-on debugging.

### Mode B: image-based/immutable edition (research after v1)

A future edition could use an image-based root, atomic deployment switching,
and rollback. It suits controlled developer desktops, appliances, ephemeral CI
or agent workers, and fleet-managed servers.

It should not begin until the project has a firm answer for package layering,
persistent homes/service data/credentials/logs, proprietary drivers, image
signing and key rotation, update channels and canaries, break-glass debugging,
and rollback-retention cost. It must be a separate product mode with explicit
constraints, not a surprise behavior change for the conventional edition.

## Related projects and lessons

| Project | Similarity | Useful lesson | Why it is not the v1 base |
| --- | --- | --- | --- |
| [NixOS](https://nixos.org/manual/nixos/stable/) | declarative system configuration, module composition, atomic generations | make desired state first-class, composable, and testable | it replaces the Debian/APT and Basaltwater execution model |
| [Fedora Atomic Desktops](https://www.fedoraproject.org/atomic-desktops/) | image-mode developer desktop | clearly separate OS image change from application tooling | Fedora/RPM-OSTree is a different operating-system foundation |
| [Universal Blue](https://universal-blue.org/) | customized desktop images derived from a trusted base | deliver a product as reproducible, composable images | built on Fedora Atomic rather than the project’s Debian target |
| [Flatcar Container Linux](https://www.flatcar.org/docs/latest/installing/) | declarative first boot, staged updates, channels | treat provisioning and update/reboot policy as deliberate product boundaries | optimized for minimal container hosts rather than hands-on desktops and servers |
| [Talos Linux](https://www.talos.dev/) | API-managed immutable operating system | narrow the trusted surface and make lifecycle APIs explicit | intentionally Kubernetes-specific and rejects normal SSH administration |
| [Ubuntu Core](https://documentation.ubuntu.com/core/) | immutable, transactional, signed appliance lifecycle | explicitly own image composition, update, rollback, and recovery | Snap confinement and appliance focus do not fit arbitrary Debian packages and developer tooling immediately |

The relevant conclusion is to borrow ideas rather than copy an entire stack:
NixOS’s declarative contract, Flatcar’s provisioning/update discipline,
Universal Blue’s image-product approach, and Ubuntu Core’s lifecycle ownership.
InfraOS’s differentiator is support for both hands-on systems and repeatable
remote operations across server, workstation, and virtualization roles.

## Scope boundaries

### In scope for the first product

- Debian stable as the only supported base;
- amd64 initially; arm64 only after a tested image and support plan exist;
- ISO, generic cloud/QEMU image, and Proxmox template;
- local-first enrollment and versioned manifests;
- profile compilation through Basaltwater plugins;
- ordinary APT-based updates plus an InfraOS package repository;
- signed releases, checksums, SBOM/provenance work, and documented recovery;
- testable VM, bare-metal, and supported unprivileged-container paths.

### Explicitly out of scope for v1

- a new package manager, package archive mirror, or rebuilt Debian universe;
- a downstream kernel or desktop environment;
- mandatory SaaS controller, telemetry, or online account;
- mobile devices, arbitrary ARM boards, and hardware-certification claims;
- immutable root, A/B partition updates, and automatic release upgrades;
- ownership of application backups without a declared backup/restore contract.

## Delivery plan and decision gates

### Phase 0 — validate the boundary

Deliver a supported-platform and security-support policy, a minimal manifest
schema, a threat model for artifacts/enrollment/secrets/updates/recovery media,
and a decision that v1 is conventional Debian. The exit statement should be:

> A signed Debian image or enrollment package that turns a machine into a
> securely operated Basaltwater profile from a versioned manifest.

### Phase 1 — reproducible image proof of concept

Deliver a QEMU/Proxmox image build from a pinned Debian release, an
`infraos-base` package and first-boot service, one `server_dev` manifest,
CI boot verification, a recovery/retry test, and initial artifact signing/SBOM
work. The exit test: an independent CI run can build a functionally equivalent
artifact and boot it without an interactive administrator.

### Phase 2 — operator-grade v1

Deliver the initial supported profile set, ISO/cloud/template builds,
interactive/file/cloud enrollment, manifest migrations,
`plan/apply/status/diagnose/recover`, update-channel policy, and user-facing
install/update/recovery documentation. The exit test: a user can replace a
failed VM, apply the redacted manifest, restore documented data, and regain the
supported role predictably.

### Phase 3 — optional fleet operations

Add controller enrollment, approval-based remote plan/apply, audit records,
health summaries, update rings, backup verification, and an explicit
no-telemetry default. The controller must improve—not replace—the local-first
workflow.

### Phase 4 — decide whether immutability solves a real problem

Only after deployments show a need, write an architecture decision record
comparing A/B Debian images, bootable containers, and adoption of an immutable
upstream. Do not proceed on aesthetics alone.

## Workstreams and risks

| Workstream | First responsibility |
| --- | --- |
| Profile compiler | formalize system types/features, validation, ownership, dependencies, and conflicts from the plugin registry |
| Manifest/state | schema, migrations, redaction, permissions, export/import, and drift classification |
| Images | reproducible definitions, ISO/cloud/template formats, boot tests, and release metadata |
| Enrollment | local/cloud input, idempotent first boot, interruption/retry behavior, user/SSH setup |
| Security/supply chain | keys, publication, artifact signing, SBOMs, provenance, vulnerability-response policy |
| Update/recovery | channels, compatibility, rescue media, and supported repair procedures |
| Test lab | QEMU, Proxmox, VM/container capability matrix, upgrade/recovery and negative-path tests |

| Risk | Mitigation |
| --- | --- |
| “Distro” scope consumes project capacity | keep v1 as images plus packages and rely on Debian’s package/security work |
| reconciliation surprises local administrators | ownership model, plan-first UX, unmanaged-drift reporting, opt-in takeover, backups |
| secrets leak through state or logs | secret references only, restrictive permissions, redaction tests, minimal support bundles |
| images work only in CI | test a stated VM, bare-metal, and container matrix; publish limitations |
| profiles become untestable | use a small set of modules; test representative combinations and reject unsupported ones |
| controller becomes a recovery dependency | preserve complete local manifest, apply, and recovery workflows |

## Measures of success

- A clean image reaches an intended profile with no undocumented manual steps.
- Dry-run plans accurately predict supported changes.
- Recovery time for a supported VM is bounded and documented.
- Published artifacts are traceable to source, signed, and retained for the
  stated recovery policy.
- Security updates reach supported systems according to a published policy.
- Operators can understand and repair a managed machine with standard Debian
  tools plus InfraOS diagnostics.
- No secret appears in manifests, logs, support bundles, or test artifacts.

## Questions to resolve before implementation

1. Is the primary audience personal labs/small teams, a managed hosted product,
   or a self-hosted platform?
2. Which two profiles are worthy Phase 1 targets? Current candidates are
   `server_dev` and `agent_workstation`.
3. Is bare-metal installation required at v1, or can QEMU/Proxmox and cloud
   images establish product value first?
4. What support window applies to published images and InfraOS packages?
5. What signing/provenance system is practical, and who owns key rotation?
6. What information, if any, may an optional controller collect?
7. Which current Basaltwater configuration fields can become stable manifest
   fields, and which need a new abstraction?
8. Is existing Debian enrollment non-destructive only, carefully convertible,
   or intentionally unsupported?

## Immediate next proposal

Create an architecture decision record that commits only to this experiment:

> Build a reproducible Debian QEMU/Proxmox image containing Basaltwater and an
> idempotent local enrollment service. It accepts one versioned `server_dev`
> manifest, produces a dry-run plan, applies the supported profile, and exports
> a redacted manifest for replacement-machine recovery.

This tests the central thesis—image plus desired-state operations—at low
irreversible cost. It does not commit the project to a new distribution,
immutable root, controller service, or public release.

## References

- [Basaltwater architecture and capabilities](../README.md)
- [machine-type model](MACHINE_TYPES.md)
- [deployment safety model](DEPLOYMENT_SAFETY.md)
- [Debian Live Manual: installation and live-build](https://live-team.pages.debian.net/live-manual/html/live-manual/installation.en.html)
- [NixOS manual](https://nixos.org/manual/nixos/stable/)
- [Fedora Atomic Desktops](https://www.fedoraproject.org/atomic-desktops/)
- [Universal Blue](https://universal-blue.org/)
- [Flatcar provisioning and updates](https://www.flatcar.org/docs/latest/installing/)
- [Talos Linux](https://www.talos.dev/)
- [Ubuntu Core documentation](https://documentation.ubuntu.com/core/)

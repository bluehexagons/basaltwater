# Basaltide: Debian developer and agent workstation

Basaltide is a planned Debian-based distribution for developers working
alongside coding agents. It bundles Basaltwater tooling and selected desktop
and development customizations. The name, Debian foundation, workstation focus
and commitment to remain completely free and open source are decided.

Status: planning; no Basaltide image, installer or package repository exists.
Desktop, application selection, packaging and delivery format remain open.
Basaltwater remains independently usable on ordinary Debian. Basaltide has its
own delivery criteria and is outside the current
[Basaltwater release gate](../release-qualification.md).

## Product constraints

- **Free and open source:** Basaltide is free of charge. Publish its source,
  image recipes, package definitions and freely licensed artwork. All bundled
  software, dependencies, drivers, firmware and assets must meet the free and
  open-source commitment; audit licenses and source availability before
  inclusion. Existing Basaltwater application choices need this review too.
- **Conventional Debian:** begin with a writable root filesystem, APT and
  systemd. Use Debian packages and security updates for the base, minimize
  downstream patches and maintain the Basaltide integration and defaults.
  Follow Debian's [derivative guidance](https://www.debian.org/derivatives/).
- **Local use:** the desktop and bundled tools must work without a controller,
  subscription or provider login. Agent authentication and optional downloads
  happen on the installed machine. Remote access is an explicit addition.
- **Native project workflows:** projects retain their own commands, toolchain
  pins and data, following the [project standards](../agents/project-standards.md).

## First workstation experience

1. Boot a local graphical desktop and establish the owner's account, keyboard,
   locale and network.
2. Find a terminal, browser, editor, Git and `basaltw` already available, with
   concise development, maintenance and recovery help.
3. Select freely licensed agent clients and development bundles. Authenticate
   selected integrations per machine; identify downloads that need connectivity.
4. Clone a project, run its native build/test commands and use the selected
   agent alongside normal desktop applications.
5. Inspect installed versions, update the system and recover failed setup
   through documented local tools.

Local graphical login must remain available on a physical workstation. A
remote-only VM mode needs a separate selection and qualification.

## Customization choices

Start with one desktop and a small default application set. XFCE is a candidate
because the [Debian workstation profiles](../workstations/workstations.md)
already use it; KDE and GNOME remain alternatives to evaluate.

| Area | Proposed starting point | Open choice |
| --- | --- | --- |
| Desktop and identity | Basaltide theme, wallpaper, readable fonts, useful launchers and local help using upstream desktop components | Desktop/session, accessibility and visual identity |
| Basaltwater | `basaltw`, shared agent skills, diagnostics and workstation workflows | Package layout, tested revision and update ownership |
| Development | Terminal, Git, browser and editor; selectable Node, Python/uv, Go and project prerequisites | Default applications and runtime sources |
| Coding agents | Freely licensed clients and optional T3 Code integration | Which clients ship and which install on request; license review of each bundle |
| Testing and desktop work | Optional browser automation, application inspection and capture tools | Supported browser/session combinations and permissions |
| Creative and game work | Optional Godot, graphics, audio/video and development bundles | First supported bundle and size budget |
| Containers and remote access | Optional rootless containers, SSH, RDP and browser desktop access | Network exposure, account boundaries and local/remote session behavior |
| Maintenance | Package updates, reboot notices, diagnostics and recovery guidance | Workstation update timing and retention defaults |

## Basaltwater integration gaps

Reuse Basaltwater's validators, capability helpers, plugin composition and
diagnostics. Keep image recipes, branding and package composition separate from
per-machine setup. The existing [workstation](../workstations/workstations.md),
[agent VM](../agents/agentic-vms.md) and
[authentication](../agents/agent-authentication.md) guides describe reusable
capabilities; Basaltide integration still needs these changes and checks:

- **Distribution identity:** the intended `ID=basaltide` with `ID_LIKE=debian`
  needs explicit support. The installer and Python preflight recognize specific
  IDs, while Debian APT-source repair requires `ID=debian`. Follow the
  [`os-release` specification](https://www.freedesktop.org/software/systemd/man/latest/os-release.html)
  and separately retain and validate the underlying Debian suite. A Basaltide
  release name must not become a Debian mirror suite; test accepted and rejected
  combinations instead of treating `ID_LIKE` as a compatibility guarantee.
- **Local desktop:** current Debian RDP setup converts graphical access to a
  shared XRDP desktop and disables console graphical login. Qualify a local
  session path before remote access; do not apply `agent_code_vm` defaults
  wholesale to a physical workstation.
- **Image construction:** separate build-time package/file composition from
  first-boot services, accounts and runtime probes. Full host setup inside an
  image-build chroot is not an established image builder.
- **Configuration and privileges:** use existing saved setup and dry runs for
  the proof. A general manifest/drift planner is separate work. Provide normal
  user-owned development and an explicit administrator path; evaluate
  [privilege approvals](../agents/privilege-approvals.md) for agent maintenance.
  Unrestricted passwordless root is not a workstation default.
- **Update ownership:** choose one owner per component. APT-packaged Basaltwater
  must not also be overwritten by its source installer or refresh flow. Pin a
  tested revision in builds and define compatible updates for installed systems.

## Image, packages and first boot

The proposed first target is one amd64 QEMU/KVM workstation VM with local
console graphics. Add a Proxmox import/boot check where available. Hardware,
cloud and additional architecture support follow separate qualification.

Evaluate Debian `live-build` for a live ISO and installation path; its
[customization mechanisms](https://live-team.pages.debian.net/live-manual/html/live-manual/customizing-contents.en.html)
include files, build hooks and boot-time hooks. Choose one initial output
format and build tool before implementation. A successful live session does
not establish persistent installed-system behavior.

Package the reusable identity/defaults, workstation application selection and
optional bundles. Exact package names and Basaltwater packaging remain open.
Define takeover and removal separately before offering installation over an
existing Debian desktop.

Version image recipes and package sources, and decide whether they belong here
or in a separate Basaltide repository. Record the Debian suite, package sources
and versions, Basaltwater revision and build-tool inputs. First establish
repeatable contents and equivalent boot behavior; demonstrate byte-identical
reproducibility separately.

First boot creates instance-specific machine identity, SSH host keys when
selected, and enabled local trust/service credentials. Templates contain no
reusable passwords, provider sessions, private keys, paired clients, personal
repositories or completed setup/job state. Initialize only selected features
and retain interrupted setup evidence for explicit repair. Offline boot must
reach the bundled desktop and help; optional downloads can wait for connectivity.

## Updates and recovery

| Layer | Responsibility to define |
| --- | --- |
| Debian base | APT against the pinned supported Debian release and its security sources; update/reboot timing suitable for interactive work |
| Basaltide packages and defaults | Versioned compatibility policy, signed repository metadata and trust-key rotation before public distribution |
| Basaltwater and agent tools | One package or upstream update owner per tool, visible versions and compatibility checks |
| Projects and user data | User/project-owned repositories, environments and backups outside replaceable system artifacts |

Name the maintenance owner and support window for Basaltide's additions;
Debian's security support does not cover every added tool or customization.
OS release upgrades are a separate workflow, never a silent Debian-suite change
during profile reconciliation.

Initial recovery uses ordinary Debian repair, retained setup diagnostics,
rescue media, and reinstall plus restoration of documented user data and
configuration. Test interrupted setup and updates. APT and saved profiles do
not provide full-system atomic rollback or restore arbitrary application data.

## Delivery and acceptance

### 1. Define one workstation

Choose the desktop/session, supported Debian suite, freely licensed app/agent
list, image format and package/update ownership. Select licenses for Basaltide's
own code and assets. Set image-size, disk, RAM and startup targets, and resolve
distribution identity and local graphical login.

Acceptance: an implementation decision and build recipe describe the complete
first workstation and its component/license inventory, using existing
Basaltwater interfaces without requiring a new controller or manifest service.

### 2. Build and use it

Build from recorded inputs, boot, create the owner account and exercise the
terminal, browser, editor, `basaltw` and one selected agent. Authenticate only
after boot. Run a small project's native build/test workflow and a browser or
desktop check, then reboot and repeat local login.

Acceptance: an independent build produces equivalent usable contents; offline
desktop/help work; two instances have distinct identities and no build-account
credentials. Setup, APT-source handling and refresh obey the chosen ownership
policy on the branded image.

### 3. Prove maintenance and recovery

Exercise a real package update, interrupted first-boot/setup, failed download
and configuration rerun. Verify user files and preferences survive, unfinished
state remains diagnosable and a repaired system reaches the same desktop.
Demonstrate reinstall and restoration of sample user data.

Acceptance: usable local install/update/recovery instructions and a concise
tested-scope summary. Debian CI and setup tests complement actual image boot,
graphical-session and recovery checks.

### 4. Qualify a distributable preview

Choose the first public live/installer ISO or installable VM image. Define
artifact verification, signing-key handling, freely available source and build
instructions, license notices, package inventory/provenance, download retention
and support lifetime. Qualify the actual installation flow as well as live use.

Acceptance: a new user can verify, install, use, update and recover the artifact
on the stated target. Bare-metal claims require representative free driver and
firmware support, graphics, network, audio, suspend/resume and storage/encryption
checks; VM success alone cannot establish them.

## Remaining decisions and later scope

- Desktop, editor and whether T3 Code is a default entry point or selectable.
- Which freely licensed clients and development bundles ship, and which need
  first-boot downloads or optional provider accounts.
- Shell/editor defaults, shortcuts, accessibility, creative tools, privacy and
  power management.
- VM versus ISO delivery, and when bare-metal installation and disk encryption
  become required.
- Repository ownership, release maintenance, compatibility tests and security
  response capacity.

Server/appliance editions, cloud images, ARM, container templates, fleet
management, a new declarative API and immutable/A/B updates remain outside the
first workstation proof. The next implementation task is the desktop, image and
packaging decision followed by one bootable recipe with these acceptance checks.

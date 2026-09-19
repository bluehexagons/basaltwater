# Basaltwater documentation

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/wordmark-dark.svg">
  <img src="brand/wordmark-light.svg" alt="Basaltwater" width="264" height="48">
</picture>

See [upgrade and rollback](BASALTWATER_MIGRATION.md) for the rename, or
the [identity guide](BRANDING.md) for reusable visual assets.

Start with [Try Basaltwater on a Debian VM](GETTING_STARTED.md) for a guided
first setup and small feature experiments. Use the
[quick reference](QUICK_REFERENCE.md) once you know the basic workflow.
Use the detailed guides only when you need the configuration model, limits, or
troubleshooting for that task. The [root README](../README.md) is the project
overview.

## Choose a reading path

| I need to… | Start here | Then use |
| --- | --- | --- |
| Try Basaltwater for the first time | [Beginner walkthrough](GETTING_STARTED.md) | [Installation](INSTALLATION.md), [Quick reference](QUICK_REFERENCE.md) |
| Install Basaltwater or configure a first host | [Installation](INSTALLATION.md) | [Quick reference](QUICK_REFERENCE.md), [CLI reference](COMMAND_LINE.md) |
| Change or inspect an existing host | [Saved configuration operations](OPERATIONS.md) | [Sysadmin shortcuts](SYSADMIN.md), [Maintenance](MAINTENANCE.md) |
| Provision or maintain a coding VM | [Agentic VMs](AGENTIC_VMS.md) | [Agent systems](agents/README.md), [Credentials](CREDENTIALS.md) |
| Add coding tools to an existing CachyOS KDE workstation | [CachyOS local setup](CACHYOS.md) | [Managed skills](AGENT_SKILLS.md) |
| Deploy an application or publish an internal site | [Deployments](DEPLOYMENTS.md) | [Internal web](INTERNAL_WEB.md), [CI/CD](CICD.md) |
| Operate a Proxmox host or guest | [Proxmox workflows](PROXMOX.md) | [Machine types](MACHINE_TYPES.md) |
| Configure alerts, audit visibility, or the panel | [Notifications](NOTIFICATIONS.md) | [Minimal web panel](WEB_PANEL.md), [Authentication hardening](AUTHENTICATION_HARDENING.md) |
| Configure storage, shares, or backups | [Storage operations](STORAGE_OPERATIONS.md) | [Samba](SAMBA_SHARES.md), [Syncthing](SYNCTHING.md), [Backups](BACKUPS.md) |
| Research a Debian-based Basaltwater operating environment | [InfraOS concept](INFRAOS.md) | [Machine types](MACHINE_TYPES.md), [Deployment safety](DEPLOYMENT_SAFETY.md) |

## Core setup and operations

| Guide | Use it for |
| --- | --- |
| [Quick reference](QUICK_REFERENCE.md) | Common command shapes and the shortest route to the right detailed guide |
| [Installation and bootstrap](INSTALLATION.md) | Launcher installation, control-plane setup, upgrades, and recovery |
| [Command-line reference](COMMAND_LINE.md) | Complete option and command reference |
| [Saved configuration operations](OPERATIONS.md) | `list`, `info`, `cmd`, `deploy`, `recall`, the shell, and testing |
| [Machine types](MACHINE_TYPES.md) | Debian bare metal, VM, LXC, OCI, and capability differences |
| [Local system maintenance](LOCAL_MAINTENANCE.md) | Focused package, desktop, browser, hostname, IP, and DNS changes |
| [Recurring maintenance](MAINTENANCE.md) | Timers, update policy, cleanup, and troubleshooting |
| [Firmware auditing and updates](FIRMWARE.md) | Local fwupd inventory, dependency installation, and guarded updates |
| [Sysadmin shortcuts](SYSADMIN.md) | SSH, transfers, health, services, logs, upgrades, and reachability |
| [Shell completion](SHELL_COMPLETION.md) | Bash, Zsh, Fish, and system-wide completion |
| [Network inventory](NETWORKING.md) | Network profiles and read-only Proxmox firewall planning |

## Security, access, and notifications

| Guide | Use it for |
| --- | --- |
| [SSH authentication](SSH.md) | Passphrase-protected keys, terminal prompts, SSH agents, and troubleshooting |
| [Credentials overview](CREDENTIALS.md) | Choose the correct credential store or workflow |
| [Agent authentication](AGENT_AUTHENTICATION.md) | Agent auth sources, rotation, recovery, portability, and lifecycle |
| [Git access and authentication](GIT_ACCESS.md) | Git policy, GitHub, self-hosted HTTPS, private CAs, and Git LFS |
| [Authentication hardening](AUTHENTICATION_HARDENING.md) | Reachability, rate limits, failure bans, and verification for login surfaces |
| [Notifications](NOTIFICATIONS.md) | Webhook, mailbox, and web-panel delivery; event volume; and alert interpretation |
| [Minimal web panel](WEB_PANEL.md) | Install, use, and troubleshoot the authenticated machine dashboard |
| [Web panel reference](WEB_PANEL_REFERENCE.md) | Notification API, data limits, diagnostics, and access boundaries |
| [Client CA trust](CLIENT_CA_TRUST.md) | Private-CA diagnosis and client enrollment |
| [Protected device pairing](DEVICE_PAIRING.md) | Basic-Auth enrollment portal, one-time provider links, rotation, and removal |

## Agent systems

The [agent-systems guide](agents/README.md) is the focused starting point for
coding VMs and workstations. It routes to the relevant operational guide
without requiring readers to infer relationships among credentials, browser
automation, T3 Code, skills, and hardening.

| Guide | Use it for |
| --- | --- |
| [Workstations and desktop applications](WORKSTATIONS.md) | Desktop profiles, human-operated browsers, Flatpak, office tools, and verification |
| [Shared desktop automation](DESKTOP_AUTOMATION.md) | Native application launch, accessibility and pixel input, screenshots, and human handoff |
| [Agentic coding security](AGENT_SECURITY.md) | Sudo, Codex approval/sandbox policy, hardened modes, and supply-chain boundaries |
| [Agentic VMs](AGENTIC_VMS.md) | Headless, full-capability, approval-panel, unrestricted, and hardened VM configurations |
| [Privilege approvals](PRIVILEGE_APPROVALS.md) | Separate HTTPS approval page, supported operations, and administrator allowlists |
| [Privilege broker reference](PRIVILEGE_BROKER_REFERENCE.md) | Allowlist policy, audit records, and approval-service security boundaries |
| [Agent browser automation](BROWSER_AUTOMATION.md) | Playwright provisioning, Codex/OpenCode registration, and browser security boundaries |
| [T3 Code server](T3_CODE.md) | Headless service, deliberate updates, pairing, remote clients, and security boundaries |
| [Managed agent workflow skills](AGENT_SKILLS.md) | Installed Codex/OpenCode skills, capability routing, reconciliation, and maintenance |
| [Godot Engine](GODOT.md) | Verified graphical/headless installation, web/publishing bundles, agent access, and updates |

## Services, deployments, and data

| Guide | Use it for |
| --- | --- |
| [Deployments and manifests](DEPLOYMENTS.md) | `--deploy`, `basaltwater.json`, static sites, services, and runtime behavior |
| [Deployment safety](DEPLOYMENT_SAFETY.md) | Persistent state, backups, rollback, and deployment boundaries |
| [Internal HTTPS sites and previews](INTERNAL_WEB.md) | Static-site publishing, supervised live previews, managed forwards, TLS trust, and cleanup |
| [CI/CD webhook system](CICD.md) | Webhook jobs, build/app servers, and executor behavior |
| [Cloudflare tunnels](CLOUDFLARE.md) | Tunnel preconfiguration, ingress refresh, firewall policy, and webhooks |
| [Gogs Git service](GOGS.md) | Self-hosted Git, SSH access, storage, and release updates |
| [HomeBox inventory](HOMEBOX.md) | Native setup, private onboarding, recurring updates, health, and complete backup/restore |
| [Antistatic services](ANTISTATIC.md) | Lobby server, report administration, STUN, and antistatic-db |
| [XRDP](XRDP.md) | Desktop RDP architecture, compatibility, and troubleshooting |
| [Samba shares](SAMBA_SHARES.md) | Authenticated shares, credentials, access changes, removals, and SMB mounts |
| [Managed Syncthing](SYNCTHING.md) | Private hub-and-spoke file exchange, HTTPS GUI administration, relays, and recovery |
| [Storage operations](STORAGE_OPERATIONS.md) | Rsync mirrors, par2 protection, schedules, locks, and recovery |
| [Generic path backups](BACKUPS.md) | Provider-neutral backup mirrors, mounted destinations, parity, and consistency limits |
| [Proxmox workflows](PROXMOX.md) | Host registration, VM/LXC provisioning, lifecycle, resource pressure, boot ordering, backups, and smoke tests |

## Plans and contributor material

The [`plans/`](plans/) directory contains implementation plans and audit
records. It is not operator documentation; start with the
[planning index](plans/README.md) only when researching project work.

The [InfraOS concept](INFRAOS.md) is an initial product discussion for a
Debian-based operating environment built around basaltwater. It is not a
supported operating system or an implementation commitment.

Repository contributors should read the
[contributor and coding-agent guide](agents/contributing/README.md). It
describes repository change rules, not how to administer a
Basaltwater-managed machine.

## Documentation conventions

Guides should lead with the task, use short procedures and tables for lookup,
and link to a detailed reference rather than duplicate it. Examples use the
installed `basaltw` launcher and placeholders only; never put real secrets
in documentation.

# infra-tools quick reference

Use this page to find the right command family and detailed guide. It is not a
replacement for the [command-line reference](COMMAND_LINE.md): read that page
before combining advanced options or changing a production host.
For a first experiment without a domain or SSH setup, follow the
[beginner walkthrough](GETTING_STARTED.md).

## Common host lifecycle

| Goal | Command | Details |
| --- | --- | --- |
| Preview a new setup | `infra-tools setup server_dev server.example admin --node --dry-run` | [Installation](INSTALLATION.md#install-and-configure-a-remote-host) |
| Set up a new host | `infra-tools setup server_dev server.example admin --node` | [CLI reference](COMMAND_LINE.md#setup-at-a-glance) |
| List saved hosts | `infra-tools list` | [Saved configuration operations](OPERATIONS.md#inspect-saved-hosts) |
| Inspect a saved host | `infra-tools info server.example` | [Saved configuration operations](OPERATIONS.md#inspect-saved-hosts) |
| View its saved command | `infra-tools cmd server.example` | [Saved configuration operations](OPERATIONS.md#recall-and-reconstruction) |
| Preview adding Python tools | `infra-tools patch server.example admin --python --dry-run` | [Saved configuration operations](OPERATIONS.md#patch-and-redeploy) |
| Update shares only | `infra-tools shares fileserver` | [Samba shares](SAMBA_SHARES.md) |
| Rerun a saved setup | `infra-tools deploy server.example` | [Saved configuration operations](OPERATIONS.md#patch-and-redeploy) |

`server.example` is a placeholder: replace it with your Debian target's IP
address or hostname, and replace `admin` with the account to configure there.
Remote setup requires root SSH key access and [host-key enrollment](SSH.md).
The saved-host rows require a successful initial setup in the same workspace.
Remove `--dry-run` from the patch example to apply it.

## Find a task

| I need to… | Use |
| --- | --- |
| Install or upgrade the controller | [Installation and bootstrap](INSTALLATION.md) |
| Understand a flag or command not shown above | [Command-line reference](COMMAND_LINE.md) |
| Connect, copy files, inspect logs, or check a service | [Sysadmin shortcuts](SYSADMIN.md) |
| Configure SSH keys or passphrases | [SSH authentication](SSH.md) |
| Manage recurring jobs, updates, or cleanup | [Recurring maintenance](MAINTENANCE.md) |
| Provision and operate a Proxmox VM or LXC | [Proxmox workflows](PROXMOX.md) |
| Change a local controller or workstation | [Local system maintenance](LOCAL_MAINTENANCE.md) |
| Configure notification destinations or alert volume | [Notifications](NOTIFICATIONS.md) |
| Inspect audit activity or receive remote notification logs | [Minimal web panel](WEB_PANEL.md) |
| Publish an internal site or preview | [Internal HTTPS sites and previews](INTERNAL_WEB.md) |
| Deploy an application or manifest | [Deployments and manifests](DEPLOYMENTS.md) |
| Configure CI/CD | [CI/CD webhook system](CICD.md) |
| Configure shares, sync, parity, or backups | [Storage and data guides](README.md#services-deployments-and-data) |
| Provision or operate a coding VM | [Agent systems](agents/README.md) |
| Choose a headless, full-capability, approval-panel, or hardened agent VM | [Agentic VMs](AGENTIC_VMS.md) |

## Safe operating habits

- Inspect a saved host with `info` and `cmd` before a broad patch.
- Keep passwords and token-bearing webhook URLs out of shared output and
  tickets.
- Use feature-specific commands such as `shares` when they cover the intended
  change; they avoid unrelated setup work.
- Check [Machine types](MACHINE_TYPES.md) before expecting a kernel, firewall,
  desktop, or service capability in a container.

For the complete documentation map, return to the
[documentation index](README.md).

# Basaltwater quick reference

Use this page to find the right command family and detailed guide. It is not a
replacement for the [command-line reference](COMMAND_LINE.md): read that page
before combining advanced options or changing a production host.
For a first experiment without a domain or SSH setup, follow the
[beginner walkthrough](../getting-started/GETTING_STARTED.md).

## Common host lifecycle

| Goal | Command | Details |
| --- | --- | --- |
| Preview a new setup | `basaltw setup server_dev server.example admin --node --dry-run` | [Installation](../getting-started/INSTALLATION.md#install-and-configure-a-remote-host) |
| Set up a new host | `basaltw setup server_dev server.example admin --node` | [CLI reference](COMMAND_LINE.md#setup-at-a-glance) |
| List saved hosts | `basaltw list` | [Saved configuration operations](../operations/OPERATIONS.md#inspect-saved-hosts) |
| Inspect a saved host | `basaltw info server.example` | [Saved configuration operations](../operations/OPERATIONS.md#inspect-saved-hosts) |
| View its saved command | `basaltw cmd server.example` | [Saved configuration operations](../operations/OPERATIONS.md#recall-and-reconstruction) |
| Preview adding Python tools | `basaltw patch server.example admin --python --dry-run` | [Saved configuration operations](../operations/OPERATIONS.md#patch-and-redeploy) |
| Update shares only | `basaltw shares fileserver` | [Samba shares](../storage/SAMBA_SHARES.md) |
| Rerun a saved setup | `basaltw deploy server.example` | [Saved configuration operations](../operations/OPERATIONS.md#patch-and-redeploy) |
| Allow LAN web access alongside a tunnel | `basaltw patch server.example admin --lan-access` | [Cloudflare direct access](../services/CLOUDFLARE.md#direct-access-alongside-the-tunnel) |
| Allow unrestricted web access alongside a tunnel | `basaltw patch server.example admin --cloudflare-allow-direct` | [Cloudflare direct access](../services/CLOUDFLARE.md#direct-access-alongside-the-tunnel) |
| Add browser access to an existing HTTPS desktop panel | `basaltw patch server.example admin --guacamole` | [Guacamole browser desktop](../workstations/GUACAMOLE.md) |
| Remove browser desktop access | `basaltw patch server.example admin --no-guacamole` | [Guacamole removal](../workstations/GUACAMOLE.md#diagnose-and-remove) |

`server.example` is a placeholder: replace it with your Debian target's IP
address or hostname, and replace `admin` with the account to configure there.
Remote setup requires root SSH key access. Interactive setup handles
[first-use host-key confirmation](../operations/SSH.md#first-connection-during-setup) inline;
unattended runs require an enrolled key.
The saved-host rows require a successful initial setup in the same workspace.
Remove `--dry-run` from the patch example to apply it.

## Existing CachyOS KDE workstation

After [installing the launcher](../workstations/CACHYOS.md#install-with-t3-code-desktop), run
these from your local desktop terminal as yourself:

```bash
# Preview Codex and the native T3 Code desktop package
basaltw setup agent_cachyos localhost --t3code-desktop --dry-run

# Apply the selection; missing packages and cache pruning may request sudo
basaltw setup agent_cachyos localhost --t3code-desktop

# Inspect desktop prerequisites without launching applications
basaltw local cachyos-doctor
```

Codex and GitHub CLI are the defaults; no other coding agent is selected.
See [the CachyOS guide](../workstations/CACHYOS.md) for optional project tools, cleanup, and
switching between T3 desktop and web mode. This profile uses direct setup
reruns; it does not populate the generic saved-host lifecycle above.

## Find a task

| I need to… | Use |
| --- | --- |
| Install or upgrade the controller | [Installation and bootstrap](../getting-started/INSTALLATION.md) |
| Understand a flag or command not shown above | [Command-line reference](COMMAND_LINE.md) |
| Connect, copy files, inspect logs, or check a service | [Sysadmin shortcuts](../operations/SYSADMIN.md) |
| Configure SSH keys or passphrases | [SSH authentication](../operations/SSH.md) |
| Manage recurring jobs, updates, or cleanup | [Recurring maintenance](../operations/MAINTENANCE.md) |
| Provision and operate a Proxmox VM or LXC | [Proxmox workflows](../operations/PROXMOX.md) |
| Change a local controller or workstation | [Local system maintenance](../operations/LOCAL_MAINTENANCE.md) |
| Configure notification destinations or alert volume | [Notifications](../services/NOTIFICATIONS.md) |
| Inspect audit activity or receive remote notification logs | [Minimal web panel](../services/WEB_PANEL.md) |
| Observe or take control of a Debian VM desktop in the browser | [Guacamole browser desktop](../workstations/GUACAMOLE.md) |
| Publish an internal site or preview | [Internal HTTPS sites and previews](../services/INTERNAL_WEB.md) |
| Deploy an application or manifest | [Deployments and manifests](../services/DEPLOYMENTS.md) |
| Configure CI/CD | [CI/CD webhook system](../services/CICD.md) |
| Configure shares, sync, parity, or backups | [Storage and data guides](../storage/README.md) |
| Provision or operate a coding VM | [Agent systems](../agents/README.md) |
| Set up Codex and T3 Code desktop on CachyOS | [CachyOS desktop example](../workstations/CACHYOS.md#install-with-t3-code-desktop) |
| Choose a headless, full-capability, approval-panel, or hardened agent VM | [Agentic VMs](../agents/AGENTIC_VMS.md) |

## Safe operating habits

- Inspect a saved host with `info` and `cmd` before a broad patch.
- Keep passwords and token-bearing webhook URLs out of shared output and
  tickets.
- Use feature-specific commands such as `shares` when they cover the intended
  change; they avoid unrelated setup work.
- Check [Machine types](MACHINE_TYPES.md) before expecting a kernel, firewall,
  desktop, or service capability in a container.

For the complete documentation map, return to the
[documentation index](../README.md).

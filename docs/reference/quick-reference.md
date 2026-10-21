# Basaltwater quick reference

Use this page to find the right command family and detailed guide. It is not a
replacement for the [command-line reference](command-line.md): read that page
before combining advanced options or changing a production host.
For a first experiment without a domain or SSH setup, follow the
[beginner walkthrough](../getting-started/getting-started.md).

## Common host lifecycle

| Goal | Command | Details |
| --- | --- | --- |
| Preview a new setup | `basaltw setup server_dev server.example admin --node --dry-run` | [Installation](../getting-started/installation.md#install-and-configure-a-remote-host) |
| Set up a new host | `basaltw setup server_dev server.example admin --node` | [CLI reference](command-line.md#setup-at-a-glance) |
| List saved hosts | `basaltw list` | [Saved configuration operations](../operations/operations.md#inspect-saved-hosts) |
| Inspect a saved host | `basaltw info server.example` | [Saved configuration operations](../operations/operations.md#inspect-saved-hosts) |
| View its saved command | `basaltw cmd server.example` | [Saved configuration operations](../operations/operations.md#recall-and-reconstruction) |
| Preview adding Python tools | `basaltw patch server.example admin --python --dry-run` | [Saved configuration operations](../operations/operations.md#patch-and-redeploy) |
| Update shares only | `basaltw shares fileserver` | [Samba shares](../storage/samba-shares.md) |
| Rerun a saved setup | `basaltw deploy server.example` | [Saved configuration operations](../operations/operations.md#patch-and-redeploy) |
| Allow LAN web access alongside a tunnel | `basaltw patch server.example admin --lan-access` | [Cloudflare direct access](../services/cloudflare.md#direct-access-alongside-the-tunnel) |
| Allow unrestricted web access alongside a tunnel | `basaltw patch server.example admin --cloudflare-allow-direct` | [Cloudflare direct access](../services/cloudflare.md#direct-access-alongside-the-tunnel) |
| Add browser access to an existing HTTPS desktop panel | `basaltw patch server.example admin --guacamole` | [Guacamole browser desktop](../workstations/guacamole.md) |
| Remove browser desktop access | `basaltw patch server.example admin --no-guacamole` | [Guacamole removal](../workstations/guacamole.md#diagnose-and-remove) |

`server.example` is a placeholder: replace it with your Debian target's IP
address or hostname, and replace `admin` with the account to configure there.
Remote setup requires root SSH key access. Interactive setup handles
[first-use host-key confirmation](../operations/ssh.md#first-connection-during-setup) inline;
unattended runs require an enrolled key.
The saved-host rows require a successful initial setup in the same workspace.
Remove `--dry-run` from the patch example to apply it.

## Existing CachyOS KDE workstation

After [installing the launcher](../workstations/cachyos.md#install-with-t3-code-desktop), run
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
See [the CachyOS guide](../workstations/cachyos.md) for optional project tools, cleanup, and
switching between T3 desktop and web mode. This profile uses direct setup
reruns; it does not populate the generic saved-host lifecycle above.

## Local service evidence

```bash
basaltw agent diagnostics --service nginx.service
basaltw agent diagnostics --service t3code.service --window boot --priority 7
basaltw agent diagnostics --service nginx.service --summary --output ./nginx-evidence.json
```

Run on the host being inspected. Default stdout is JSON containing `properties`,
`events`, and `issues`, using the shared diagnostic collector's redaction.
`--service` is required; all selectors accept only the fixed choices shown by
`basaltw agent diagnostics --help`. The defaults are `--window 24h` and
`--priority 4` (warnings and errors). There are no arbitrary unit or journal path inputs.
Collection is read-only, uses the invoking account's permissions without sudo,
and limits each query to five seconds and 64 KiB, with at most 100 journal events
and a fixed set of runtime properties. T3 Code uses the current user's service
manager and journal; other services use the system scope.

`--output PATH` saves that full redacted JSON with mode `0600` and keeps the
default full stdout. Choose a new filename in an existing directory owned by
the invoking account without group or other write access; relative paths are
allowed. Existing files, symlinks in any path component, and `..`
components are rejected. No parent directories are created and no files are
overwritten. Review redacted evidence before sharing it.
Ancestors must belong to root or the invoking account, with no group/other
write access unless sticky permissions protect their owned children (as in
`/tmp`). This prevents another account from replacing the reported file path.

Add `--summary --output PATH` to omit journal events from stdout only after
the full artifact has been saved successfully. The JSON summary retains all
service properties and collection issues, selected service/window/priority,
returned event count, counts by observed priority (`0`–`7`, or `unknown`),
absolute evidence path, an explicit events-omitted flag, and collector limits.
Counts describe the bounded returned events, not the whole journal.
If saving fails, stdout includes the full events and a generic save issue;
the command exits `1`. `--summary` without `--output` exits `2` before collection.

Exit `1` indicates collection issues (including unavailable or incomplete
evidence); exit `0` means no collection issues were reported. Neither exit `0`
nor empty logs prove service health: filters, permissions, and journal retention
can limit the evidence. Invalid command-line selectors exit `2` before collection.

## Find a task

| I need to… | Use |
| --- | --- |
| Install or upgrade the controller | [Installation and bootstrap](../getting-started/installation.md) |
| Understand a flag or command not shown above | [Command-line reference](command-line.md) |
| Connect, copy files, inspect logs, or check a service | [Sysadmin shortcuts](../operations/sysadmin.md) |
| Configure SSH keys or passphrases | [SSH authentication](../operations/ssh.md) |
| Manage recurring jobs, updates, or cleanup | [Recurring maintenance](../operations/maintenance.md) |
| Provision and operate a Proxmox VM or LXC | [Proxmox workflows](../operations/proxmox.md) |
| Change a local controller or workstation | [Local system maintenance](../operations/local-maintenance.md) |
| Configure notification destinations or alert volume | [Notifications](../services/notifications.md) |
| Inspect audit activity or receive remote notification logs | [Minimal web panel](../services/web-panel.md) |
| Observe or take control of a Debian VM desktop in the browser | [Guacamole browser desktop](../workstations/guacamole.md) |
| Publish an internal site or preview | [Internal HTTPS sites and previews](../services/internal-web.md) |
| Deploy an application or manifest | [Deployments and manifests](../services/deployments.md) |
| Configure CI/CD | [CI/CD webhook system](../services/cicd.md) |
| Configure shares, sync, parity, or backups | [Storage and data guides](../storage/README.md) |
| Provision or operate a coding VM | [Agent systems](../agents/README.md) |
| Set up Codex and T3 Code desktop on CachyOS | [CachyOS desktop example](../workstations/cachyos.md#install-with-t3-code-desktop) |
| Choose a headless, full-capability, approval-panel, or hardened agent VM | [Agentic VMs](../agents/agentic-vms.md) |

## Safe operating habits

- Inspect a saved host with `info` and `cmd` before a broad patch.
- Keep passwords and token-bearing webhook URLs out of shared output and
  tickets.
- Use feature-specific commands such as `shares` when they cover the intended
  change; they avoid unrelated setup work.
- Check [Machine types](machine-types.md) before expecting a kernel, firewall,
  desktop, or service capability in a container.

For the complete documentation map, return to the
[documentation index](../README.md).

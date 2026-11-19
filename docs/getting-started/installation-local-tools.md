# Local tools, workspaces, and credentials

[Documentation index](installation.md)

## Optional local tools

The installer already bootstraps the launcher and shell completion. For a
manual completion refresh or another shell:

```bash
basaltw completions --shell bash
basaltw completions --shell zsh
```

See [Shell completion](../reference/shell-completion.md) for system-wide and Fish setup.

## Workspace and credentials

Saved host state defaults to `~/.config/basaltwater`. Use another workspace
when separating projects or test environments:

```bash
basaltw --workspace /srv/basaltwater-workspace list
```

The `credentials` commands manage the workspace password store used by
features such as Samba/SMB; they do not configure GitHub, Codex, Claude Code,
or OpenCode:

```bash
basaltw credentials set admin --service gogs --destination server.example
basaltw credentials list
basaltw credentials remove admin --service gogs --destination server.example
```

Passwords are excluded from saved setup state and reconstructed commands.

Use `basaltw cleanup --dry-run` to inspect incompatible local setup or Proxmox
records, then `basaltw cleanup --yes` to remove the reviewed findings. Cleanup
locks affected state against managed writers and refuses files changed since
inspection. Its private timestamped backups under `<workspace>/cleanup-backups/`
retain workspace-relative paths, so identically named files keep separate
copies. Symlinked cache or backup directories are refused.

For agent VM authentication, provider-specific login/file/GitHub sources, per-VM isolation, and remote
rotation, see [Agent authentication](../agents/agent-authentication.md). For repository
policy and HTTPS credentials, see [Git access](../agents/git-access.md).

## Related guides

- [Command-line reference](../reference/command-line.md) — all setup flags and system types
- [Credentials overview](../agents/credentials.md) — choose the correct credential flow
- [Agent authentication](../agents/agent-authentication.md) — agent auth and recovery
- [Git access](../agents/git-access.md) — repository policy and HTTPS authentication
- [Workstations](../workstations/workstations.md) — desktop profiles, browsers, and agents
- [XRDP](../workstations/xrdp.md) — RDP sessions and firewall behavior
- [Machine types](../reference/machine-types.md) — Debian VMs, bare metal, and containers

You can review the downloaded [`install.sh`](../../install.sh) before executing
it. The examples download a file first and do not pipe network content into a
privileged shell.

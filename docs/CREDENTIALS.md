# Credentials overview

Basaltwater handles several kinds of credentials. Keep them separate because
they have different storage, rotation, and sharing rules.

| Credential | Used for | Manage it with |
| --- | --- | --- |
| Workspace password | Syncthing administration, initial Gogs administration, Samba, SMB mounts, and managed non-GitHub Git origins | `basaltw credentials` |
| HomeBox initial password and API-key pepper | Inventory login and API-key verification | Target-only `/etc/homebox/secrets.json`; see [HomeBox](HOMEBOX.md) for recovery |
| GitHub CLI authentication | Private GitHub repositories and `gh` | Git or agent auth options |
| Coding-agent authentication | Codex, Claude Code, and OpenCode | Agent auth options |
| Target account password | Unix login and optional RDP login | `--password` or a hidden setup prompt |
| Device-enrollment password | Protected provider-pairing portal | Device-pairing options |
| Browser website session | Sites opened through browser automation | The site or a scoped secret flow; Basaltwater does not copy it |

## Choose the right workflow

| Need | Guide |
| --- | --- |
| Save a service or self-hosted Git password | [Workspace credential store](#workspace-credential-store) |
| Seed or rotate `gh`, Codex, Claude Code, or OpenCode auth | [Agent authentication](AGENT_AUTHENTICATION.md) |
| Recover agent auth from an existing VM | [Pull credentials from an agent VM](AGENT_AUTHENTICATION.md#pull-credentials-from-an-agent-vm) |
| Configure GitHub or another HTTPS Git server | [Git access and authentication](GIT_ACCESS.md) |
| Configure the pairing portal | [Protected device pairing](DEVICE_PAIRING.md) |
| Diagnose SSH login | [SSH authentication](SSH.md) |

## Workspace credential store

The workspace store contains named passwords for Basaltwater-managed services.
It is not read by GitHub CLI, Codex, Claude Code, or OpenCode.

```bash
basaltw credentials set workspace-user
basaltw credentials list
basaltw credentials remove workspace-user
```

Omit the password from `credentials set` to use a hidden prompt. The default
store is private to the active workspace:

```text
~/.config/basaltwater/credentials.json
```

Use the global `--workspace PATH` option to select another workspace. The
repeatable setup option `--credential USERNAME PASSWORD` updates the same
store, but a command-line password may be exposed through shell history and
the process list.

Common consumers are:

- managed Syncthing, using `syncthing-admin` by default;
- initial Gogs administration when the name matches the setup user;
- Samba shares and SMB mounts; and
- origin-scoped Git HTTPS credentials for non-GitHub servers.

Saved setup commands and ordinary summaries omit workspace passwords. Consult
the service-specific guide to learn whether rerunning setup rotates or
preserves its target value.

## Agent and Git credentials

Agent-provider credentials are file-backed authentication for `gh`, Codex,
Claude Code, and OpenCode. They are independent from tool installation and
from non-secret agent configuration.

[Agent authentication](AGENT_AUTHENTICATION.md) covers canonical paths,
credential sources, initial seeding, status, rotation, recovery from an
existing VM, portability, and lifecycle.

[Git access and authentication](GIT_ACCESS.md) covers Git policy, GitHub CLI,
private repositories, self-hosted HTTPS origins, private certificate
authorities, and Git LFS.

## Security checklist

- Keep credential files outside repositories and protect them with mode `0600`.
- Prefer hidden prompts or protected files over command-line secrets.
- Use one least-privilege provider identity per VM or trust boundary.
- Treat copied static tokens as one shared identity with one revocation scope.
- Do not run copied renewable Codex ChatGPT sessions concurrently.
- Inspect `agent auth status` before replacing a target credential.
- Revoke provider-side tokens when a VM retires or may be compromised.
- Do not treat `--git-access read` as a provider-side permission boundary.

## Quick troubleshooting

| Symptom | Check |
| --- | --- |
| Private GitHub clone fails | Tool selection, auth source, repository authorization, and `--git-access` in [Git access](GIT_ACCESS.md) |
| Agent auth source is missing | Canonical path and keyring limitations in [Agent authentication](AGENT_AUTHENTICATION.md) |
| Source file is rejected | It must be regular, non-symlink, nonempty, no larger than 4 MiB, and not group/world-writable |
| Codex reports expired auth | Run `basaltw agent doctor HOST USER --tool codex --capability host --json`, then use [Codex maintenance](AGENT_AUTHENTICATION.md#codex-maintenance) |
| Syncthing, Samba, or Gogs password is wrong | Confirm the workspace, credential name, and service-specific rotation procedure |

## Related documentation

- [Agent authentication](AGENT_AUTHENTICATION.md)
- [Git access and authentication](GIT_ACCESS.md)
- [Command-line reference](COMMAND_LINE.md)
- [Installation](INSTALLATION.md)
- [Workstations](WORKSTATIONS.md)
- [Samba shares](SAMBA_SHARES.md)
- [Managed Syncthing](SYNCTHING.md)

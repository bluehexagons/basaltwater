# Credentials overview

Basaltwater handles several kinds of credentials. Keep them separate because
they have different storage, rotation, and sharing rules.

| Credential | Used for | Manage it with |
| --- | --- | --- |
| Workspace password or token | Syncthing administration, initial Gogs administration, Samba, SMB mounts, managed non-GitHub Git origins, and explicitly selected GitHub tokens | `basaltw credentials` |
| HomeBox initial password and API-key pepper | Inventory login and API-key verification | Target-only `/etc/homebox/secrets.json`; see [HomeBox](../services/homebox.md) for recovery |
| GitHub CLI authentication | Private GitHub repositories and `gh` | Git or agent auth options |
| Coding-agent authentication | Codex, Claude Code, and OpenCode | Agent auth options |
| Game publishing sessions and optional Steam beta API key | Butler uploads, SteamCMD builds and optional beta promotion | Publishing in the VM HTTPS panel; credentials stay VM-local |
| Target account password | Unix login and optional RDP login | `--password` or a hidden setup prompt |
| Device-enrollment password | Protected provider-pairing portal | Device-pairing options |
| Browser website session | Sites opened through browser automation | The site or a scoped secret flow; Basaltwater does not copy it |

## Choose the right workflow

| Need | Guide |
| --- | --- |
| Save a service or self-hosted Git password | [Workspace credential store](#workspace-credential-store) |
| Seed or rotate `gh`, Codex, Claude Code, or OpenCode auth | [Agent authentication](agent-authentication.md) |
| Authorize or recover Codex on a VM | [Target-owned subscription or API-key login](agent-authentication.md#authorize-codex-on-a-target) |
| Configure GitHub or another HTTPS Git server | [Git access and authentication](git-access.md) |
| Configure the pairing portal | [Protected device pairing](../services/device-pairing.md) |
| Authenticate game publishers without controller credential copies | [VM game publishing](../workstations/game-publishing.md#install-and-authenticate) |
| Diagnose SSH login | [SSH authentication](../operations/ssh.md) |

## Workspace credential store

The workspace store keys each password or token by service, destination, and
credential name. The same username can have independent passwords for
different Git origins, Gogs targets, and SMB shares. Provider tools use their
own authentication. Setup can resolve a saved GitHub token and install it for
the target's GitHub CLI.

```bash
basaltw credentials set alice --service git --destination https://git.example.test:8443
basaltw credentials set alice --service gogs --destination server.example.test
basaltw credentials set alice --service smb --destination //nas.example.test/docs
basaltw credentials list
basaltw credentials remove alice --service smb --destination //nas.example.test/docs
```

Omit the password from `credentials set` to use a hidden prompt. Listing shows
the service, destination, and name. Use the global `--workspace PATH` option to
select a different workspace. The default store lives at
`~/.config/basaltwater/credentials.json`.

| Service | Destination | How setup selects the name |
| --- | --- | --- |
| `git` | HTTPS origin, including any nondefault port | `--git-credential HTTPS_ORIGIN USERNAME` |
| `gogs` | Setup target host | The setup username, when `--gogs` is enabled |
| `smb` | `//HOST/SHARE` from the mount specification | The username in `--mount-smb /mnt/MOUNT HOST USERNAME SHARE SUBDIR` |
| `samba` | Setup target host | Usernames in `--share` |
| `syncthing` | Setup target host | `--syncthing-admin USERNAME`, which defaults to `syncthing-admin` |
| `antistatic` | Setup target host | `--antistatic-admin USERNAME` |
| `github` | Setup target host receiving the token | `--git-auth-credential NAME` |

Use the host from the setup command for target-scoped services. For Gogs,
this is the host running the instance. The public Gogs domain belongs in the
`git` scope when it forms the origin used by Git clients. An SMB scope follows
the server and share regardless of the client machine, mountpoint, or
subdirectory. Use the share spelling from `--mount-smb`. Use the configured
host spelling for destination aliases. Git origins normalize case and the
default HTTPS port.

Saved controller configurations retain these selectors. A fresh setup, patch,
or replay uses the current value for each scope. To rotate a saved secret, run
`credentials set` with the same service, destination, and name. Gogs generates
an initial admin password when that target and user have no Gogs entry. Gogs
preserves an existing admin account.

The store must be an owned regular file with one link, no symlink, and valid
versioned JSON no larger than 1 MiB. Reads repair its permissions to `0600`
through the opened file descriptor. Invalid stores block credential updates and
remain available for recovery; only a missing file starts an empty store.

### Migrate unbound credentials

Version 1 entries appear as `legacy` and `(unbound)` in `credentials list`.
Setup never selects them by name. Verify which account and destination a legacy
value belongs to before copying it into a scope.

```bash
basaltw credentials migrate alice --service git --destination https://git.example.test:8443
```

Migration keeps the legacy entry and refuses to overwrite a different value
already saved in that scope. Scoped writes upgrade the file to version 2.
Use a controller with version 2 support for subsequent operations. If the old
value has an uncertain destination, use
`credentials set` with a verified password for each intended scope.
`credentials remove alice --service legacy` removes only the unbound entry.
For compatibility, omitting both scope flags with `credentials set` or
`credentials remove` selects the legacy entry.

The repeatable setup option `--credential USERNAME PASSWORD` applies only
when the command declares exactly one scope for that name. Setup rejects the
option if the name is unused or appears in multiple scopes. Inline Samba and
SMB passwords use the scope of the share or mount. A real setup saves these
explicit inputs in their resolved scopes. Command-line secrets can be exposed
through shell history and the process list. Prefer the hidden prompt.

For initial Gogs administration, use a unique bootstrap password and rotate it
in Gogs after setup. The target's account-creation command receives the
password as a process argument even when setup generates it or reads it from
the workspace. A process observer or an execution audit rule on the target
may record it. [Gogs account creation](../../web/gogs_steps.py)

Saved setup commands and ordinary summaries omit workspace passwords. Consult
the service-specific guide to learn whether rerunning setup rotates or
preserves its target value.

## Agent and Git credentials

Agent-provider credentials are file-backed authentication for `gh`, Codex,
Claude Code, and OpenCode. They are independent from tool installation and
from non-secret agent configuration.

[Agent authentication](agent-authentication.md) covers canonical paths,
target-owned subscription login, explicit API-key billing, credential imports,
status, rotation, recovery, and lifecycle. Codex login requires no local Codex
installation on the controller. Credential pulling has been removed.

[Git access and authentication](git-access.md) covers Git policy, GitHub CLI,
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
| Private GitHub clone fails | Tool selection, auth source, repository authorization, and `--git-access` in [Git access](git-access.md) |
| Agent auth source is missing | Canonical path and keyring limitations in [Agent authentication](agent-authentication.md) |
| Source file is rejected | It must be regular, non-symlink, nonempty, no larger than 4 MiB, and not group/world-writable |
| Codex reports expired auth | Run `basaltw agent doctor HOST USER --tool codex --capability host --json`, then use [Codex maintenance](agent-authentication.md#codex-maintenance) |
| Syncthing, Samba, or Gogs password is wrong | Confirm the workspace, service, destination, credential name, and service-specific rotation procedure |

## Related documentation

- [Agent authentication](agent-authentication.md)
- [Git access and authentication](git-access.md)
- [Command-line reference](../reference/command-line.md)
- [Installation](../getting-started/installation.md)
- [Workstations](../workstations/workstations.md)
- [Samba shares](../storage/samba-shares.md)
- [Managed Syncthing](../storage/syncthing.md)

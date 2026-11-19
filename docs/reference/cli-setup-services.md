# Service and access setup flags

[Documentation index](command-line.md)

### Setup completion access details

After a successful setup, the `Setup Complete!` block includes an `Access:`
section for selected web interfaces and services. It lists each usable URL or
endpoint with a one-line description: T3 Code, the optional protected
device-pairing portal, the generic web server, Gogs, Antistatic services, RDP,
Samba, and SSH. For example, a T3 Code setup with device pairing reports the
generated managed HTTPS endpoints as the usable links.
The direct T3 and Basic Auth listeners are intentionally omitted from this
completion summary.

Setup step output remains live while it runs. Marked warnings and errors are
also deduplicated into a final `Run notes:` section so important non-fatal
issues are easy to find without scanning the full transcript.

Select coding providers with `--codex`, `--claude`, and/or `--opencode`.
The general `--agent-tool` selector also accepts repeatable or comma-separated
values. The agent profiles default to `gh` only; coding providers are explicit;
supplied tools add to that set, while `--no-agent-tool` removes selected
defaults. Other profiles retain no implicit agents. Add unrelated packages with
`--apt-install`, and add runtimes with their individual flags. `--godot` works
with every profile, including the headless `agent_vm`; its system-wide launchers
make the engine available in SSH, desktop, T3 Code, and coding-agent shells.
Repeat `--godot-bundle` to give that same target account web-export and
publishing commands without changing its agent configuration.

Any setup with agent features installs a managed `~/.local/bin/basaltw`
launcher for the target user. On a remote setup it uses the source
deployed under `/opt/basaltwater`, so diagnostics and deliberate agent updates
work directly from an SSH, desktop, or T3 Code terminal without a separate
Basaltwater installation on the VM. An existing executable with that name is
retained; setup never overwrites an unmanaged user launcher.

When Codex or OpenCode is selected, setup also installs the shared base
workflow skills under `~/.agents/skills`. Selected Claude Code installations
receive the catalog under `~/.claude/skills`. Supporting references, scripts,
assets, and agent metadata are copied with each bundle and tracked for safe
refresh and retirement. T3 Code and Godot web setup add their
capability-specific skills. See [Managed agent workflow
skills](../agents/agent-skills.md) for the catalog and reconciliation rules.

T3 Code hosting is selected with `--web-interface t3code`; it is not an `--agent-tool`
provider. On CachyOS, `--t3code-desktop` instead installs or integrates the
upstream-listed AUR desktop package. Rerun with the other flag to switch modes;
desktop and managed web data remain separate and are never deleted or merged.
See [CachyOS desktop setup](../workstations/cachyos.md#t3-code-desktop). The Debian server
path installs Node and T3 Code's upstream per-user background service; see
[t3-code.md](../agents/t3-code.md) for updates, LAN access, pairing, client choices, and
the loopback/HTTPS boundary. The optional
`--device-pairing t3code` portal lets a new device request its own one-time
link through Nginx Basic Auth. Setup publishes the portal through the managed
HTTPS gateway by default; see [device-pairing.md](../services/device-pairing.md).

For a managed VM/server with a LAN T3 Code service, obtain its one-time
administrative pairing URL from the control system with
`basaltw agent web pair HOST USER` (add `--key PATH` when needed). The
resulting app session includes T3's `access:write` scope for pairing-link and
client-session management. Opening the bare service address is expected to show
T3's pairing-key form; it is not an authenticated session. The local CachyOS
profile does not use this controller-side command; generate its native pairing
link and configure T3 Connect from the [CachyOS guide](../workstations/cachyos.md#t3-code-web-service-host-locally-or-on-a-trusted-lan).

When the protected portal is selected, open the printed **T3 Code pairing
HTTPS endpoint**, answer the Basic Auth challenge, and pair the current browser
or create a link for a desktop/mobile client. Basic Auth protects only enrollment; T3 continues to
own and revoke the resulting device session. The direct HTTP mode is limited
to trusted private source CIDRs and does not encrypt the Basic Auth password.

Codex CLI, Claude Code, OpenCode, and T3 Code are installed from their official
distribution channels. T3 Code uses its documented background-service updater
with lifecycle scripts enabled only for that trusted update; see
[t3-code.md](../agents/t3-code.md) for the exact host-side command and recovery path.
The Codex installer runs with `CODEX_NON_INTERACTIVE=1`,
so setup does not prompt to start Codex or remove a conflicting installation.
The other agent tools are not installed with npm. Any
selected agent installs only that agent's tool and its required installer
dependencies.

Credential seeding and config copy are intentionally tool-scoped and transient:

- `--git-auth`, `--git-auth-file`, `--git-auth-token`, and `--git-auth-credential` seed only a missing selected GitHub host entry, preserve target-managed credentials on rerun, and run `gh auth setup-git`. Named workspace references persist on the controller; token values and staged payloads do not enter setup history.
- `--agent-auth login` retains or renews target Codex auth and starts device authorization when needed in a terminal. Unattended setup fails clearly when authorization is required.
- Explicit `--agent-auth-file TOOL PATH` seeds that provider's missing credentials without requiring the tool on the controller; other providers retain their defaults. A Codex file also replaces refresh-required auth when the staged source is unambiguously current. Coding-agent active copying is removed; `--git-auth active` remains GitHub-specific and requires controller `gh` only when its token is keyring-backed.
- `--agent-config active` copies known non-secret configuration from the active controller user.
- Codex, OpenCode, and Claude Code receive only non-secret managed workflow
  skills; T3 Code adds its focused service and HTTPS-gateway guidance.
  Basaltwater does not copy T3 Code credentials.

The root-only upload payload is removed after selected config is applied and
credentials are reconciled. Repositories are never cloned or cached on the
controller: the target VM performs each HTTPS clone after GitHub credentials
are configured.
Public repositories on any reachable Git host work without credentials. Agent
repository URLs with embedded credentials, SSH/scp syntax, and non-HTTPS schemes
are rejected. A requested repository that cannot be cloned stops setup, while an
existing repository is preserved only when its origin exactly matches.
Private non-GitHub origins use the separate `--git-credential` flow. Its
password is resolved from the workspace credential store, never embedded in a
repository URL or saved setup declaration. `--git-ca-certificate` optionally
adds origin-scoped trust for an internal CA or self-signed service certificate.
The source may be a local file or a certificate read directly from another
Basaltwater host over host-key-verified SSH, so no intermediate controller copy
is required. It does not disable certificate verification globally. The target
stores the credential in a dedicated mode-`0600` file because unattended Git
and Git LFS need persistent access.

## Choose a topic

- [Agent diagnostics and storage commands](cli-agent-diagnostics.md) Manifest, doctor, visuals, capacity, and cleanup.

- [Agent workflows and maintenance commands](cli-agent-operations.md) Worktrees, model policy, tasks, memory, support bundles, updates, and authentication.

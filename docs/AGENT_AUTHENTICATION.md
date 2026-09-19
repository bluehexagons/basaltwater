# Agent authentication

Basaltwater manages Codex authentication through `agent auth login`. ChatGPT
subscription login is the default; API-key authentication is an explicit option.
Each VM owns its renewable session. The controller needs Python and SSH, but
does not need Codex, stored subscription credentials, or a graphical desktop.

## Authorize Codex on a target

From the controller, run:

```bash
python3 basaltwater.py agent auth login 192.168.0.44 agent
```

The command starts Codex's device authorization flow on the target and prints
a URL and one-time code. Open that URL in a browser on any trusted device and
enter the code. Device authorization must be enabled for your ChatGPT account
or workspace. Add `--open-browser` to open the page on the controller when a
browser is available. This still uses device authorization and does not require
a callback port or SSH tunnel. Fully unattended subscription sign-in is not
supported: the provider requires the account holder to authorize the device.

The target needs a recent Codex version with device-code app-server support
(the protocol is checked against 0.155.1). Add `--key PATH` or `--port PORT`
for SSH settings. Host-key enrollment follows the usual strict SSH policy.
The target does not need an existing Basaltwater installation for this command.

Authorization stages credentials privately on the target, then atomically
replaces `~/.codex/auth.json` with mode `0600`. It sets the user's
`cli_auth_credentials_store` to `file`, preserving unrelated TOML settings.
Cancellation, rejection, and timeout preserve the previous credential;
concurrent changes to credentials or config stop replacement. The command
waits at most 15 minutes for authorization and returns status 3 if it cannot
complete. Subscription tokens never cross SSH to the controller.
The controller sends its current login helper over SSH, so updating the
controller checkout also updates this command's target-side login logic.
An SSH disconnect cancels an incomplete authorization; normal completion stops
the disconnect watcher before the helper exits.

### Setup and recovery

`agent_vm`, `agent_workstation`, and `agent_code_vm` default to
`--agent-auth login` when Codex is selected. Other Codex-enabled profiles can
select it explicitly. `--agent-auth active` has been removed; replace it with
`--agent-auth login` in existing commands.

Setup first retains or renews the target credential. If authorization is
needed and setup was launched from a terminal, it prints a device code and
continues after you authorize. This also recovers a copied session whose
refresh token was already used elsewhere. When input or output is redirected,
setup fails with the controller login command instead of waiting for a human.
Run that command and rerun setup. `--agent-auth none` disables this profile
default. A healthy existing API-key credential is also retained.

Credential pulling has been removed. An existing VM's subscription session
cannot serve as a reusable source of independent sessions for other VMs.
Authorize each target independently; do not copy rotating refresh tokens.

### Optional API-key authentication

**API-key usage is billed separately through the OpenAI API; it does not use
your ChatGPT subscription allowance.** Basaltwater prints this notice before
collecting an API key and never switches to API billing automatically.

```bash
# Hidden terminal prompt:
python3 basaltwater.py agent auth login 192.168.0.44 agent --method api-key

# A mode-0600 file owned by the controller user:
python3 basaltwater.py agent auth login 192.168.0.44 agent \
  --method api-key --api-key-file /run/secrets/agent-openai-key

# Or pass a secret-manager output stream with --api-key-stdin.
```

Keys travel through SSH stdin and are never command arguments or saved setup
values. A successful API-key login confirms storage, not provider acceptance
or available billing quota; it does not issue a billable model request.
Run subscription login again to switch back to your ChatGPT account.

See OpenAI's [authentication guide](https://learn.chatgpt.com/docs/auth) for
account requirements and billing modes.

## Supported files

| Tool | Target path |
| --- | --- |
| GitHub CLI (`gh`) | `~/.config/gh/hosts.yml` |
| Codex | `~/.codex/auth.json` |
| Claude Code | `~/.claude/.credentials.json` |
| OpenCode | `~/.local/share/opencode/auth.json` |

These are the standard Linux paths used by basaltwater. Supply an explicit
file when a vendor setting relocates its credential.

Selecting an agent tool does not create authentication. Select tools with
`--agent-tool` and choose credentials separately:

```bash
--agent-tool gh,codex,opencode \
--agent-auth login
```

## Credential sources

Authentication defaults are independent for each provider:

| Provider | Default on agent VM profiles | Override |
| --- | --- | --- |
| Codex | Target-owned subscription login | `--agent-auth none`, explicit `--agent-auth-file codex PATH`, or `agent auth login --method api-key` |
| GitHub CLI | Active GitHub credentials on `agent_code_vm`; unselected on `agent_vm` and `agent_workstation` | `--git-auth active\|none`, `--git-auth-file PATH`, or interactive token input |
| Claude Code / OpenCode | No automatic credential import | `--agent-auth-file claude PATH` / `--agent-auth-file opencode PATH`, or authenticate on the target |

Codex login and another provider's file import can be combined:

```bash
basaltw setup agent_vm HOST USER --agent-tool claude \
  --agent-auth login --agent-auth-file claude /run/secrets/claude.json \
  --git-access read --git-auth active
```

A file overrides only its provider's default. An explicit Codex login and
Codex file import together are rejected. Removing Codex from the selected
tools disables its default login. `--agent-auth none` controls Codex login;
it does not disable explicitly selected provider files or GitHub credentials.

### Active GitHub credentials

`--git-auth active` reads GitHub credentials from the invoking controller user,
or the original user under `sudo`. Coding-agent active credential copying has
been removed, including `agent auth set --active` for Codex, Claude, and
OpenCode; that rotation option now supports only `--tool gh`.

For `gh`, Basaltwater uses the selected `hosts.yml` token. If GitHub CLI stores
the token in an operating-system keyring, Basaltwater asks the authenticated
controller-local `gh` command for it. This GitHub-specific flow is independent
of Codex subscription authorization.

### Specified files

Explicit files work when the controller has no local agent installation or
when each VM has a separate identity:

```bash
--git-auth-file /run/secrets/agent-1/hosts.yml
--agent-auth-file codex /run/secrets/agent-1/codex-auth.json
--agent-auth-file opencode /run/secrets/agent-1/opencode-auth.json
```

`--agent-auth-file` is repeatable. A source must be a regular non-symlink file,
must not be group- or world-writable, and must be no larger than 4 MiB. The
controller does not need the corresponding agent executable.

Do not combine active and file sources for GitHub. GitHub authentication
must come from exactly one of `--git-auth`, `--git-auth-file`,
`--agent-auth-file gh`, or the interactive token prompt.

### Interactive setup

`--interactive` can prompt for tools, Git policy, auth sources, non-secret
configuration, and optional pairing. Hidden prompts keep tokens and passwords
out of process arguments. Automation should use protected files. Dry-run mode
does not prompt for or stage credentials.

## Explicit credential imports, status, and rotation

Explicit file imports install missing selected credentials at the
canonical path with mode `0600`. Ordinary reruns preserve existing mutable
credentials. The narrow
exception is a Codex target marked `refresh_required`, `refresh_due`, or
`expires_soon`: a staged, unambiguously current source may replace it. Setup
therefore consumes a supplied current credential before an overdue target's
access token expires, without requiring a separate `agent auth set` command.

If both files need renewal and the supplied ChatGPT refresh token differs from
the target's, setup first attempts to renew the supplied credential in a private,
temporary home on the target VM. It uses the target user's Codex executable and
installs the result only after successful renewal and a fresh metadata check.
Failure preserves the existing target credential. A target that becomes current
during the attempt is also preserved. Temporary files are removed afterward.
This applies to `--agent-auth-file codex PATH`;
the controller does not need Codex installed. Identical refresh tokens are not
tested as a separate recovery source.

Recovery accepts the runtime's managed `agent_payload` link into its private
setup workspace. It validates that workspace's location, ownership, and
permissions before reading; symlinks inside the payload or in credential
destinations remain rejected. Setup recreates the link on each run and removes
the uploaded payload afterward, so no manual link repair is needed.

Renewal rotates the staged session for use on the target. It does not update the
controller's copy or synchronize credentials with the source VM. Reusing that
session concurrently on another machine can invalidate its older copies.

Use `agent auth set` for every other intentional replacement:

```bash
basaltw agent auth status 192.168.0.41 agent-1 --json
basaltw agent auth set 192.168.0.41 agent-1 \
  --tool codex --file /run/secrets/agent-1/codex-auth.json
basaltw agent auth set 192.168.0.41 agent-1 --tool gh --active
```

Status reports installation, presence, ownership, permissions, age, and safe
Codex freshness metadata without displaying file contents, tokens, or account
IDs. Rotation replaces the selected target atomically. Inspect status before
replacing a credential.

## Portability and sharing

| Tool | Guidance |
| --- | --- |
| `gh` | A `hosts.yml` with an embedded token is portable. A keyring-only file is not; use authenticated `gh` on the source controller or a protected token file. |
| Codex | `auth.json` is file-backed, but ChatGPT OAuth state is renewable per machine. Do not use copies concurrently. Prefer independent login or a dedicated automation credential. |
| Claude Code | Linux commonly uses the credentials file; macOS may use Keychain. A Keychain session is not exported by copying this file. |
| OpenCode | The JSON file can be copied, but use separate provider identities when independent revocation and audit are required. |

Sharing a static token means every VM has the token's full scope, provider
audit logs show the same identity, and one revocation affects every copy. Use
least-privilege, per-VM credentials where practical.

## Non-secret agent configuration

`--agent-config active` copies settings, instructions, skills, rules, aliases,
and extensions for selected tools. It excludes auth files and `gh` `hosts.yml`.
A setting that selects a keyring backend does not make a portable credential
file appear. Missing configuration is skipped.

T3 Code is an interface rather than a credential source. Its server uses the
provider credentials installed for the target user, while pairing state is
managed on the VM. Browser cookies and website sessions are also outside this
copying flow.

## Codex maintenance

Codex-enabled VMs receive a non-root daily maintenance timer with an additional
check after boot. It asks Codex to refresh file-backed ChatGPT authentication
only when safe metadata reports stale or uncertain state. It does not refresh
API-key auth.

The eight-day refresh interval is a local maintenance threshold, not a token
expiry date. When the cached access token has a future expiry, an overdue
refresh is reported as `refresh_due`: maintenance still attempts renewal, but
the age alone does not fail agent-update readiness. Expired tokens, or overdue
credentials with no readable expiry, remain `refresh_required`. These local
metadata checks do not establish whether the provider has revoked a token.

An explicit setup rerun performs the same bounded freshness check once after
installing or configuring Codex, before the managed agent update. This gives a
dormant VM a chance to renew its cached login before setup records post-update
readiness.

If Codex authentication fails:

```bash
basaltw agent doctor HOST USER --tool codex --capability host --json
basaltw agent auth status HOST USER --tool codex --json
```

Inspect `codex-auth-maintenance.service` when the timer is present. If the file
is invalid, lacks required refresh state, or remains stale after provider
rejection, run `basaltw agent auth login HOST USER` or rerun setup with
`--agent-auth login` from a terminal.

Maintenance explicitly selects file storage for its child Codex process and
checks the private credential file after the request, even when the selected
model provider returns no account. A newly current file counts as successful
renewal; a returned account alone does not. The user's Codex configuration is
not rewritten.

Transient refresh failures receive one automatic retry within the service's
two-minute timeout. Vendor stderr is drained in bounded memory, reduced to
fixed failure categories, and never copied into Basaltwater logs. Recognized
terminal failures (`refresh_token_expired`, `refresh_token_reused`,
`refresh_token_invalidated`, `invalid_grant`) are not retried. Setup with
`--agent-auth login` starts a fresh authorization after failed renewal; other
setup modes show sanitized maintenance output directly. If no usable replacement was supplied
and the provider has rejected the refresh credential, unattended renewal is
not possible; repeating the same rejected credential cannot repair it.

Maintenance also distinguishes `account_read_rpc_error` (with a numeric JSON-RPC
code when available), `no_account_returned`, `unexpected_account_type`, and
`invalid_account_response`. These account-response categories do not by themselves prove that
the provider rejected a refresh token. Provider response messages and account
contents are deliberately omitted from logs. A successful earlier timer run
may simply have occurred before the local refresh threshold; an inactive
oneshot service with exit status zero is normal.

## Security and lifecycle

- Keep sources in protected directories outside repositories.
- Credential source paths and payloads are not stored in setup history.
- Setup removes transient staged copies after success or failure.
- Use separate identities for separate revocation and audit boundaries.
- Revoke provider-side credentials when a VM retires or may be compromised.
- Do not distribute one renewable Codex ChatGPT session to concurrent VMs.

See [Credentials overview](CREDENTIALS.md), [Git access](GIT_ACCESS.md), and
[Agentic coding security](AGENT_SECURITY.md).

# Agent authentication

This guide covers file-backed authentication for GitHub CLI, Codex, Claude
Code, and OpenCode on managed agent VMs. Tool installation, authentication,
non-secret configuration, and browser website sessions are separate concerns.

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
--agent-auth active
```

## Credential sources

### Active controller user

`--agent-auth active` reads supported files from the invoking controller user,
or the original user under `sudo`. `--git-auth active` is the GitHub-specific
form.

For `gh`, Basaltwater uses the selected `hosts.yml` token. If GitHub CLI stores
the token in an operating-system keyring, Basaltwater asks the authenticated
controller-local `gh` command for it. Other tools use only their file-backed
paths; Basaltwater does not run them to create credentials.

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

Do not combine active and file sources for the same tool. GitHub authentication
must come from exactly one of `--git-auth`, `--git-auth-file`,
`--agent-auth-file gh`, or the interactive token prompt.

### Interactive setup

`--interactive` can prompt for tools, Git policy, auth sources, non-secret
configuration, and optional pairing. Hidden prompts keep tokens and passwords
out of process arguments. Automation should use protected files. Dry-run mode
does not prompt for or stage credentials.

## Setup, status, and rotation

Initial setup installs missing selected credentials at the canonical path with
mode `0600`. Ordinary reruns preserve existing mutable credentials. The narrow
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
This applies to both `--agent-auth active` and `--agent-auth-file codex PATH`;
the controller does not need Codex installed. Identical refresh tokens are not
tested as a separate recovery source.

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

## Pull credentials from an agent VM

Run the pull command from a cloned Basaltwater repository. The Debian or
CachyOS control system needs Python 3 and OpenSSH, but does not need a
Basaltwater installation or local agent programs.

```bash
python3 basaltwater.py ssh-key enroll 192.168.0.41
python3 basaltwater.py agent auth pull 192.168.0.41 agent-1
```

| Option | Purpose |
| --- | --- |
| `--tool TOOL` | Pull only `gh`, `codex`, `claude`, or `opencode`; repeat as needed |
| `--output-dir PATH` | Write renamed files to a private staging directory instead of active-user paths |
| `-k, --key PATH` | SSH identity file |
| `-p, --port PORT` | SSH port |
| `--overwrite` | Deliberately replace other existing regular files |

Without `--tool`, absent files are skipped. An explicitly requested but absent
file fails. The command uses the workspace's strict SSH host-key policy and
never prints credential contents or remote error text. It reads only the
canonical paths above and rejects unsafe ownership, permissions, symlinks,
changes during transfer, empty files, and files over 4 MiB.

By default, each file is written to the active controller user's canonical
path from the table above. Parent directories must be owned by that user and
not writable by other users; new directories use mode `0700`. Output files are
atomically written with mode `0600`. With `--output-dir`, the names are
`gh-hosts.yml`, `codex-auth.json`, `claude-credentials.json`, and
`opencode-auth.json`, ready for the matching setup option or `agent auth set
--file`.

Without `--overwrite`, an existing file is retained except when stale Codex
auth can be safely refreshed from a current pulled credential. If both Codex
files contain expired cached access tokens, the command reports an error.
Expiry cannot be established safely for the other formats, so replacing those
still requires `--overwrite`. The command cannot export an operating-system
keyring; a `gh` file without an embedded token is not portable.

The pull destination is on the machine running the command, not the named VM.
When replacement is declined, Codex diagnostics show source and destination
freshness classifications. For an intentional replacement, select only the
desired tool and add `--overwrite`; use a new `--output-dir` to stage a separate
copy without changing active credentials.

Pulling is for migration or recovery, not synchronization. Stop using the
source VM's renewable Codex ChatGPT session before activating its pulled
`auth.json` elsewhere.

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
rejection, authenticate the VM independently or deliberately replace it with
`agent auth set`.

Maintenance explicitly selects file storage for its child Codex process and
checks the private credential file after the request, even when the selected
model provider returns no account. A newly current file counts as successful
renewal; a returned account alone does not. The user's Codex configuration is
not rewritten.

Transient refresh failures receive one automatic retry within the service's
two-minute timeout. Vendor stderr is drained in bounded memory, reduced to
fixed failure categories, and never copied into Basaltwater logs. Recognized
terminal failures (`refresh_token_expired`, `refresh_token_reused`,
`refresh_token_invalidated`, `invalid_grant`) are not retried. Setup shows the
sanitized maintenance output directly. If no usable replacement was supplied
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

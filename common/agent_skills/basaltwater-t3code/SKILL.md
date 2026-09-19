---
name: basaltwater-t3code
description: Operate and troubleshoot the managed T3 Code server, pairing flow, and server-side Git environment on a Basaltwater VM.
metadata:
  managed-by: basaltwater
---

# Managed T3 Code

Use this skill for the T3 Code server installed by Basaltwater.

## Readiness

Start with:

```bash
basaltw agent doctor --capability t3code --capability host --json
```

Treat T3 service readiness and host-pressure warnings as separate results. Use
the `basaltwater-vm-triage` skill for host pressure or a support snapshot.

The server is the upstream-managed per-user service. Inspect it without sudo:

```bash
systemctl --user status t3code.service
journalctl --user -u t3code.service -n 100 --no-pager
tail -n 100 ~/.t3/userdata/logs/boot-service.log
```

The upstream unit is `~/.config/systemd/user/t3code.service`.
Basaltwater keeps networking and workspace settings in
`~/.config/systemd/user/t3code.service.d/basaltwater.conf`.

## Interpret logs in context

T3 may health-check optional agent executables that were not selected during
setup. A warning such as `Claude Agent CLI health check failed` is expected when
Claude is intentionally absent; use the doctor inventory's `required` and
capability results rather than installing an unrequested agent to silence it.

T3 also polls pull-request status for open repositories. A
`SourceControlProviderError` with `provider: unknown` is expected for an
intentional local-only repository with no remote. Confirm the repository has no
remote and ordinary Git operations work. Do not invent a remote or change its
branch solely to suppress PR-only UI noise. If the repository should use
GitHub, repair its owning remote configuration instead.

Basaltwater bounds numbered T3 log rotations through user-cache maintenance;
the host doctor reports total T3 log use and warns when it crosses the managed
threshold. Do not delete current log files while T3 is running. Treat a timeout
created by a deliberate preview wait as action evidence, not a service failure,
unless readiness or unrelated operations fail too.

## Updates

When T3 Code is selected, a Basaltwater setup rerun checks the upstream
service and updates it when a newer release is available. A healthy service is
restarted only when the runtime or managed configuration changes. Prefer the
connected client's explicit **Update server** action when an update should be
performed outside setup. For a host-side update, set `T3_RELEASE` to `latest`
or the exact version required by the client, then run:

```bash
T3_RELEASE=latest
T3_NPM_SHIM="$HOME/.local/share/basaltwater/t3-npm/bin"
env -u npm_config_dangerously_allow_all_scripts \
  -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
  -u npm_config_allow_scripts \
  -u NPM_CONFIG_ALLOW_SCRIPTS \
  PATH="$T3_NPM_SHIM:$PATH" \
  CC=gcc \
  CXX=g++ \
  npm_config_strict_allow_scripts=false \
  npm_config_foreground_scripts=true \
  npx --yes --package="t3@$T3_RELEASE" -c \
  'env -u npm_config_allow_scripts \
    -u NPM_CONFIG_ALLOW_SCRIPTS \
    -u npm_config_dangerously_allow_all_scripts \
    -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
    t3 service install'
basaltw agent doctor --capability t3code --fix
```

Keep those npm settings scoped to this trusted T3 update command. npm 12
rejects inherited `allow-scripts` and `dangerously-allow-all-scripts` settings
in T3's nested runtime. Basaltwater installs the referenced npm passthrough; it
recognizes only a versioned T3 install into an immutable `.staging-*` runtime,
creates a short-lived project policy allowing only `node-pty` and
`msgpackr-extract`, and removes it before publication. Other npm commands pass
through unchanged.

If the UI update or setup rerun rolled back with a native-module load error,
rerun the VM's Basaltwater setup. It repairs the retained candidate without
stopping the working active version; then retry **Update server**. The doctor
repairs and verifies the active runtime. `basaltw agent update` is not a
T3 updater; setup reruns update selected Codex, Claude Code, and OpenCode
installations as well, while the command remains available for an agent-only
update.
Do not start a second foreground T3 server on the managed port.

## Long-running work

Use the `basaltwater-agent-operations` skill for bounded maintenance holds and
redacted readiness records. Release a hold promptly after protected work.

## Browser previews

Use the browser skill installed for this VM: `basaltwater-browser-testing` when
managed Playwright is also provisioned, or
`basaltwater-t3-preview-testing` when T3 preview is the only browser surface.
Those skills account for the preview depending on the connected T3 application
remaining open.

An explicit preview `net::ERR_CERT_AUTHORITY_INVALID` is a connected-client
trust issue, not a T3 service failure. Certificate enrollment is optional: use
Playwright when available, or skip the affected browser operation and continue
with server checks. Offer the verified `basaltwater-web ca` enrollment URL and
fingerprint only when the user wants collaborative preview access restored.
Never weaken TLS or require client trust to complete unrelated work.

A healthy T3 doctor result does not exercise the connected desktop client's
preview-presentation bridge. If background preview navigation or DOM checks
work but `preview_open` produces no visible UI and snapshots keep failing, use
the installed browser skill's stale-preview workflow. Restarting the desktop
client may not clear VM-side tab state. Only after the user accepts interruption
of active T3 sessions, restart the managed server with
`systemctl --user restart t3code.service`, reconnect, and retry once. Do not
turn this deliberate recovery into an automatic setup or monitoring restart.

## Pairing

From the control system, request a one-time pairing URL:

```bash
basaltw agent web pair HOST USER
```

Use the full returned URL. A bare T3 URL showing a pairing-key form is expected.
When protected browser enrollment is enabled, use the HTTPS pairing endpoint
and Basic Auth credentials supplied by the operator.

## Git

GitHub authentication and Git operations happen on the server as the target
user:

```bash
gh auth status
git config --global --get user.name
git config --global --get user.email
git config --global --get init.defaultBranch
```

Keep repository remotes on HTTPS when GitHub CLI is the credential helper.
Never copy or print tokens from `~/.config/gh/hosts.yml`.

Basaltwater configures new repositories to use `main` unless the user already
selected another global default. An unborn repository has no branch ref until
its first commit: rename its symbolic branch with `git branch -m main`; do not
use `git branch main`, which requires an existing commit and fails in T3's
create-branch action. Create the initial commit before creating or switching to
additional branches.

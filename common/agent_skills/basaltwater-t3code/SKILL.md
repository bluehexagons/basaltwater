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

On Basaltwater-managed Debian services, startup tokens, pairing URLs, and QR
rows are filtered before they reach this log. The first service start after
filter installation also scrubs matching values from the prior log and its
numbered rotations. This filtering does not sanitize all application or provider
output; review and redact relevant log excerpts before sharing them.
Pairing commands still return a one-time URL directly to
the caller; do not paste that output into shared logs or support reports.

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

Basaltwater targets stable T3 releases. Setup reruns use npm's `t3@latest`
stable channel and reconcile the upstream service and selected terminal agents.
For a deliberate T3 update, prefer the connected client's **Update server**
action. Read [the update procedure](references/updates.md) before a host-side
update, launcher migration, or native-module repair. It distinguishes
standalone archives from older npm runtimes and keeps npm policy scoped.
Stable `0.1.0` introduces orchestration V2. Read the
[thread-migration procedure](references/thread-migration.md) before the first
upgrade: arrange the interruption, preserve a complete private recovery copy,
and update the client too. A `0.0.45` client cannot use the new orchestration
protocol. Do not modify migration state or downgrade to resolve a mismatch.
Setup allows up to 30 minutes for migration readiness, prints progress, and
avoids restarting a runtime that the updater just started. Inspect the boot
log before interrupting a slow migration or retrying a failed startup.

## Current workflow

After setup refreshes managed skills, or after changing plugins or MCP servers,
use **Restart agent session** in T3's command palette (`Ctrl+K` on Linux/Windows,
`Cmd+K` on macOS) once the current turn is finished. The next message resumes
the conversation with a new provider process and reloads its skills, plugins,
and MCP servers. This is a per-thread action; reloading agent configuration does
not require restarting the whole T3 service.

Use the **Update all** control at the bottom of T3's sidebar for a requested
bulk update across connected environments. It updates outdated providers,
servers, and the desktop app. For one provider on one VM, use that provider's
individual control in **Settings → Providers** or
`basaltw agent update HOST USER --tool TOOL`. After a provider update, record
readiness with:

```bash
basaltw agent doctor --tool TOOL \
  --capability t3code --capability host --record
```
Basaltwater's recorded readiness and rollback history belong to updates made
through Basaltwater; a T3 update does not create those records automatically.

**No project** threads keep generated files in individual folders under
`~/.t3/scratch` on the managed Debian service. Deleting a thread retains its
folder. These are user files, not a disposable cache; the host doctor reports
their total size and cache maintenance leaves them alone. Use a real project
and a managed worktree for changes intended for a repository. Do not move T3's
data directory or relax Codex policy to enable projectless work.

When this session exposes `link_pull_request`, register each PR created or
worked on using its full URL, including every layer of a stack. Do this when
the PR is created or work starts; before finishing, use
`list_thread_pull_requests` and register any missing PR from this task. Native
PR links give T3's linked-PR panel the associations that CLI Git operations
alone do not supply.

## Long-running work

Use `basaltwater-agent-tasks` for scheduled follow-ups and bounded goals.
The task skill documents the available controls, handoff fields, and workspace
choices. Stable scheduled runs use independent Codex context; they do not
inherit this T3 thread's tools or browser. Native T3 thread execution is not
part of the current stable workflow.

Use the `basaltwater-agent-operations` skill for bounded maintenance holds and
redacted readiness records. Holds are shared across this account's tasks;
release one you created after protected work only when no other task depends
on it. An unattended inspection must not create or release holds.

## Browser previews

Use the installed browser skill: `basaltwater-browser-testing` when managed
Playwright is provisioned, or `basaltwater-t3-preview-testing` when T3 preview
is the only browser surface. Follow its connectivity, trust, and recovery
boundaries.

An explicit preview `net::ERR_CERT_AUTHORITY_INVALID` is a connected-client
trust issue, not a T3 service failure. Certificate enrollment is optional.
Never weaken TLS or require client trust to complete unrelated work. Restarting
the managed server interrupts active T3 sessions; use the browser skill's
stale-preview procedure and obtain the owner's agreement before that recovery.

## Pairing

From the control system, request a one-time pairing URL:

```bash
basaltw agent web pair HOST USER
```

Use the full returned URL. A bare T3 URL showing a pairing-key form is expected.
When protected browser enrollment is enabled, use the HTTPS pairing endpoint
and Basic Auth credentials supplied by the operator.
That endpoint automatically issues a one-time browser credential and opens
T3; `/devices` retains manual pairing and T3 Connect controls. Use the primary
T3 URL after pairing to reuse the native browser session. If a previously paired
browser returns to the key form, check hostname/scheme changes and cookie
acceptance before attributing it to an update. Reopen the protected entry to
recover a rejected session. Never print cookies or pairing credentials while
diagnosing the transition.

A fresh, unpaired browser can log WebSocket authentication failures while
showing the pairing form. T3 v0.0.45 can also report a Clerk production-key
domain error on a private HTTPS origin; that identifies rejected cloud-account
requests. Check the failing origin and native pairing separately. It does not
by itself establish a gateway, TLS, or local pairing failure. Keep cloud sign-in
unverified when those requests fail; preserve the managed TLS/authentication
policy and upstream assets.

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

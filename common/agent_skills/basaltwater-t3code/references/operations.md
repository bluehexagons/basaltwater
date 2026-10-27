# Managed T3 service, pairing, and Git

Use the section relevant to the failing readiness check; do not collect every
log by default.

## Service and logs

For bounded redacted evidence, choose a new output file in an existing private
directory and keep event bodies in the artifact:

```bash
basaltw agent diagnostics --service t3code.service --window 1h \
  --summary --output /absolute/private/new-evidence.json
```

The summary retains collection issues and event counts, not service-health
proof. Inspect relevant artifact events when needed. If collection or saving
fails, read the issues and exit status; a save failure keeps events on stdout.

For missing readiness evidence or startup details, inspect the per-user service
without sudo:

```bash
systemctl --user status t3code.service
journalctl --user -u t3code.service -n 100 --no-pager
tail -n 100 ~/.t3/userdata/logs/boot-service.log
```

The upstream unit is `~/.config/systemd/user/t3code.service`;
`~/.config/systemd/user/t3code.service.d/basaltwater.conf` holds Basaltwater's
networking/workspace settings. On managed Debian services, startup tokens,
pairing URLs, and QR rows are filtered before logging. The first start with
the filter also scrubs those values from previous numbered rotations. Other
application/provider output is not fully sanitized: review and redact excerpts
before sharing. Pairing commands return credentials directly; keep their output
private.

Interpret warnings against the doctor inventory:

- Optional unselected executables may fail health checks. Do not install an
  unrequested provider to silence a warning.
- `SourceControlProviderError` with `provider: unknown` can be expected for an
  intentional local-only repository. Confirm no remote and working ordinary
  Git operations. Repair a remote only when the project should have one.
- A deliberate preview wait timeout is action evidence; it does not establish
  a service failure without other failed operations/readiness.

User-cache maintenance bounds numbered log rotations; host doctor reports log
usage. Do not delete current logs while T3 runs. For slow startup, inspect the
boot log before retrying; migration readiness can take 30 minutes. Follow
[updates](updates.md) for recovery without starting a second server or editing
migration state.

## Provider updates and records

Use **Update all** at the sidebar bottom for requested bulk updates across
connected environments. For one provider, use **Settings → Providers** or
`basaltw agent update HOST USER --tool TOOL`. Record readiness afterwards:

```bash
basaltw agent doctor --tool codex --capability t3code --capability host --record
```

Basaltwater's readiness/rollback history belongs to updates through
Basaltwater; T3 updates do not create these records automatically.

## Attention alerts

For long-running work, the owner can opt into **Thread notifications** and
separate **In-app notifications** in **Settings → General → Notifications**.
Both default off. System alerts/sound cover completion, failure, input, and
approval on that device while T3 is open; focused-app toasts have their own
switch. Browser system alerts need HTTPS and notification permission, or the
desktop app. These are attention aids, not task wakeups, retained receipts, or
a guarantee of background push delivery. Do not change the owner's notification
preferences as a side effect of scheduling. See the
[notification controls](https://github.com/pingdotgg/t3code/blob/v0.1.3/apps/web/src/components/settings/NotificationSettings.tsx).

## Pairing and browser sessions

From the control system, request a one-time URL:

```bash
basaltw agent web pair HOST USER
```

Use the full returned URL; a bare URL showing a pairing-key form is expected.
Protected browser enrollment uses the HTTPS pairing endpoint and operator's
Basic Auth credentials. It automatically issues a one-time browser credential
and opens T3; `/devices` retains manual pairing and T3 Connect controls.
Use the primary T3 URL afterwards to reuse the native session.

If pairing returns to the key form, check hostname/scheme changes and cookie
acceptance. Reopen the protected entry to recover a rejected session. Never
print cookies or credentials. An unpaired browser may log WebSocket auth
failures while showing the form.

Older v0.0.45 clients may report a Clerk production-key domain error on private
HTTPS origins. Identify the failing origin and check native pairing separately;
rejected cloud requests do not establish a gateway, TLS, or local pairing
failure. Leave cloud sign-in unverified when it fails. Preserve managed
TLS/authentication policy and upstream assets.

## Outside agents

An independent agent can deliberately connect through T3's OAuth MCP endpoint
at the environment's `/mcp` route. Copy the URL from **Settings → Environments**
and approve only a sign-in you initiated, with the access level the task needs.
Use HTTPS or host-local loopback for sign-in; a cloud-hosted agent needs an
internet-reachable route such as T3 Connect. A private LAN/tailnet URL alone does
not supply that reachability. Revoke an unused grant through **Sharing & access**.

This is separate from T3's injected thread tools and Basaltwater's task MCP.
Installation does not connect terminal/panel agents automatically. External
access does not make them delegated children of this thread or transfer its
browser attachment. Do not register an outside agent or enable public access as
an incidental repair. Use the [tagged outside-agent guide](https://github.com/pingdotgg/t3code/blob/v0.1.3/docs/user/outside-agents.md)
when that integration is requested.

## Server-side Git

Inspect authentication and identity as the target user:

```bash
gh auth status
git config --global --get user.name
git config --global --get user.email
git config --global --get init.defaultBranch
```

Use HTTPS remotes with GitHub CLI's credential helper. Never print or copy
tokens from `~/.config/gh/hosts.yml`.

Basaltwater defaults new repositories to `main` while preserving a user's
chosen global default. Before the first commit, rename the symbolic branch
with `git branch -m main`; `git branch main` needs an existing commit. Create
the first commit before creating/switching to additional branches.

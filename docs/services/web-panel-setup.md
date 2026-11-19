# Install, sign in, and manage access

[Documentation index](web-panel.md)

## Install and sign in

HTTPS is recommended:

Replace `REPLACE_WITH_A_UNIQUE_PASSWORD` with a unique secret before running
either command; the placeholder is not a usable password. Command-line
passwords can appear in shell history and to local process inspectors.

```bash
basaltw setup agent_vm 192.168.1.50 agent --codex \
  --web-panel \
  --web-panel-password 'REPLACE_WITH_A_UNIQUE_PASSWORD' \
  --ssl
```

The default port is 80 for HTTP or 443 with `--ssl`. Use another port when
needed:

```bash
basaltw patch 192.168.1.50 agent \
  --web-panel 9443 \
  --web-panel-password 'REPLACE_WITH_A_UNIQUE_PASSWORD' \
  --ssl
```

Setup prints the panel URL. The Basic Auth username is the setup username. The
password is hashed before upload and is not saved or reconstructed. Repeat
`--web-panel-password` to rotate it; omit the flag on a later patch to retain
it. If the setup username changes, supply a new password. This login also
authorizes integrated [privilege approvals](../agents/privilege-approvals.md).
Keep it out of agent-controlled browsers and tools.

Use HTTP only over localhost or in a controlled test environment. HTTP sends
login credentials and any integrated approvals without encryption. The HTTP
listener can be reachable from the network, so check its effective firewall
rules before relying on local-only access. Use `--ssl` over shared or untrusted
networks. With `--ssl`, the panel uses a suitable existing certificate or the
managed VM CA. Enroll that CA on your client when required. See
[Client CA trust](client-ca-trust.md).

Setup checks ownership of both the panel Nginx site and its enabled link before
replacing the site. An unrelated file, directory or symlink at either managed
path requires operator reconciliation; setup leaves it in place. Publication,
link creation and validation failures restore the previous site or remove the
new site and link created by that attempt. A failed reload also attempts to
reload the restored configuration. Inspect `nginx -t` and actual routing after
a reload failure: a timed-out reload or failed recovery can leave loaded workers
and files out of sync. This recovery covers the panel route, not earlier setup
steps, and does not provide a durable Nginx transaction snapshot.

Panel service setup retains unit definitions and activation state through the
HTTP socket readiness check. A failed check restores the previous unit and its
enablement/running state. If rollback cannot stop or disable the service, it
retains the candidate, private originals and a blocking unit operation marker;
follow [transaction recovery](../operations/transaction-recovery.md) before a
retry. This unit boundary does not restore the manifest, credentials or earlier
setup steps.

Audit and Proxmox inventory exporter setup use the same durable unit recovery.
Each stops its timer before publication, refreshes the snapshot through an idle
oneshot and rearms the timer after that job succeeds. A collection already
running is left alone; setup does not wait for or restart it. Failures restore previous unit files and
timer state where safe. A refused or uncertain required stop retains definitions,
private snapshots and the recovery guard. Follow transaction recovery before
retrying; already written snapshot data and snapshot-directory ownership changes
are not rolled back. This also applies to the first Proxmox inventory collection:
its timer is armed only after an idle collector succeeds, while an existing
collection can finish independently.

## Browser desktop

Add `--guacamole` to an HTTPS panel setup to install an embedded browser desktop.
It enables the shared desktop without opening RDP ingress. Open **Desktop
(Guacamole)** under **Services**, enter the setup account's Unix password, and
connect to observe. On agent systems, the desktop UI offers **Enable agents**,
**Disable agents**, and **Take control** with a 15-minute default, duration
choice, or indefinite override. Timed expiry returns to enabled because the
human chose that duration. Closing the browser, disconnecting, or restarting
the backend does not change remembered state. Other systems show ordinary
desktop controls. The panel password and Unix password remain separate. See
[persistent desktop control](../workstations/desktop-control.md) for state
transitions and approval rules.
Use `--no-guacamole` to remove browser access while retaining the desktop.
The same installation provides **Console**, a separate Unix shell with phone
keyboard controls, text sizes and terminal resizing. It uses the Unix password
through a private OpenSSH Unix socket, leaving public SSH authentication policy
unchanged. Connecting to the console does not start or pause the desktop.
It closes on disconnect or expiry; use `tmux` for persistent work.
See [Guacamole browser desktop](../workstations/guacamole.md) for installation, limits,
security boundaries and recovery.

## Git and credential settings

Open **Git & credentials** to set the setup account's commit name and email.
Changes apply immediately to its global Git config; repository overrides still
take precedence. New agent VMs receive missing author fields from the controller
even when GitHub credentials are not copied. Use `--git-name` and `--git-email`
on setup or patch to save an explicit per-VM override on the controller, or
`--git-identity none` to disable automatic identity seeding. Explicit controller
overrides are reapplied on reruns, so update them too after changing a VM's
identity in the panel.

On an HTTPS panel, paste a classic or fine-grained GitHub token into **New GitHub
access token**, then select **Save or replace token**. The panel privately replaces
this account's `github.com` entry and configures the GitHub CLI credential helper
for Git HTTPS access. Other GitHub host entries remain intact. Tokens are never
prefilled or shown in responses; failed submissions require re-entry. Select the
repositories and provider-side permissions the VM needs; saving a token confirms
storage, not provider acceptance. Test the intended repository separately.

**Remove stored GitHub access** removes the `github.com` file entry after its
checkbox is selected. It does not revoke the provider token or remove separate
keyring/environment credentials. Revoke the token at GitHub when appropriate.
HTTP panels keep token controls disabled; use an HTTPS setup or the existing
SSH credential-import command. Git identity editing remains available.

The page edits only the non-root setup account running the panel, with owned
files and safe paths. Root-profile panels show controller guidance because
their dedicated service account has no user home. Agent subscription sign-in
continues through the existing target-owned device login flow, linked from this
page and documented in [Agent authentication](../agents/agent-authentication.md).

## Privilege approvals

Installing the web panel includes the approval engine by default, with no
additional password or public listener. **Admin controls → Requests**
also lists this account's recent broker requests, including agent follow-ups,
service restarts and commands. Review status, expiry and safe execution results,
cancel unclaimed requests, or open the exact action to approve or deny it using
the panel login. **Review all requests** opens the full queue.
Use `--no-privilege-broker` to disable the engine and `--privilege-broker` to
re-enable it. Patches preserve an explicit opt-out. Existing installations gain
the default on setup or refresh. This preserves the setup account's sudo policy.

For a minimal VM, `--approval-panel [PORT] --approval-panel-password` installs
an independent HTTPS panel without the dashboard or Nginx. Its password remains
separate even when both surfaces are installed. `--no-approval-panel` removes
only that listener and credential. Use [Privilege approvals](../agents/privilege-approvals.md) for the
agent and user workflow.

## Admin controls

Open **Admin controls** for host updates and power actions. Host actions require
the [privilege approval service](../agents/privilege-approvals.md), included by
default on supported panel installations, including root-profile panels using
their dedicated non-root service account. With approvals disabled, Admin controls
shows setup guidance and inspection links. Decisions run through the isolated
approval service; the ordinary panel process has no decision-socket access.
The panel login and browser origin are trusted to approve actions.
Agent tools, tasks and maintenance policy live under **Agents**.
Manage T3 Code updates in its own **Settings → Environments → Update server** control.

| Action | Behavior |
| --- | --- |
| Update system packages | Refresh Debian APT indexes, then upgrade installed packages; keep existing configuration files. Package hooks can restart services. No distribution upgrade, autoremove, or automatic reboot. |
| Check Basaltwater refresh | Validate `basaltw refresh --dry-run`; report whether the saved setup and source channel can be refreshed. |
| Refresh Basaltwater | Upgrade the installed managed source channel and replay the last successful local setup with `basaltw refresh`. This can reconcile configuration and restart services. |
| Check web configuration | Run `nginx -t` without reloading. |
| Reload web gateway | Run `nginx -t`, then reload Nginx only if validation passes. |
| Restart web panel | Restart the UI; independent task work continues. Older installations with panel-owned scheduling interrupt active prompts. |
| Restart host | Restart 5 seconds after scheduling by default. Choose **Warn users, then restart in 2 minutes** for a longer warning. All running sessions are interrupted. |
| Shut down host | Schedule power-off two minutes after approval; interrupt all running sessions. |
| Cancel scheduled power action | Cancel a still-pending scheduled reboot or power-off. |

Click anywhere on an enabled action row to open its focused approval review.
Read the effects, choose restart timing when applicable, then select **Approve**.
That explicit decision creates and approves one fixed action through the isolated
service. Opening the review creates no request and schedules nothing. The review
expires in five minutes; a consumed review cannot be submitted twice. The result
opens that request's status directly. The standalone panel also offers **Host
actions** with the same flow, using its own sign-in.
Both surfaces explain unavailable actions before offering approval. If only one
restart timing is supported, the review disables the other choice. The former
`/admin?action=…` and `/actions/admin` submission flow has been removed; host
actions now use the isolated review exclusively.

**Requests** contains pending decisions and recent outcomes. **Activity & diagnostics**
contains the latest maintenance result and inspection links. Refresh keeps the
selected view. Existing request links still open an individual review with
**Approve once** and **Deny**; technical metadata and untrusted explanations are
in a disclosure, while exact general-command arguments remain visible before approval.
Use **Cancel request** to withdraw an unclaimed request; cancellation cannot undo
an action already started. Approval expiry follows the installed broker policy.
Cancelling a scheduled power action is a new approval request: allow time to
approve it before the selected deadline, or use the administrator console. Five
seconds leaves little cancellation time; choose the two-minute warning when needed.
After power-off, starting the machine requires its VM controller or physical
access.

The request and activity views show recent broker requests across all operations
and the latest maintenance job's result, timestamps, and exit code. A request's
**View latest maintenance activity** link opens that latest job, which may be a
different request. **Dispatched** means the
maintenance service started, not that its commands finished. A successful power
job confirms scheduling only. Review running work before updates or interruptions.

Maintenance runs in a separate root system service with a six-hour limit,
so restarting the panel or approval broker does not cancel it. One maintenance
job runs at a time; jobs and uncertain dispatches are never retried automatically.
Use **Refresh status** to inspect results. The latest job result and request
history survive panel restarts. Privileged command output is not exposed; use
administrator SSH or console access for detailed diagnosis.

Refresh reuses the installed source and saved successful Debian setup, including
controller-installed snapshots. It automatically prepares a managed checkout for
a snapshot and preserves local state and staged deployment data. Snapshot updates
follow the recorded branch, or `main` when no branch was recorded. The previous
source is retained for recovery. Writable or unidentified source trees are
rejected. The refresh check validates local state without fetching;
it does not preview upstream code changes. No credentials are copied and no
interactive login is started by refresh. Saved coding-agent selections are
retained even if profile defaults change, including a selection with no agents.
Legacy records without a saved selection still use profile defaults.
The screen explains unavailable actions rather than granting wider privileges.

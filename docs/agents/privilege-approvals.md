# Privilege approvals

The web panel installs the approval service by default. Open **Admin controls →
Requests → Review all requests** to review, approve or deny exact
actions using the panel login. Host administration, agent service prerequisites,
Samba changes and storage operations share this queue. Nothing runs just because
you open a review.

For common host maintenance, open **Admin controls → Host actions**, click the
action row, review its effects, then approve once. Restart defaults to five
seconds after scheduling, with an explicit two-minute warning option. These
focused reviews create no request until approval and expire after five minutes.
The standalone panel offers the same catalog through **Host actions**, with
availability reasons for actions blocked by policy or outstanding maintenance.

Use `--no-privilege-broker` to disable approvals, or `--privilege-broker` to
re-enable them on a panel installation. An omitted option preserves an explicit
opt-out on patches. Existing panels gain approvals on their next setup or refresh;
no additional password or public port is needed. Integrated approvals follow the
panel's HTTP/HTTPS configuration; use `--ssl` to protect the login and decisions.

For a lighter agent VM without the web panel, install the standalone HTTPS panel
below. It has its own password and service identity and requires neither Nginx
nor dashboard/agent-browser components. This mode removes reusable sudo access
from the coding account. Installing integrated approvals alone preserves the
account's existing sudo posture.

The agent can request an exact command and argument vector for one-time root
execution. The approval page shows every argument; shell strings, environment
assignments, stdin, and command prefixes are not accepted. VM reboot and exact
administrator-registered service restarts remain available as structured actions.

## Set up the standalone approval panel

Run this from the trusted controller:

```bash
basaltw patch 192.168.1.50 agent \
  --approval-panel \
  --approval-panel-password
```

The password flag prompts privately. Pick a 16–256 character password that is
different from the Linux, Git, T3, and web-panel passwords. Only a salted hash
is uploaded; saved configuration and reconstructed setup commands omit it.

Supplying `--approval-panel-password` or its legacy alias
`--privilege-broker-password` also selects the standalone panel on port 9444
when no standalone port is specified. This works with `--web-panel` and
`--privilege-broker` too. A password-only patch keeps an existing standalone
port; an explicit `--approval-panel PORT` overrides it. A dry run selects the
same listener without prompting for a password. The independent password
protects the standalone page; approvals inside the main web panel continue
to use the main panel login.

The approval page uses the managed VM host and HTTPS port 9444. If setup uses an
IPv4 address with a CIDR prefix, the approval origin uses that address and drops
the prefix. Use
`--approval-panel PORT` to choose another port above 1023, outside the shared
gateway's 8443–8999 range. Restrict the VM with `--lan-access` or
`--access-source`, and enroll the VM CA on the device that will approve
requests; see [Client CA trust](../services/client-ca-trust.md).

The standalone panel is available on VM `agent_vm`, `agent_code_vm`, `agent_workstation`
and `server_github_runner` setups. Runner hosts grant request access to the
non-root setup/panel operator, never the dedicated build account. It cannot coexist with `--nopasswd`,
`--harden-agent`, or `--harden-user`. On an existing VM, end old coding-user
sessions after setup so they cannot retain removed group memberships.

## Request and approve an action

The agent runs one of these commands:

```bash
basaltw agent privilege request system.reboot \
  --reason "Apply the installed kernel update" --json

basaltw agent privilege request service.restart --unit example.service \
  --reason "Restart the reviewed service" --json

basaltw agent privilege wait REQUEST_ID --timeout 3600 --json

# Request an exact command, without a shell or a leading sudo.
basaltw agent privilege request command.run \
  --reason "Refresh package metadata" --command /usr/bin/apt-get update
```

The request returns a `review_url`. Open it on your own device, sign in with
the main panel login or the standalone panel's separate password, check the machine identity, requester UID,
parameters, effects, and expiry, then choose **Approve once** or **Deny**.
Treat the agent's explanation as untrusted. The root broker performs one
approved attempt automatically.

New installations use a one-hour review window. Administrators can select
30 seconds to 24 hours through `ttl_seconds` in the root-owned policy. Setup
reruns preserve an existing window, including an older five-minute value; change
that policy explicitly to extend an existing installation. The CLI's wait
timeout only controls observation and does not extend the review deadline.
A changed policy invalidates a request; an interrupted execution is marked
uncertain and is never retried.
Check status before submitting a replacement request. A reboot reported as
`dispatched` is not proof that the VM returned.

For a refresh or another long Admin-screen job, inspect existing work without
requesting an action:

```bash
basaltw agent privilege admin-status --json
```

This returns owned request IDs, maintenance unit state, and the latest job's
fixed stage, command index, failure category and correlated setup context.
It returns nonzero for a failed/interrupted latest job or unavailable status.
Missing fields from older brokers remain unknown. A source activation receipt
does not prove setup success or that services loaded that revision. Preserve
recovery evidence and diagnose before another approved refresh; this command
never replays, approves, cancels or stops work.

Approved commands run in a separate supervised root service with host
filesystem, device and network access. The broker's own filesystem sandbox
remains enabled. Review every argument and any referenced executable/configuration
before approval. Each command has a 60-second runtime limit and its process
group is stopped when the unit finishes. Status and the review page expose
safe exit/failure metadata; privileged stdout and stderr remain discarded.
The command unit has a 1 GiB memory limit and a 128-task limit; use the fixed
administration workflow for long maintenance jobs.

When an action is no longer needed, withdraw your own request without an
operator approval:

```bash
basaltw agent privilege cancel REQUEST_ID --json
```

Cancellation prevents a pending or approved request from executing if the
worker has not claimed it yet. It is recorded as `cancelled` in the audit
history and cannot be approved again. Repeating cancellation is safe; already
expired, denied, or invalidated requests retain their existing state. The
command rejects a request whose execution was claimed, including one that has
finished; it cannot stop a running action or undo its effects. `wait` exits
unsuccessfully for a cancelled action, while `cancel` succeeds for a cancelled
or already closed, unexecuted request. Setup must refresh both the CLI and the
broker before using this command on an existing VM.

When the [web panel](../services/web-panel.md) is installed, **Admin
controls → Requests** lists this account's recent requests from task
follow-ups, terminal agents and Admin controls. It shows status, approval expiry,
safe execution results, cancellation for unclaimed work, and links to review
the exact actions. Both panels list pending, approved and executing requests
before recent closed requests, so completed work cannot hide an active review.
Use **Refresh status** after an action finishes. Command
arguments and agent explanations appear on the exact-action review page.
Follow-up restart prerequisites use registered service rules when available.
Otherwise they request one-time command approval for the exact `systemctl`
restart arguments; the portal still requires your decision and no service
allowlist entry is created. The panel verifies the successful receipt's exact
operation and arguments before launching the task.
Main-panel decisions pass through an isolated approval service; the ordinary
panel process has no decision-socket access. The panel login and browser origin
are trusted to approve actions. Keep that login out of agent-controlled browsers
and tools. The standalone panel retains its independent credential and origin,
including when both panels are installed. Request links prefer the main panel
when it is present; either review surface can decide the same pending request.

The panel’s [Shares & Data page](../services/shares-data.md) also requests
individually approved `data.run` operations for single-file verification,
repair, restoration and explicit acceptance. Review the exact file, configured
scrub root, parity directory and any independent copy. These run in a separate
worker with a six-hour deadline and a storage lock. `dispatched` reports launch;
the page shows the worker’s result separately. A changed finding or file requires
verification and a fresh review. This operation does not grant general file
editing or automatic remediation.

## Rotate or remove it

```bash
# Prompt for and set a new approval password.
basaltw patch 192.168.1.50 agent --approval-panel-password

# Remove only the standalone listener and its credential.
basaltw patch 192.168.1.50 agent --no-approval-panel

# Stop the services and remove approval authority.
basaltw patch 192.168.1.50 agent --no-privilege-broker
```

Omitting these flags preserves the feature and current standalone password.
`--privilege-broker-password` remains an alias for `--approval-panel-password`.
Removing the standalone panel retains integrated approvals when the main panel
is installed; removing the main panel retains an explicitly selected standalone
panel. Removing the last surface stops the approval engine. Rotation
expires outstanding approvals. Disabling removes the managed services,
credentials, and polkit rule but retains policy and audit history for review.

## Administrator reference

Administrators who register restartable services, inspect audit records, or
need the detailed security contract should use the [privilege broker
reference](privilege-broker-reference.md). Keep root SSH as the recovery
channel; the approval page has no password-recovery flow.

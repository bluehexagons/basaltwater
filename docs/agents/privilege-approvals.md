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

## Request software for a Debian agent

For a recurring tool, request a saved setup flag when available. Use a saved
Debian package for other tools. The broker supports the package-only setup
flags `jupyterlab`, `av-tools` and `gl-tools` on Debian agent profiles.
Run these commands on the agent.

```bash
basaltw agent software plan --flag jupyterlab --json
basaltw agent software request --flag jupyterlab --reason "Run notebooks for recurring data analysis" --json
basaltw agent software request --apt shellcheck --reason "Lint deployment scripts on this host" --review-window 2d --json
basaltw agent privilege wait REQUEST_ID --until decision --timeout 2d --json
```

The request returns its ID and review URL immediately after planning. Pass the
URL to the operator and continue independent work. The approval shows the
target selection, upgrades, download size, disk change and a digest. The plan
command lists every changed package, version, source and archive hash. APT uses
the configured, trusted Debian indexes. A changed transaction requires fresh
review. Approved packages can run maintainer scripts and start services.
Software installs default to a one-hour execution budget, capped by policy.
Use `--runtime 30m` or `--runtime 2h` to request a different budget.

On the controller with the host's saved setup and enrolled root SSH identity,
run the following once to collect current approvals. Add `--watch` to keep
collecting them as they arrive. The watcher checks every 15 seconds and backs
off on SSH outages. Use the workspace that owns the host's rebuild record.

```bash
basaltw agent software sync vm.example --watch
```

Saved requests wait in `awaiting_controller` until synchronization or expiry.
The controller merges just the approved selection into the current setup,
then releases the installation. Rerunning sync after an interrupted save is
safe. Use `agent privilege status REQUEST_ID --json` to check
`saved_for_rebuild` and `applied` separately. On installation failure the
controller retains the approved declaration. Inspect the package manager and
target receipt before requesting further changes. A rebuild uses versions
available from its configured repositories at rebuild time.

For a host package needed only for one task, add `--temporary` to the request.
If it is already installed, the command reports `already_installed`. Installing
a missing package requires approval and can install dependencies. It skips the controller
and leaves the installed packages on the running host until removed or rebuilt.

Use `basaltw agent privilege list --json` to recover a lost ID or check a
submission whose reply was interrupted. It lists up to 20 of the requesting
account's active and recent requests, with active requests first.

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

The standalone panel is available on VM `agent_vm_lite`, `agent_vm`, `agent_code_vm`, `agent_workstation`
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

New installations give the operator 24 hours to decide. A request can use
`--review-window 2d` and a command can use `--runtime 30m`. Put both options
before `--command`. Seconds and the suffixes `s`, `m`, `h` and `d` are accepted.
The administrator controls the default with `ttl_seconds` and the ceiling with
`max_review_seconds` (seven days on new installs). An older policy without
a ceiling uses its existing review window as the ceiling.

Submission returns immediately. Keep the request ID and continue independent
work. Run `basaltw agent privilege wait REQUEST_ID --until decision --timeout 2d --json`
as a background command to receive the decision, or omit `--until decision` to
wait for the execution result. Brief connection failures are retried within
the local budget. A wait that expires returns exit 124 and the same ID, ready
to reattach. The review deadline stays in effect. A changed policy invalidates
a request. An interrupted execution is marked uncertain and requires inspection.
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
before approval. The default command budget is 15 minutes. The requested
runtime can range from one second to six hours within policy. Its process
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

Integrated approval pages use the main panel's navigation, including configured
agent and publishing tools. Setup publishes these non-secret selections in the
panel manifest so the isolated service does not need access to the operator's
home directory. Rerun setup to publish missing selections on older installations.

The panel’s [Shares & Data recovery controls](../services/shares-data-findings.md)
submit reviewed scrub actions to the [local recovery service](privilege-broker-panel-and-storage.md#shares--data-recovery-operations).
The broker retains its decision rules for existing `data.run` requests.
Share, Samba account and routine changes continue to use broker approval.

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

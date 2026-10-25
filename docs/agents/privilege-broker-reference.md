# Privilege broker reference

This reference describes the administrator-owned policy and implementation
boundaries for [privilege approvals](privilege-approvals.md). It is for people
who administer the VM, not agents requesting an action.

## Policy and allowlists

Root owns `/etc/basaltwater/privilege-broker/policy.json`. A new policy has an
empty `services` mapping and `"reboot": "approve"`. It accepts only these
rules:

```json
{
  "services": {
    "example.service": "approve",
    "reviewed-worker.service": "allow"
  },
  "reboot": "approve",
  "commands": "approve",
  "ttl_seconds": 3600
}
```

This fragment describes the mutable values; retain the installation's existing
machine identity and requester UID. `approve` needs a browser decision for
every request. `allow` executes automatically but is still audited.
Unregistered services are denied. Reboot accepts only `approve` or `deny`.
The schema permits at most 32 exact `.service` names and an expiry of 30–86,400
seconds. New installations default to 3,600 seconds; setup reruns preserve the
administrator's existing value. Commands always require individual approval.
The broker reads policy for every request and before execution, so a
change invalidates an outstanding request.

Keep the non-secret policy root-owned with mode `0644`: the dedicated approval
account also reads it at startup. Setup reconciles `/etc/basaltwater` and its
`privilege-broker` directory to root ownership and mode `0755`, including on
reruns after an installation with a restrictive umask. These directories must
permit the approval account to traverse to the policy. An atomic policy rewrite
must explicitly retain mode `0644`; a writer's private default of `0600` prevents
the HTTPS service from starting. Keep `auth.json`, `server.key`, and broker audit
state private. If the approval page refuses connections after a policy edit,
inspect the service through root SSH or the VM console and restore policy
readability and directory traversal; the page cannot approve its own recovery.

Registering a service grants every effect of restarting it. Audit its unit,
drop-ins, environment, executable, scripts, configuration, dependencies, and
lifecycle hooks first. Never register a root service that consumes
agent-writable code or configuration. The broker does not snapshot transitive
service inputs; avoid changing service definitions while requests are pending.
Never allowlist the broker or approval service themselves.

## Trust boundaries

The coding account has no sudoers grants, privileged supplementary groups, or
polkit authorization. The root broker accepts requests only from that account's
kernel-authenticated Unix-socket identity. The locked `basaltwater-approval` service
identity alone can use the separate decision socket. The approval page uses its
own HTTPS origin and Basic Auth record, has no cookies, and does not share the
web-panel credential.

Both sockets accept bounded messages with strict action fields. Execution uses
the reviewed exact argv (fixed `systemctl` argv for structured service/reboot
actions), a clean environment, no shell, and no privileged output
returned to the agent. Policy, code, credentials, and their ancestor paths must
be root-owned, non-symlinked, and not group/world writable.

The broker and HTTPS service retain their filesystem/device/home sandboxes.
An approved `command.run` dispatches a unique transient root unit through the
system manager and waits for it to stop. Its fixed, root-owned
`common/service_tools/privilege_command.py` helper receives only the reviewed
argv as bounded JSON on a private stdin pipe. User arguments never enter
systemd's command-line/environment substitution. The helper revalidates argv
and executes it with a clean environment and closed interactive stdin.

The transient unit deliberately has host filesystem, device and network access;
that authority is described in the reviewed effects. It has no restart, a
60-second command timeout, a 65-second unit runtime cap, a five-second stop
deadline and control-group termination. The broker waits up to 80 seconds;
the unit is limited to 1 GiB of memory and 128 tasks. A signalled systemd client or
a lost wait response stays uncertain and is never replayed. Child output is
discarded. `execution` metadata records only state, exit code and a fixed failure
category, including on the review page. Existing audit databases gain a nullable
execution column without losing requests or events.

The approval page checks the configured Host and Origin, uses per-request CSRF
tokens, escapes agent-supplied text, disables caching and framing, and has no
unauthenticated request feed. Login failures are globally limited to 20 per
minute. Keep access source-restricted: flooding can affect availability.
Approval origins currently support DNS names and IPv4 literals. Literal IPv6
origins are not supported by host validation; bracket formatting alone does not
qualify that transport contract.
Controller-to-target arguments carry the effective approval hostname, including
an explicit override. IPv4 CIDR setup targets such as `10.0.0.20/16` use the
address `10.0.0.20` for approvals; the prefix remains part of setup metadata.

## Audit, limits, and recovery

The root-only database is
`/var/lib/basaltwater-privilege-broker/requests.sqlite3`. Its `requests` and
`events` tables record decisions and outcomes. It is protected from the coding
account, but not tamper-proof against root. Execution claims are committed
before effects. Pending requests expire on restart; an in-progress action is
marked uncertain and never replayed.

The request socket also permits its authenticated owner to cancel pending or
approved requests. Cancellation shares the execution-claim lock and commits a
`cancelled` state and UID-attributed event before responding. It cannot cancel
another UID's request or an action already claimed by the worker. Cancelled
requests remain terminal across broker restarts and stale browser decisions;
cancellation does not erase audit records or reset the hourly request quota.

There can be eight outstanding requests and 30 requests per UID per hour. The
database capacity is bounded and fails closed. Archive it as root with the
services stopped if storage fills.

The managed units are `basaltwater-privilege-broker.service` (root) and
`basaltwater-privilege-approval.service` (locked `basaltwater-approval` account).
The web service receives its credentials through systemd `LoadCredential`.
Root SSH remains the setup and recovery path. The first version has no
passkeys, multi-user roles, push notifications, or browser password recovery.

Setup and disable query the manager for both unit names even if their on-disk
fragments are missing: loaded services can still hold credentials. Existing
fragments must pass the root ownership/path checks and carry the broker's
management marker. Disable requires both services to be observed inactive and
disabled before removing authentication and reporting success. Unknown states,
query errors, and failed stops fail closed and retain recovery evidence.

A rerun, re-enable, or password rotation deliberately stops and restarts both
services. This reloads systemd credentials and expires pending/approved requests;
executing requests become uncertain on broker startup and are never replayed.
The policy's machine identity and administrator choices, audit database, and
terminal decisions are retained. Rollback never restores a database snapshot or
replays an approval.

Broker setup owns a transaction through activation and the HTTPS authentication
challenge check. Before mutation it privately snapshots policy, authentication,
certificate/key, polkit rule, unit files, their modes and ownership, directory
metadata, and each unit's activation and persistent/runtime enablement. Failure
or a catchable interruption quiesces the replacement, restores those files and
metadata, reloads units, restores the prior activation/enablement, and verifies
the previous endpoint if it was active. A failed first installation returns to
stopped/disabled services and removes its partial broker files.

The stable lock and operation marker are under `/etc/systemd/system/`:
`.basaltwater-privilege-operation.json.lock` and
`.basaltwater-privilege-operation.json`. Recovery snapshots are in a root-only
`.basaltwater-privilege-*` directory there; its `previous.json` contains private
credentials and must never be pasted into logs or agent chat. Failed or
unverifiable restoration retains the marker and backup and blocks setup and
disable retries. Process death also leaves the guard in place. Recover through
root SSH or the console using the [transaction recovery guide](../operations/transaction-recovery.md):
inspect actual service/file state and the recorded backup, quiesce the group,
restore broker files and their metadata, reload units, then restore and verify
the recorded activation/enablement and HTTPS endpoint. Resolve the marker only
after recovery is verified; never delete the stable lock to bypass it.

This rollback covers broker-owned files and service state. Account creation or
locking and shared internal-web-host effects (packages, CA/utility/static-site
publication, and gateway configuration) remain outside this boundary and may
persist after failure. They run before broker quiescence so a failure there
leaves the previous broker endpoint running. The transaction validates directory
safety before prerequisites and refreshes its directory snapshot afterward,
before broker mutations. Rollback preserves a shared `/etc/basaltwater` parent
created by the gateway and its `internal-web` subtree, while removing directories
created by the broker. A safely rolled-back first installation permits a retry.

## Panel administration operations

The [Admin controls screen](../services/web-panel.md#admin-controls) requests `admin.run`
with only an `action` chosen from the installed catalog. It always requires
individual approval; reboot and shutdown also respect the existing `reboot`
deny rule. Review the fixed helper argv and the described effects. Actions can
update packages, replay saved setup, reload Nginx, restart the panel, or schedule
host power changes, so approving them can interrupt active work.

Long jobs are dispatched as the detached root
`basaltwater-admin-maintenance.service`, with a six-hour runtime cap and no
automatic restart. The broker records `dispatched`, not command completion.
The helper records its latest result in a private root-owned file under the
broker state directory. Its fixed commands use a clean environment, no shell,
and no interactive stdin. Root output is discarded. The shared transient unit
and file lock prevent concurrent maintenance; cancelling a pending broker
request does not stop an already dispatched job. Use administrator access to
investigate or stop that unit deliberately.

Sanitized job status includes a fixed stage (`preflight`, `source_verification`,
or `command`), one-based command index and fixed failure category. Each stage
is persisted before its effects. Categories distinguish unavailable actions,
rejected source, timeout, launch/validation errors, child failure/signal and
interrupted jobs. Exception text and output remain private. Older results keep
unknown stage/category values instead of inventing diagnostics; invalid values
make the result unavailable. Confirmed absence of the job-history file is shown
as no latest result; unsafe, corrupt, or inaccessible state is unavailable. The
helper validates the trusted state directory and checks the file without
following its final symlink. It reads the job record before observing the unit,
so an older inactive observation cannot mark a newly published running job as
interrupted. An inactive unit with a running record is reported as interrupted
and is never retried automatically.

The CLI's `agent privilege admin-status --json` and the panel show the fixed
diagnostics. Each helper invocation records a random `job_id` before effects;
refresh passes it only through its clean child environment. The target setup
marker retains that identity through steps, failure and explicit recovery.
Only a protected, schema-validated marker for that exact job can supply
`setup_context`: phase, pre-step/step stage, numeric step index/count and an
allowlisted error class. Step names, exception text, configuration and root
output are omitted. The helper persists safe context with its final result,
so a later marker cannot replace an older job's explanation. Process-loss
inspection can correlate a still-running record before marking it interrupted.
Legacy records without a job identity and unrelated/corrupt/missing markers
leave context unknown. New readers cannot reconstruct diagnostics omitted by
an older running broker; source files and loaded service code remain separate.

There can be only one pending/approved/executing administration request at a
time. A new `admin-status` requester message returns bounded owned request
metadata and sanitized job state; it cannot execute actions, decide requests, or
read command output. Its `privilege_requests` field lists up to 20 recent
requests owned by the requester, across all supported operations, with status,
approval expiry, a fixed operation/target summary, safe execution metadata and
the independently authenticated review URL. It omits command arguments,
agent explanations and approval digests, and trims the list to the transport
size limit. `restartable_services` names the registered structured restart
targets. Unregistered follow-up targets can use individual approval of the
exact restart argv through the existing `command.run` contract; their receipt
must match that exact operation and argument vector before launch. This does
not add a service rule or relax broker policy. `requests` retains
administration-only metadata for job gating and can also be trimmed to fit.
An older broker omits the new field; the panel links to the standalone portal
and reports the listing as unavailable. Neither panel installation nor these
read-only metadata fields change the standalone portal or approval identity.
The separate approval interface and existing request
quotas remain in force. A lost dispatch response remains uncertain, and a
broker restart never retries it. Root refresh additionally checks all installed
code and Git metadata for root ownership, symlinks, and writable permissions.

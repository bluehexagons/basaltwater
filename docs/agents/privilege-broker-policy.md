# Privilege broker policy and audit

This is [administrator guidance](privilege-broker-reference.md) for VM policy.
For requesting an action, see [privilege approvals](privilege-approvals.md).

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

In standalone approval-panel mode, the coding account has no sudoers grants,
privileged supplementary groups, or polkit authorization. Integrated main-panel
approvals preserve the setup account's existing sudo policy. Root-profile
panels use their dedicated non-root service UID as requester.
The root broker accepts requests only from the configured requester's
kernel-authenticated Unix-socket identity. The locked `basaltwater-approval` service
identity alone can use the separate decision socket. The standalone approval
panel uses its own HTTPS origin and Basic Auth record, has no cookies, and does
not share the web-panel credential.

Main-panel approvals are enabled by default. Nginx authenticates the ordinary
panel login and routes `/approvals`, `/approvals/admin`, fixed-action review paths
under `/approvals/admin/ACTION`, and exact `/requests/ID` paths to an HTTP
Unix socket owned by the isolated approval identity and proxy group. The
approval service checks the kernel peer UID against `www-data`, the single
forwarded login, and the configured panel Host/Origin. The requester account
has no decision-socket access and cannot authenticate a direct connection to
the review socket. Credentials are stripped before proxying. The main-panel
login, proxy and browser origin are trusted to authorize approvals: compromised
content on that origin could act through the signed-in browser. Deploy only
the standalone panel when an independent credential and origin are needed.
Integrated approvals follow panel transport; HTTPS is recommended.

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
minute on the standalone listener; integrated routes use the panel's Nginx
login throttling and fail2ban policy. Keep access source-restricted: flooding can affect availability.
Approval origins support DNS names, IPv4 literals, and bracketed IPv6 literals.
Authenticated forms with valid bounded framing are read completely before origin
rejection, so an unread body cannot reset the rejection response on the Unix
proxy connection. A truncated body cannot authorize an action.
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
The web service receives standalone credentials through systemd `LoadCredential`.
An integrated-only service has no standalone credential or TCP listener.
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

Broker setup owns a transaction through activation and the selected review
listeners' readiness checks. Before mutation it privately snapshots policy, authentication,
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
locking and shared certificate prerequisites (OpenSSL and CA/certificate files)
remain outside this boundary and may
persist after failure. They run before broker quiescence so a failure there
leaves the previous broker endpoint running. The transaction validates directory
safety before prerequisites and refreshes its directory snapshot afterward,
before broker mutations. Rollback preserves a shared `/etc/basaltwater` parent
created by prerequisites and its existing subtrees, while removing directories
created by the broker. A safely rolled-back first installation permits a retry.

## Refresh interrupted during source activation

For a controller-installed source snapshot, refresh publishes a private
operation marker beside the installed source before its first rename:
`<source-parent>/.basaltwater-source-refresh-operation.json` (normally
`/opt/.basaltwater-source-refresh-operation.json`). It records the exact
`repository_path`, `backup_path`, `staged_path`, `temporary_path`, and the
`runtime_data_names` that are being carried across. A completed activation
removes the marker after writing its bounded `.last.json` result. A caught
failure that cannot restore the original source keeps the marker with
`recovery_required`; process loss leaves it `in_progress`. The marker identifies
planned copies but does not prove which rename completed. These renames are not
one filesystem transaction, and this evidence does not claim atomic
power-loss recovery.

An unresolved marker blocks refresh and its dry-run check. First confirm that
the original maintenance process has exited and keep all recorded paths in
place. Inspect the record and each path as root:

```bash
sudo systemctl status basaltwater-admin-maintenance.service
sudo python3 -m json.tool /opt/.basaltwater-source-refresh-operation.json
sudo ls -lai -- /opt/basaltwater /opt/REPLACE_WITH_RECORDED_BACKUP_PATH /opt/REPLACE_WITH_RECORDED_STAGED_PATH
```

Use the exact paths from the marker if they differ from the normal `/opt`
layout. Check which copy has the old installed source, which has the downloaded
source, and where every name in `runtime_data_names` currently resides. Inspect
symlinks with `sudo readlink` and compare file identity, modes and contents
before moving anything. Preserve the marker and all copies while deciding
whether to restore the old source or finish activation. Never rerun refresh or
blindly replay a rename while this marker exists. If a path or runtime entry is
missing or duplicated and its owner is unclear, keep the evidence in place and
continue recovery from administrator SSH or the VM console.

After restoring or activating a complete source tree at `/opt/basaltwater`,
verify its code, retained runtime entries, ownership and modes. Then resolve
only the inspected operation using that verified source:

```bash
sudo /usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/basaltwater")
from lib.operation_state import OperationStateStore

marker = "/opt/.basaltwater-source-refresh-operation.json"
expected_operation_id = "REPLACE_WITH_RECORDED_OPERATION_ID"
store = OperationStateStore(marker)
record = store.load()
if (record is None or record.operation_type != "source_refresh"
        or record.operation_id != expected_operation_id):
    raise SystemExit("Expected source refresh recovery marker is missing or has changed")
outcome = "rolled_back"  # Use "succeeded" only after verifying completed activation.
store.complete(record.operation_id, outcome=outcome)
PY
```

Keep the retained backup until a later successful refresh and setup are
verified. A separate target setup marker at
`/opt/basaltwater/state/setup-operation.json` also blocks refresh when it is
still `in_progress`, preserving its original job correlation for inspection.
Use the [transaction recovery guide](../operations/transaction-recovery.md)
before resolving such a hard-killed setup. A matching
`recovery_required` setup remains eligible for its idempotent setup replay;
refresh refuses a profile, machine type or username mismatch before changing
source.

# Privilege broker reference

This reference describes the administrator-owned policy and implementation
boundaries for [privilege approvals](PRIVILEGE_APPROVALS.md). It is for people
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
  "reboot": "approve"
}
```

This fragment describes the mutable values; retain the installation's existing
machine identity and requester UID. `approve` needs a browser decision for
every request. `allow` executes automatically but is still audited.
Unregistered services are denied. Reboot accepts only `approve` or `deny`.
The schema permits at most 32 exact `.service` names and an expiry of 30–900
seconds. The broker reads policy for every request and before execution, so a
change invalidates an outstanding request.

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
fixed `systemctl` argv, a clean environment, no shell, and no privileged output
returned to the agent. Policy, code, credentials, and their ancestor paths must
be root-owned, non-symlinked, and not group/world writable.

The approval page checks the configured Host and Origin, uses per-request CSRF
tokens, escapes agent-supplied text, disables caching and framing, and has no
unauthenticated request feed. Login failures are globally limited to 20 per
minute. Keep access source-restricted: flooding can affect availability.

## Audit, limits, and recovery

The root-only database is
`/var/lib/basaltwater-privilege-broker/requests.sqlite3`. Its `requests` and
`events` tables record decisions and outcomes. It is protected from the coding
account, but not tamper-proof against root. Execution claims are committed
before effects. Pending requests expire on restart; an in-progress action is
marked uncertain and never replayed.

There can be eight outstanding requests and 30 requests per UID per hour. The
database capacity is bounded and fails closed. Archive it as root with the
services stopped if storage fills.

The managed units are `basaltwater-privilege-broker.service` (root) and
`basaltwater-privilege-approval.service` (locked `basaltwater-approval` account).
The web service receives its credentials through systemd `LoadCredential`.
Root SSH remains the setup and recovery path. The first version has no
passkeys, multi-user roles, push notifications, or browser password recovery.

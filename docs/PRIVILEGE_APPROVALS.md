# Agent privilege approvals

Agent VMs can request a privileged operation, give the user a review link, and
receive its execution status without the user opening a terminal. Approval uses
a separate HTTPS page and password. The main web panel links to it when enabled.
The first version supports VM reboot and exact administrator-registered system
service restarts. It does not provide arbitrary sudo, shells, package installation,
file writes, command prefixes, or wildcard permissions.

## Enable and assign a password

Run setup from your trusted administrator machine:

```bash
infra-tools patch 192.168.1.50 agent \
  --privilege-broker https://192.168.1.50:9444 \
  --privilege-broker-password
```

The password flag without a value prompts privately. Use a separate password of
16–256 characters. An explicit value is supported for automation but can appear
in shell history and process arguments. Only its salted scrypt hash is uploaded
in private setup arguments; saved configuration and reconstructed setup commands
omit it. The approval login username is the setup username, but its password is
independent of the Linux, web-panel, and T3 passwords.

The same flags work with `setup agent_vm`, `agent_code_vm`, or
`agent_workstation` on a VM. Use a lowercase hostname and an explicit dedicated
HTTPS port above 1023, outside the shared gateway's reserved 8443–8999 range.
Setup rejects managed port conflicts and non-VM targets.
The listener uses the managed machine certificate; enroll its CA on your own
device using [Client CA trust](CLIENT_CA_TRUST.md). Do not bypass certificate
verification. Managed firewall access-source restrictions apply to this port.

Setup removes administrator and root-equivalent supplementary groups and refuses
remaining named-user sudoers grants. Explicitly disable an existing
`--nopasswd`, `--harden-agent`, or `--harden-user` posture when switching.
A root-owned polkit rule also denies the coding account's polkit authorizations.
On an existing machine, terminate old coding-user sessions or reboot through the
administrator channel before treating this boundary as effective: running
processes can retain their old groups and existing privileged processes cannot
be revoked by changing account configuration. Prefer a fresh VM if that account
previously ran untrusted code with root access.

Keep root SSH as the setup and recovery channel. Never give the agent the
approval password or store it in the VM's browser. Approve from your own trusted
device. The page uses browser-native HTTPS Basic authentication, no cookies,
and a distinct origin. A compromised coding account or main panel must not hold
the approval credential.

## Agent and user workflow

```bash
infra-tools agent privilege request system.reboot --reason "Apply the installed kernel update" --json
infra-tools agent privilege request service.restart --unit example.service --reason "Restart the reviewed service" --json
infra-tools agent privilege status REQUEST_ID --json
infra-tools agent privilege wait REQUEST_ID --timeout 300 --json
```

The agent shares the returned `review_url`. The user opens it, signs in, checks
the machine, requester UID, operation, parameters, effects, and expiry, then
chooses **Approve once** or **Deny**. The explanation is explicitly untrusted.
The root broker executes an approved operation automatically; no terminal or
second privileged command is required. The page's root URL lists recent requests.

Requests expire after five minutes by default. An approval authorizes one
attempt, never a sudo session or a reusable bearer token. Changed policy
invalidates the request. Restarting the broker expires pending approvals and
marks interrupted execution uncertain, without retrying it. A reboot can be
reported as `dispatched`; this is not proof that the VM came back. Check status
before repeating a failed or uncertain request. Wait returns exit code 2 if its
timeout elapses before a terminal state.

## Administrator policy and allowlists

Root owns `/etc/infra-tools/privilege-broker/policy.json`. Initial policy has an
empty `services` object and `"reboot": "approve"`. An administrator can register
exact units through the existing root management channel:

```json
{
  "example.service": "approve",
  "reviewed-worker.service": "allow"
}
```

This is the value of the policy's `services` field, not a replacement for the
whole policy. `approve` needs a browser decision for each request; `allow`
executes automatically and still records the decision and outcome. Unregistered
units are denied. Reboot supports only `approve` or `deny`. There are no default
automatic permissions. Changes are read on every request and before execution;
no restart is needed for service rules. Preserve the installation's random
`machine` identity and requester UID. The strict schema permits at most 32
services and a `ttl_seconds` value from 30 to 900.

**A service registration grants all effects of restarting that service.** Root
must audit its unit, drop-ins, environment, executable, scripts, configuration,
dependencies, and lifecycle hooks before registering it. Do not register a root
service that consumes agent-writable code or configuration: restarting it could
give the agent arbitrary root execution even with an exact unit name. The broker
does not inspect or snapshot those transitive inputs. Administrators must avoid
changing service definitions while approvals are outstanding. Register only
services whose full restart effects are acceptable; do not allowlist the broker
or approval service themselves.

Policy, code, credentials, and all ancestor paths must be root-owned and not
group/world writable or symlinks. Never let the coding user edit this policy.
The approval page cannot change it.

## Rotation, removal, and recovery

Rotate the separate password from the trusted controller:

```bash
infra-tools patch 192.168.1.50 agent --privilege-broker-password
infra-tools patch 192.168.1.50 agent --no-privilege-broker
```

Omitting the flags preserves the installed setting and password. Rotation
restarts the services and expires outstanding approvals. Supply a new password
when changing the approval login username. Disabling stops and removes managed
services, deletes approval credentials and the polkit rule, and restores recorded
account groups through the normal security-posture reconciliation. The root-only
audit database, TLS files, and administrator policy remain for inspection and
possible reinstallation. Do not rename the coding account without reconciling
the broker and its polkit rule through setup.

The service units are `infra-tools-privilege-broker.service` (root) and
`infra-tools-privilege-approval.service` (locked `infra-approval` account).
The web service receives private credentials through systemd `LoadCredential`.
It alone can connect to the decision socket; the coding UID can only request
operations and read its own status. Both interfaces validate kernel peer
credentials, bounded messages, and strict action fields. Execution uses fixed
argv, a clean environment, no shell, and no privileged output returned to agents.

The audit database is
`/var/lib/infra-tools-privilege-broker/requests.sqlite3`, with `requests` and
`events` tables. Decisions and execution claims are committed before effects;
it is protected from the coding account, not tamper-proof against root.
Requests are limited to eight outstanding and 30 per UID per hour, persisting
across daemon restarts. Database capacity is bounded; archive it as root while
services are stopped if it fills. Full storage fails closed.

The web service limits login failures to 20 per minute globally, bounds reads,
requires exact Host/Origin plus per-request CSRF tokens, escapes agent text, and
disables caching and framing. It deliberately has no public unauthenticated
request feed. Network or login flooding can deny availability; use private
network access restrictions. This first version has no passkey enrollment,
multi-user roles, push notifications, or browser password-recovery flow:
recovery uses the trusted setup/root channel.

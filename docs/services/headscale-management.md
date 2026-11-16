# Explicit SSH management and tagged enrollment

A saved Headscale name supplies endpoint discovery. It grants no administrative
authority. The controller keeps a separate private SSH management binding to
that stable server ID and HTTPS endpoint. Renames preserve the binding; endpoint
changes, replacement records and changed SSH identities require explicit review.
External references need the same explicit authority as managed references.

Routine managed networks use setup: `--headscale --headscale-name NAME` saves
and binds a successfully installed server, and client setup with
`--tailscale NAME --tailscale-tag tag:service` handles preparation and private
enrollment. The [three-VM walkthrough](headscale-vms.md) is the usual entrypoint.
The explicit bind/enroll commands below remain available for separately managed
servers and recovery.

For DNS hookup, use saved VM names:

```bash
basaltw headscale-manage dns-publish office --resolver dns-vm --peer app-vm
basaltw headscale-manage dns-inspect office
basaltw headscale-manage dns-withdraw office --resolver dns-vm --peer app-vm
```

These commands derive observed native identities and configure restricted
publication SSH authority from the existing controller bindings. They require
no user-authored JSON or manual private-key transfer. Publication and withdrawal
retain the [DNS transaction and recovery checks](headscale-dns.md).

The management adapter requires root SSH with an explicitly selected private key
and an existing pinned known-hosts file. It disables password prompts, agent and
connection sharing, forwarding, proxy commands and automatic host-key updates.
Credential files remain open through descriptor paths during each bounded SSH
exchange. The remote adapter checks the actual managed endpoint, pinned binary,
running process and private native socket before every operation.

The root CLI exposes these commands in the active `--workspace`:

```bash
basaltw headscale-manage bind office --ssh-host control.example.com --ssh-key /private/controller.key --ssh-known-hosts /private/known_hosts
basaltw headscale-manage show office
basaltw headscale-manage enroll office --target-host worker.example.com --target-key /private/worker.key --target-known-hosts /private/known_hosts --tag tag:service
basaltw headscale-manage inspect office --remote
```

Both SSH bindings default to port 22; use `--ssh-port` for the control server
and `--target-port` for an enrollment target when needed. Bind the control server
before enrolling targets, and enroll targets serially within the workspace.
For complete preparation and VM commands, see the
[three-VM walkthrough](headscale-vms.md).

First prepare the target with ordinary native client setup using that network.
The target must have active kernel networking, the managed prepared binding,
private native state and the logging opt-out in its running daemon environment.
An already enrolled matching node is observed on both ends without issuing a key.
A pending, changed or unowned client must be reconciled before a new attempt.

`enroll` explicitly authorizes only the listed service tags for userless tagged
machines; creating a personal user is not a prerequisite for this flow. It records
a private controller intent before requesting a single-use, non-ephemeral key with a
one-hour expiry. The control server also retains its issuance intent. The secret
travels only in private SSH input/output streams and a temporary mode-600 file
in a private volatile target directory. It never appears in argv, setup/cache
payloads, command output, logs, review records or the enrollment journal.

The server records a one-use delivery claim before returning the secret and
deletes its delivery file. The pinned native API returns a masked prefix when
listing keys; delivery checks that prefix against the private creation response
and verifies its exact key ID, tags, lifetime and unused single-use state. It
does not expect a listed key to reproduce the secret. The target records its
joining state before passing the file to the native client and removes that
file after submission. The final
check compares the native target identity, addresses and tags with the control
server's node and verifies its native bootstrap-key association. Successful
completion expires the consumed bootstrap key and clears the issuance guard.
Key expiry is separate from node expiry; revoking a key does not revoke a node.

Any lost acknowledgement retains a blocking controller journal and server/native
state. Do not rerun issuance, fetch a claimed key again, delete guards, or assume
a failed transport means that enrollment failed. Inspect the retained attempt
and native key/node metadata through `inspect --remote`. Its `new_key_ids` lists
keys created since the server's retained issuance intent. Review those IDs and
all current node IDs, then reconcile explicitly:

```bash
basaltw headscale-manage reconcile office --expected-key-id 9 --expected-node-id 7
```

Repeat the expected-ID flags for each reviewed identity. With a retained server
intent, expected key IDs are the keys created since that intent. If remote
completion succeeded and only its acknowledgement was lost, review all current
key IDs instead. The native reconciliation expires outstanding issued keys and
removes private delivery material, preserving enrolled nodes. The controller
records that review without replaying any enrollment. A still-pending target
requires explicit native abandonment after remote key/node reconciliation.
Existing nodes require their own explicit expiry/removal to offboard.
After completed reconciliation, setup can inspect a new attempt with corrected
requested tags. An existing enrolled node must still match those tags; setup
does not retag it.

Each exchange opens a new SSH connection. Host SSH rate limits can reject a
burst of inspection, reconciliation and enrollment commands. Allow that limit
to clear before an explicitly reviewed new attempt; a connection refusal does
not authorize replaying a retained delivery or clearing its journal.

`remove` forgets only the local management binding and refuses an unreconciled
attempt. `--dry-run` validates references and private schemas without acquiring
locks, reading credentials, connecting, issuing keys or creating state. Local
inspection also creates no state. Private controller files are
`headscale-management.json` and `headscale-enrollment.json` in the workspace.
Keep them and SSH inventories outside Git. Independent root SSH recovery remains
available through the [native administration adapter](headscale-administration.md).

Offline tests cover authority separation, rename/endpoint binding, private-file
safety, delivery ordering, missing acknowledgements, explicit reconciliation and
secret-free diagnostics. Disposable Debian amd64 qualification passes full private
SSH delivery to two tagged machine clients, exact native key/node identity checks
and daemon privacy checks. Native tagged disable/enable and stopped-profile resume
at `145197b2` change only `WantRunning`, with 70 focused client tests passing.
Personal Auth-ID approval and connected-client revocation have separate installed
portal qualification; see the [qualification record](../testing/headscale.md)
for the exact native evidence and remaining acceptance checks.

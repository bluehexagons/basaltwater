# Explicit SSH management and tagged enrollment

A saved Headscale name supplies endpoint discovery. It grants no administrative
authority. The controller keeps a separate private SSH management binding to
that stable server ID and HTTPS endpoint. Renames preserve the binding; endpoint
changes, replacement records and changed SSH identities require explicit review.
External references need the same explicit authority as managed references.

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

First prepare the target with ordinary native client setup using that network.
The target must have active kernel networking, the managed prepared binding,
private native state and the logging opt-out in its running daemon environment.
An already enrolled matching node is observed on both ends without issuing a key.
A pending, changed or unowned client must be reconciled before a new attempt.

`enroll` explicitly authorizes only the listed service tags. It records a private
controller intent before requesting a single-use, non-ephemeral key with a
one-hour expiry. The control server also retains its issuance intent. The secret
travels only in private SSH input/output streams and a temporary mode-600 file
in a private volatile target directory. It never appears in argv, setup/cache
payloads, command output, logs, review records or the enrollment journal.

The server records a one-use delivery claim before returning the secret and
deletes its delivery file. The target records its joining state before passing
the file to the native client and removes that file after submission. The final
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

`remove` forgets only the local management binding and refuses an unreconciled
attempt. `--dry-run` validates references and private schemas without acquiring
locks, reading credentials, connecting, issuing keys or creating state. Local
inspection also creates no state. Private controller files are
`headscale-management.json` and `headscale-enrollment.json` in the workspace.
Keep them and SSH inventories outside Git. Independent root SSH recovery remains
available through the [native administration adapter](headscale-administration.md).

Offline tests cover authority separation, rename/endpoint binding, private-file
safety, delivery ordering, missing acknowledgements, explicit reconciliation and
secret-free diagnostics. Native socket/key-to-node linkage and full SSH delivery
still require disposable live qualification.

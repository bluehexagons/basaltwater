# Internal build-host SSH client

`lib.build_client.BuildHostClient` is the controller-side transport for the
typed `basaltwater-build-v1` service described in
[build-host service](build-host-service.md) and
[build contracts](build-contracts.md). The public
[selected-builder setup flow](build-setup.md) resolves its explicit endpoint
from saved controller configuration.

## Construction and results

Create a client with an explicit SSH destination, key, pinned known-hosts file,
port, and finite deadline:

```python
from lib.build_client import BuildHostClient

with BuildHostClient(
    host="builder.example.net",
    username="build-controller",
    port=22,
    identity_file="/home/controller/.ssh/build-host",
    known_hosts_file="/home/controller/.ssh/build-host-known-hosts",
    timeout=300,
) as client:
    grants = client.capabilities()
```

Paths must be absolute. The identity file must be an effective-UID-owned,
single-link regular file with mode `0600` or stricter. The known-hosts file must
be a root or effective-UID-owned, single-link regular file that is not writable
by group or other users. Both are opened without following the final symlink
and referenced by SSH through their pinned file descriptors. The known-hosts
file must contain the host key for this host and port. Hostnames and Unix
usernames use the repository validators. The timeout must be finite, greater
than zero, and no more than the server's 900-second connection ceiling.

The methods return the service's validated public data:

| Method | Result |
| --- | --- |
| `capabilities()` | The caller's project grants and safe tool-version observations |
| `submit(request, source_path)` | A public job receipt matching the request's ID, source, project, platform, and limits |
| `status(request_id)` | A public job receipt whose returned ID must match the requested ID |
| `cancel(request_id)` | The matching public receipt after the service records its cancellation request |
| `log(request_id, offset, max_bytes)` | `BuildLogChunk(data, offset, next_offset, waiting, complete)` with raw bytes |
| `download_artifact(request_id, destination, expected_sha256, expected_size)` | The absolute path of the verified published file |

Each call opens a new SSH transport. There are no automatic retries or
request-ID generation. A `BuildSubmissionUnconfirmed` raised after SSH starts
includes `request_id` and any fixed service error code. Admission may already
be durable for any submit error, including `rejected` and `unavailable`, or
when the reply is lost. Query `status()` and reconcile using the same request ID
and exact inputs. Never create a replacement ID because a submit reply was
uncertain. A keyboard interrupt after SSH starts also raises
`BuildSubmissionUnconfirmed` after local transport cleanup; an interrupt before
the process starts remains an ordinary `KeyboardInterrupt`.

Calling `cancel()` sends the service's durable cancellation request. Interrupting
or dropping a local call only stops its SSH process group; it does not stop an
already accepted worker job. The worker is independent of the connection.

## SSH and wire boundaries

The client invokes `/usr/bin/ssh` with `-F /dev/null`, explicit key and
known-hosts descriptors, `BatchMode=yes`, `StrictHostKeyChecking=yes`, and
global host keys disabled. It disables agent use and forwarding, TTY allocation,
all forwarding, proxy commands and jumps, host canonicalization, local commands,
and connection multiplexing. It sends exactly the remote command
`basaltwater-build-v1`; no request field can select a command, path, principal,
or SSH option. It starts one local process group and kills only that group on
timeout, interruption, or protocol failure. For each call, the client
duplicates both credential descriptors and keeps them open until SSH cleanup.
SSH reads those duplicates through `/proc/<controller-pid>/fd/N`; SSH receives
no inherited credential descriptors, and the original configured pathnames
are never used as a fallback.

Requests use the service's schema-1 JSON header and the exact action fields.
Submit streams the source snapshot after its header; other actions send EOF
after the header. The source is opened no-follow as a regular file. Its size is
bounded and used as the header's `source_size`; its bytes are checked against
`request.source.snapshot_sha256` and its file identity is checked again while
streaming. It is hashed in bounded chunks and is never loaded wholly into
memory.

The response line is capped at 64 KiB and parsed with duplicate-key and
non-finite-number rejection. The client checks exact top-level and
action-specific fields, public job identities and types, the requested job ID,
and each action's payload length. Log data is capped at 64 KiB per call. Artifact
data is streamed in 64 KiB blocks to a mode-`0600` exclusive temporary in the
destination directory. The destination parent must be an effective-UID-owned
private `0700` directory reached through no-follow, trusted ancestors. The
client verifies the expected total size and SHA-256 before publishing with
Linux `renameat2(RENAME_NOREPLACE)`, then syncs the file and directory. Existing
destination names are never replaced; unsupported no-replace publication
fails closed. If the rename succeeds but syncing the destination directory
fails, `ArtifactPublicationUncertain` exposes `destination`, `expected_sha256`,
and `expected_size`. The verified bytes are already present at that destination,
but crash durability of the directory entry is uncertain. Check that exact
destination against the expected size and digest before retrying; never delete
or overwrite it automatically.

SSH stderr is drained throughout the call, with at most 64 KiB retained and
excess bytes discarded. The client does not include remote exception text or
stderr in protocol errors. Timeouts include pipe activity and remote response
waits; cleanup uses bounded TERM/KILL waits. A local transport exit or
connection loss does not imply that the server rejected an operation.

The focused tests use temporary files and a mocked SSH process boundary. They
do not contact a build host or run SSH against a VM.

## Internal transport reuse

The [target client](build-target-client.md) composes a private specialization of
this client. Its fixed `_remote_command`, `_header_limit` and `_parse_reply`
extension points select the target protocol without duplicating credential,
selector, framing or cleanup machinery. `_call()` has an internal `on_start`
callback so target mutations can preserve uncertainty even for cleanup errors.
Cleanup attempts every pipe and credential close even when an earlier cleanup
step is interrupted; a post-start cleanup interrupt retains submit uncertainty.
`_open_source()` also accepts an internal maximum and expected exact size for
artifact uploads. Both source paths check EOF explicitly during prehash and
transfer. These internal hooks do not change the public `BuildHostClient` API.

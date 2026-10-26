# Internal target SSH client

`lib.build_target_client.BuildTargetClient` sends typed controller requests to
`lib.build_target_service.BuildTargetService` using the exact forced command
`basaltwater-target-v1`. It is an internal library; enrollment, installer,
controller selection and command-line wiring are separate slices.

## Explicit construction and API

```python
from lib.build_target_client import BuildTargetClient

with BuildTargetClient(
    host="target.example.net",
    username="deploy-controller",
    port=22,
    identity_file="/home/controller/.ssh/target",
    known_hosts_file="/home/controller/.ssh/target-known-hosts",
    timeout=300,
) as client:
    grant = client.preflight("shop")
```

All six operations return detached, validated public dictionaries. The method
signatures are:

```python
preflight(application)
reserve(*, idempotency_key, application, request, deployment, ttl_seconds)
status(operation_id)
cancel(operation_id)
stage(*, operation_id, generation, request, deployment,
      artifact_path, artifact_sha256, artifact_size)
activate(*, operation_id, generation, request, deployment, artifact_sha256)
```

The constructor takes exactly the explicit `host`, `username`, `port`,
`identity_file`, `known_hosts_file` and `timeout` keyword parameters shown above.
`close()` is idempotent, and the context manager releases pinned credentials.
Timeouts must be finite, positive and at most 900 seconds. Every call starts
one fresh SSH process. There are no implicit retries, generated IDs, builder
fallbacks or target builds.

`request` is the full schema-1 build request, and `deployment` is the restricted
[target deployment projection](build-target-policy.md). Local artifact and
credential paths and an authenticated owner are never added to the wire
document. The endpoint derives its owner from trusted SSH authentication.
The request parser is shared with the target service so both ends enforce the
same exact keys, types, request/projection binding, TTL and artifact ceilings.

## Public results and recovery

`preflight()` returns exactly `application`, `project`, `routes`, `platform` and
`limits`. Routes contain only `name`, `type`, `domain` and `path`. The client
validates the selected application, unique names and destinations, normalized
routes, supported platform and bounded integer artifact limits.

`reserve()`, `status()` and `cancel()` return exactly `operation_id`,
`application`, `project`, `generation`, `input`, `bindings_sha256`, `source`,
`created_at`, `expires_at`, `state`, `effective_status`, `superseded` and
`expired`. `input` contains only `request_id` and `request_sha256`; source has
the revision, snapshot digest and manifest digest. The reservation response
must match the supplied application, project, full request digest, request ID
and source. Status and cancellation must echo the supplied operation ID;
cancellation must acknowledge `cancelled`. Operation IDs differ from build
request IDs. Bindings digests describe target-owned policy and are checked as
digests; the controller cannot reconstruct private target bindings.

Admission metadata follows the [admission store](build-admission.md): finite
non-negative timestamps with expiry after creation, bounded integer generations,
strict boolean flags, and effective status derived from state and flags.
`activating` reports `uncertain`. For reserved work, supersession takes priority
over expiry. Other stored states retain their effective status even when the
flags change. Flags reflect the target's observation; controller wall time does
not decide whether the target's record is expired. A replayed reservation may
already be completed, cancelled, superseded or expired.

`stage()` returns exactly `operation_id`, `generation`, `artifact_sha256`,
`artifact_size` and `status="staged"`. `activate()` returns exactly
`application`, `operation_id`, `generation`, `artifact_sha256` and
`status="completed"`. Every echoed identity and size must match the supplied
values with strict types. Cached completed activation acknowledgements are
accepted, including after expiry; the endpoint owns replay authorization.

After SSH starts, **every mutating error** from `reserve()`, `cancel()`,
`stage()` or `activate()` raises `TargetOperationUnconfirmed`. This includes
fixed server rejections, malformed replies, disconnects, timeouts and keyboard
interruption. It exposes `action`, `operation_id`, `idempotency_key` and
`error_code` (only a fixed service code when available).

For an uncertain reservation, repeat `reserve()` with the same idempotency key
and exact inputs to recover the operation ID. Never choose a replacement key
to bypass uncertainty. For the other mutations, query `status()` with the same
operation ID and reconcile before retrying. Disconnecting or stopping local
SSH cannot undo accepted target work. There is no automatic retry or
reconciliation in this client.

Pre-start validation, file and transport failures remain ordinary local
errors; a pre-start keyboard interrupt remains `KeyboardInterrupt`. Read-only
calls use `TargetProtocolError`, `TargetTransportError` or `TargetRemoteError`,
which inherit `TargetClientError`. Status errors never imply a mutation.

## Transport and artifact boundaries

The target's private transport specialization uses the existing
[build-host client's SSH supervision](build-client.md). It changes only the
fixed remote command, request header bound and typed response parser. The
public target API uses composition and exposes only its six target operations.
The build-host public API and operation behavior remain unchanged.

Both protocols use no ambient SSH configuration, no agent or forwarding, no
passwords, strict explicit known hosts, no proxies, no multiplexing and no TTY.
Each call duplicates the no-follow trusted credential descriptors and keeps
them open through SSH cleanup. SSH opens `/proc/<controller-pid>/fd/N`; it
inherits no credential descriptor and never falls back to configured paths.

Stage opens a canonical absolute artifact path without following its final
symlink and requires a nonempty regular file of exactly the supplied size.
The request's artifact ceiling and the protocol bundle ceiling both apply.
The client hashes the whole pinned file before starting SSH, checks descriptor
and pathname identity before and after hashing, then streams after the header.
It rehashes every transferred byte, checks exact size and EOF, and rechecks both
identities at the end of input. File reads and pipe writes use at most 64 KiB
chunks. One finite deadline covers validation, prehashing, streaming and the
response wait; TERM/KILL cleanup has separate bounded waits.

Every response is one strict JSON line of at most 64 KiB, with duplicate keys
and non-finite numbers rejected, exact envelope/result keys and no payload.
Trailing bytes, even another newline, fail closed. Stderr is drained while
retaining at most 64 KiB privately. Exceptions never expose response content or
stderr. Fixed public errors are only `rejected` and `unavailable`.

Tests use temporary fixtures, mocked SSH processes with real local pipes and
the target dispatcher. They exercise no native SSH, network, VM or deployment
operations.

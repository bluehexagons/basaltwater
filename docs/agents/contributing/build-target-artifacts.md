# Target artifact staging store

`lib.build_target_artifacts.BuildTargetArtifactStore` is an internal bounded
backend for retaining a verified externally built bundle on a deployment
target. It does not implement transport, setup or command enablement. The
caller must reserve a `DeploymentAdmissionStore` operation before staging.
Authentication, application authorization, current policy, stream deadline
supervision and activation remain caller responsibilities.

## API

```python
store = BuildTargetArtifactStore(
    "/var/lib/basaltwater/target-artifacts",
    max_records=128,
    max_bytes=64 * 1024**3,
)
receipt = store.stage(
    operation_id,
    generation,
    validated_request,
    application="web",
    owner=authenticated_principal,
    bindings_sha256=authorized_bindings_digest,
    artifact_sha256=bundle_sha256,
    artifact_size=bundle_size,
    source=bounded_binary_stream,
    admissions=admission_store,
)
```

The root must already exist as an effective-UID-owned `0700` directory. Its
canonical path is opened component by component without following symlinks;
ancestors must prevent untrusted rename access. A trusted sticky parent such as
`/tmp` is allowed when it protects the trusted-owned child. The stable private
lock and initialization witness are store-owned. Keep them and retained files
together; do not replace the lock while the store is in use.

`application` is explicit because the target application identity is not part
of a build request. The request is parsed with `parse_build_request()`. The
intent stores only its canonical digest and source identity, not commands,
environment values, secrets or local paths.

`stage()` checks the owner, application, operation, generation, request digest,
source identity, target bindings, current desired generation, cancellation and
expiry through `admissions.status()` before it reads a new stream. It writes an
exclusive durable intent before reading bytes or creating the partial bundle.
It reserves the declared wire size, the request's maximum expanded artifact
bytes, one maximum descriptor, and bounded intent metadata. The stream is read
in chunks no larger than 64 KiB, must match the exact declared size and SHA-256,
and must reach EOF with no trailing bytes.

The staged bytes are checked with the existing `unpack_artifact()` using the
actual validated request. Verification uses a private temporary tree and does
not execute bundle content. After verification, the store checks the admission
again before publication and once more before returning the receipt. Publication
uses a no-replace rename, a read-only `0400` bundle, file and directory syncing,
and a small durable ready marker. The receipt contains operation/application/
request identities, generation, source and bindings digests, artifact digest,
and size; it contains no private filesystem path.

The receipt is an internal staging result, not authorization to activate. The
trusted caller should use the same artifact digest with
`DeploymentAdmissionStore.activate()`. Its action callback runs under the
admission lock and can open the exact staged bytes through:

```python
with store.open_verified_bundle(
    operation_id,
    generation,
    validated_request,
    application="web",
    owner=authenticated_principal,
    bindings_sha256=authorized_bindings_digest,
    artifact_sha256=bundle_sha256,
) as bundle_path:
    return activate_release(bundle_path)
```

The exact accessor signature is:

```python
open_verified_bundle(
    operation_id: str,
    generation: int,
    request: object,
    *,
    application: str,
    owner: str,
    bindings_sha256: str,
    artifact_sha256: str,
) -> Iterator[str]
```

The trusted caller must already hold the admission activation lock and have
checked current admission and application policy. The accessor intentionally
does not call `admissions.status()`, avoiding an admission-lock/staging-lock
inversion. It checks all supplied identities against the protected staging
intent, derives size and reservation from that validated intent, verifies the
published file's type, mode, link count, stable identity, size and full hash,
then yields its protected path while holding the staging lock and read-only file
descriptor through activation. It checks the named file identity again as the
context exits and accepts no path from the caller. The admission
activation method still rechecks reservation state, generation, expiry,
cancellation, request, bindings and the same artifact digest before invoking
the callback.

## Replay, retention and recovery

An operation ID can bind to one owner, application, generation, request/source,
bindings digest, artifact digest and size. A replay without a source is allowed
only for a ready published bundle. If a stream is provided, replay also
consumes it and verifies its exact size, EOF and digest. Before success, replay
checks retained file identity and hash and runs `unpack_artifact()` against the
request again. A different digest, size or identity is rejected without
overwriting the retained file. An exact completed admission may replay its
already staged artifact; a completed admission cannot start fresh staging.

Each retained intent counts toward `max_records` and keeps its full reservation
against `max_bytes`, including after a failed or partial upload. There is no
garbage collection. A partial stream, failed archive verification, missing
ready marker, or uncertain post-rename directory sync leaves the intent and
available evidence in place. That operation cannot be resumed or rebound. A
published bundle without its ready marker is ambiguous and cannot be replayed.
A ready-marker write or sync failure adds a retained uncertainty marker when
the filesystem still accepts writes; that operation also requires
reconciliation.

The lock witness and directory scan also reject retained bytes whose intent or
initialization evidence has been lost. Preserve all evidence for operator
reconciliation; this store has no reset or repair API.

The transport must bound stream read time. The store does not prove artifact
provenance, authenticate a principal, authorize a target application, validate
runtime compatibility, run isolation, protect against a malicious process
under the same effective UID, or roll back activation side effects. It must be
wired only after those surrounding contracts are designed.

# Internal controller delivery compositor

`lib.build_delivery` composes the existing trusted [builder client](build-client.md)
and [target client](build-target-client.md). It accepts prepared pinned inputs;
it does not prepare Git repositories, select credentials, bootstrap machines,
install packages or integrate setup/configuration/CLI selection. The proposed
`--build-on` setup flag is not available through this library.

The first slice supports the existing Debian 13 x86-64 platform, static outputs
and self-contained Go services. The target retains authority over application
roots, runtime validation, generations, expiry and final activation. A Go tool
declaration is a build requirement, not proof of binary compatibility; the
target's prebuilt runtime checks remain necessary. There is no fallback build
on the target, alternative builder selection or automatic remote cancellation.

## Exact API

```python
from lib.build_delivery import (
    ApplicationDeliveryReceipt,
    BuildDeliveryController,
    DeliveryPlan,
    DeliveryReceipt,
    DeliveryRecoveryRequired,
)

plan = DeliveryPlan(
    request=prepared_request,
    source_path=prepared_snapshot_path,
    deployment=typed_target_deployment,
    idempotency_key=stable_application_key,
    required_tools=("go",),
    ttl_seconds=3600,
)
controller = BuildDeliveryController(
    builder=trusted_builder_client,
    target=trusted_target_client,
    workspace=private_delivery_directory,
    timeout_seconds=3600,
    poll_interval=1,
    # Optional test dependencies: clock=time.monotonic, sleeper=time.sleep.
)
receipt = controller.deliver([plan])
```

`DeliveryPlan` is a frozen dataclass with these fields, in order:

| Field | Type / contract |
| --- | --- |
| `request` | `Mapping[str, Any]`; complete schema-1 [build request](build-contracts.md), including caller-selected request ID |
| `source_path` | `str` or `os.PathLike[str]`; canonical absolute existing prepared snapshot file |
| `deployment` | `Mapping[str, Any]`; restricted schema-1 [target projection](build-target-policy.md), validated against the exact request |
| `idempotency_key` | `str`; explicit stable 32 lowercase hexadecimal digits for this application/attempt |
| `required_tools` | `tuple[str, ...]`; explicit distinct names from `go`, `node`, `python` |
| `ttl_seconds` | `int`, default `3600`; existing target reservation TTL bounds apply |

Pass ordinary JSON mappings/lists for the wire documents. The projection has
exactly `schema`, `application`, `project`, `platform`, `components`. Static
components have `name`, `type="static"`, `domain`, `path`, `artifacts`, `output`,
`spa`. Service components have `name`, `type="service"`, `domain`, `path`,
`artifacts`, `binary` and optional `health`. Deployment roots, commands, runtime
environment and credentials are not projection fields. The parser checks
component names, types and exports against the request and rejects extra fields.
See [target policy](build-target-policy.md) for the full projection rules.

Callers retain the full request, deployment and prepared source identity for
recovery. Use manifest preparation's explicit tool requirements; this library
does not infer tools from commands. Command-bearing requests require a nonempty
tool tuple; services require `go`. Every requested tool name must appear in both
the builder's project runtime grant and its observed tool-version map. A version
reported for another tool cannot satisfy that requirement. This slice does not
add a version-constraint language.

`deliver(plans: Sequence[DeliveryPlan]) -> DeliveryReceipt` accepts 1–16 plans.
Application IDs, build request IDs and idempotency keys must each be unique
within the delivery. Both documents are normalized into detached validated
inputs before probes. Snapshot hashes must match the request before any remote
call. The builder remains responsible for checking the pinned snapshot's full
format and manifest/request consistency during source admission.

`DeliveryReceipt` has `applications: tuple[ApplicationDeliveryReceipt, ...]`
and `receipt_path: str`. Each application receipt has `application`, `request_id`,
`operation_id`, `generation`, `artifact_sha256`, `artifact_size`, and
`status="completed"`, in the caller's activation order. `DeliveryRecoveryRequired`
exposes `journal_path` and concrete same-identity inspection guidance.

## Ordering and waiting

1. Validate every plan, projection, snapshot hash, identity and wire bound locally.
2. Check every target application grant and builder project/platform/resource/tool
   capability. No target reservation or builder submission precedes these checks.
3. Persist all recovery identities, then reserve **all** target intents before
   the first builder submission. Replayed non-current reservations fail closed.
4. Submit each build once. Poll fresh status until a terminal outcome. Download
   with the existing client's verified no-replace publication, independently
   check size/digest, unpack with request/descriptor/inventory verification, and
   stage the verified artifact. Finish all builds and stages before activation.
5. Recheck **all** target admissions after staging and immediately before the
   first activation. Require the same request, source, operation, generation
   and bindings, reserved/current state, and no expiry or supersession flags.
6. Activate sequentially and save each completed acknowledgement immediately.
   Publish an exclusive durable receipt before clearing the unfinished guard.

A later build or stage failure causes zero activations; active applications
remain unchanged. Multiple activations are not atomic: a later activation
failure preserves earlier completed receipts and the remaining recovery IDs.
The target still authorizes current state under its activation lock; status
probes cannot remove that target-side responsibility.

The overall deadline is finite, positive and at most 24 hours. Poll cadence is
finite, positive and at most 60 seconds. The injected monotonic clock and sleeper
make waiting deterministic in tests. Each synchronous SSH call temporarily uses
the smaller of its configured timeout and the remaining delivery budget; the
original timeout is restored even after interruption. Clients are exclusively
borrowed for delivery and must not be used concurrently. Internally the target's
existing `_transport.timeout` supplies that same budget; this is intentional
coupling to the current internal client contract, without changing either client.

Local source/artifact hashing checks the deadline in bounded chunks. Artifact
verification and durable filesystem calls are synchronous; they can finish after
the deadline, and the next deadline check prevents a new remote mutation. SSH
cleanup also retains its client's separate bounded grace periods. Successful
long-running builds can outlive individual short SSH calls because each poll
opens a fresh status call. Failed, expired, cancelled, uncertain or timed-out
builds never activate. Local timeout does not cancel accepted remote jobs.

## Private workspace and retained recovery evidence

Provision a separate existing effective-UID-owned `0700` output directory for
each delivery. Initially it must be empty, apart from the library's stable
`delivery.json.lock` left by a validation/preflight-only attempt. Ancestors are
opened without following symlinks and must prevent untrusted rename access;
trusted sticky parents such as `/tmp` are supported. Validation occurs before
the first write or network call. Snapshot files must be single-link private
regular files owned by the effective UID under a similarly trusted `0700`
parent. Preserve source snapshots separately from the output workspace.

The compositor uses `OperationStateStore` ownership and `delivery.json` as a
fixed unfinished guard. Before each remote mutation it syncs bounded context
containing application/project, stable reservation key, request ID, request and
deployment digests, source identity and phase. Returned operation ID, generation
and target bindings digest are saved before further calls. Artifact digest/size,
build state and fixed staging/activation receipts are added as they become
known. Context is capped at 64 KiB, the store bounds its marker/history, and
request limits bound each of at most 16 bundles and verification trees. An
internal operation-marker UUID is unrelated to all caller-selected remote IDs.

No credential, full build environment, command or full deployment input enters
the journal or receipt. Files use private modes. `artifact-REQUEST_ID.bundle`
and `verified-REQUEST_ID` name immutable new publications. The compositor never
replaces an existing publication and never performs housekeeping deletion.
Uncertain publication retains the exact expected filename, digest and size.

An exception or keyboard interruption after intent persistence leaves the
unfinished guard and raises `DeliveryRecoveryRequired`. A second `deliver()` in
that workspace stops before remote probes or mutations. If saving the recovery
phase fails, the preceding durable mutation phase remains the guard. Corrupt or
unsafe state is preserved and rejected. A successful delivery retains an
exclusive `receipt-FIRST_KEY.json` plus the operation store's `.last.json`
evidence; that workspace cannot be reused for another delivery. A partial
receipt publication also leaves its guard and available bytes for inspection.

After a lost submit reply, query `builder.status(request_id)` with the recorded
ID. After a lost reservation reply, explicitly call `target.reserve()` with the
**same key and exact caller-retained inputs** to recover its operation ID; the
compositor never does this automatically. After lost stage/activate replies,
query `target.status(operation_id)` and inspect the exact retained artifact and
actual target state. Target status alone does not prove staging completion.
Preserve prior completed receipts. Do not generate new IDs, overwrite files,
delete a guard, cancel accepted work or replay uncertain mutations to bypass
reconciliation. Recovery and receipt archival are explicit operator procedures;
this module supplies no reset/resume API.

Focused tests mock both clients and process creation, use private temporary
directories and verify actual bundle parsing/checksums with a temporary-file
publication adapter. They perform no SSH, Git, network, VM, native runtime probe
or application deployment. The existing deployment test-suite wildcard includes
`tests/test_build_delivery.py` without a coverage-plan change.

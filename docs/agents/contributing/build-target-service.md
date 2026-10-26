# Typed target deployment dispatcher

`lib.build_target_service.BuildTargetService` is one internal request
dispatcher for target deployments using externally built artifacts. The protected
[SSH entrypoint](build-target-entrypoint.md) supplies the authenticated principal,
maintenance ownership and connection deadline. Managed enrollment and the
[controller setup flow](build-setup.md) compose that transport; the dispatcher
itself does not listen or provision authority.

## Construction and trust boundary

```python
service = BuildTargetService(
    admissions=deployment_admission_store,
    load_policy=load_current_target_policy,
    stage_artifact=stage_and_verify_artifact,
    activate_release=activate_verified_release,
)
service.serve_once(
    principal=authenticated_principal,
    input_stream=stdin,
    output_stream=stdout,
)
```

The principal is a separate argument from trusted transport state. No action
accepts an owner, shell command, filesystem path, deployment root, environment,
credential, or arbitrary action field. The dispatcher uses the current target
policy on every operation and the real
[`DeploymentAdmissionStore`](build-admission.md) for durable intent and
generation ownership. It never starts a build.

The caller must enforce deadlines for blocking reads and writes. `serve_once()`
reads one bounded header line; `handle()` receives that line directly. The
service does not listen, authenticate, or provide a connection deadline.

## Framing and bounds

Every connection carries exactly one UTF-8 JSON header line with `schema: 1`
and one action. The maximum header line is `2 * MAX_REQUEST_BYTES + 4096`
bytes: up to one MiB each for the validated build request and target
projection, plus framing fields. Duplicate JSON keys, non-finite values,
unsupported schemas, unknown actions or fields, malformed lines, and wrong
types are rejected. Non-stage actions require exact EOF immediately after the
header, before policy loading or admission lookup/mutation.

Only `stage` accepts a binary body. Its positive `artifact_size` is bounded by
`MAX_ARTIFACT_BYTES + MAX_DESCRIPTOR_BYTES + 1024` and by the build request's
artifact ceiling plus descriptor overhead. `stage_artifact` owns consuming
exactly that many bytes, requiring EOF, verifying the complete digest and
descriptor, durably publishing the staged artifact, and rechecking the
admission after staging. The dispatcher does not read or buffer the artifact.
The entrypoint must supervise body reads with its deadline.

Every response is one JSON line no larger than 64 KiB. This service never
returns a binary response payload. Dispatch runs in a single-connection process;
Python console output from existing deployment helpers is redirected to stderr.
Inherited native child stdout also needs descriptor isolation in the transport;
Python redirection alone does not protect the wire. Repeat typed service
activation remains unqualified until that boundary is verified. Treat stderr as private operational
diagnostics, not as a response or a public log. Errors contain only `rejected` or
`unavailable`; an OS `PermissionError` with an errno is unavailable, while an
authorization `PermissionError` without an errno is rejected. Unexpected
exceptions return only `unavailable`. No exception text, command, path,
environment, credential, or callback result is serialized.

## Actions

Every field below is required; extra fields are rejected.

| Action | Fields | Effect |
| --- | --- | --- |
| `preflight` | `application` | Loads current policy and returns only the authenticated principal's selected application, project, routes, platform, and artifact limits. |
| `reserve` | `idempotency_key`, `application`, `request`, `deployment`, `ttl_seconds` | Revalidates the current policy and target projection, then durably reserves an application generation before any build is started elsewhere. |
| `status` | `operation_id` | Looks up the operation with the authenticated owner, then checks the current application and project grant. |
| `cancel` | `operation_id` | Performs the same owner and current-policy checks before cancellation. Started activation cannot be undone. |
| `stage` | `operation_id`, `generation`, `request`, `deployment`, `artifact_sha256`, `artifact_size` | Recomputes current bindings and exact admission identity before handing the binary body to the durable stage callback. |
| `activate` | `operation_id`, `generation`, `request`, `deployment`, `artifact_sha256` | Recomputes current bindings and exact admission identity, then delegates generation-locked activation to the admission store. |

`preflight` has no filesystem paths. Reserve and status replies use a fixed
public metadata projection: operation ID, application, project, generation,
request ID and digest, source identity, bindings digest, creation and expiry
times, state, and expiry/supersession flags. They exclude owner, idempotency
key, deployment root, artifact paths, logs, and stored activation results.
Stage replies contain only the operation, generation, artifact digest and size,
and a fixed `staged` status. Activation replies are a fixed receipt containing
application, operation, generation, artifact digest, and `completed`; the
activation callback's return value is discarded.

## Admission and callback ordering

`reserve` calls `authorize_target_deployment()` against freshly loaded policy,
then `DeploymentAdmissionStore.reserve()`. The store binds owner, idempotency
key, full request digest, project, source, bindings digest and generation. An
exact idempotency replay returns the original operation and does not extend
expiry or advance generation. A key reused for different input is rejected.

`status` and `cancel` first call `admissions.status(operation_id,
owner=principal)`. Only after that owner-scoped lookup do they check that the
current principal still has the same application and project grant. Missing,
other-owner, or revoked records return a fixed error without metadata.

For `stage` and `activate`, the dispatcher gets owner-scoped admission
metadata, loads current policy, and recomputes private bindings from the
validated request and deployment projection. It requires the projection
application, project, request ID and full request digest, source, generation,
and bindings digest to match the admission. Cancelled, expired, superseded, or
otherwise non-current operations cannot enter the stage callback. The callback
signature is:

```python
stage_artifact(
    operation_id,
    generation,
    request,
    *,
    application,
    owner,
    bindings_sha256,
    artifact_sha256,
    artifact_size,
    source,
    admissions,
)
```

The callback owns exact stream framing, durable artifact binding and upload,
whole-bundle digest and descriptor verification, and a final admission
recheck. It must not treat a disconnect as cancellation after durable
acceptance. It must fail closed if the operation is no longer current.

`admissions.activate()` holds the generation lock, checks expiry/current
generation and persists its activation marker before running the action. The
action reloads policy and recomputes the binding immediately before calling
`activate_release`, without reentering the admission store. The callback
receives only trusted private bindings, the parsed request, operation ID,
generation, artifact digest, and authenticated principal:

```python
activate_release(
    bindings=private_bindings,
    request=parsed_request,
    operation_id=operation_id,
    generation=generation,
    artifact_sha256=artifact_sha256,
    principal=authenticated_principal,
)
```

That callback must verify that a staged artifact has the same identity, perform
runtime validation, and carry out actual activation or publishing. Its return
value remains private and is discarded by this dispatcher. The admission store
caches a fixed `null` result for completed replay. An uncertain or activating
operation blocks automatic replay; the service does not rebuild, retry with a
new ID, or infer that activation failed.

## Recovery and limits

An error or disconnected caller can follow durable acceptance. After any
mutation error, inspect `status` and replay only the same operation with the
same identity; never create a fresh idempotency key or operation ID to retry
uncertain work. A successful reservation is durable intent even if the
connection closes before a response. An activation callback failure leaves
admission uncertain for operator reconciliation. Whether activation effects
continue after disconnect depends on the supervising entrypoint and callback.

This dispatcher adds no automatic build, fallback, background work, or
listener. Existing local deployment and setup paths remain independent.

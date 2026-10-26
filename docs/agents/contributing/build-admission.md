# Target deployment admission store

`lib.build_admission.DeploymentAdmissionStore` is a backend contract for a
target that receives an externally built artifact. It durably reserves target
intent before a build is acknowledged, assigns a per-application desired
generation, and serializes the final activation callback with the generation
check. This is an internal storage slice; it does not implement SSH transport,
caller authentication, target authorization policy, artifact transfer or
verification, runtime compatibility checks, or application deployment.

## Caller responsibilities

- Provision the store beneath an existing directory owned by the effective UID
  with owner read/write/search and no group or other permissions. The state
  file and stable sibling lock are private regular `0600` files. Do not remove
  or replace the lock file while the store is in use.
- Authenticate the transport before calling the store. Pass its trusted
  principal as `owner`; never accept an owner value from a build request.
- Validate that the principal may deploy the application and that the target
  bindings digest represents the authorized deployment descriptor. Hash
  canonical target bindings, and do not put secret values in the descriptor or
  this store.
- Keep the original validated build request available to `activate()`. The
  store persists its digest and non-secret identity fields, not the full
  request or its build environment. Build request parsing does not prove
  artifact provenance or runtime compatibility.
- Verify the transferred artifact against that request and pass its whole
  bundle SHA-256 to `activate()`. Supply a trusted, bounded action callback
  that returns JSON and does not include runtime secrets. The callback executes
  while the store lock is held; keep it non-reentrant and do not call this store
  from the callback.
- Treat admission operation IDs and build request IDs as distinct identities.
  Repeat `reserve()` with the same owner/key and exact application, project,
  request digest, source, and bindings to recover a lost response. A mismatch
  is an error. A replay does not extend expiry or allocate a new generation.

## Ordering and recovery

Every new reservation advances the application's desired generation and
supersedes older reserved work. Cancellation, expiration, build failure, or an
uncertain newer activation never selects an older generation again. Rollback
must be a new explicit admission for a retained compatible artifact.

Activation checks owner, request identity, generation, current desired intent,
expiry, cancellation state, and target bindings while holding the same global
lock used for the durable activation marker. It saves the artifact digest and
`activating` state before calling the action. A successful bounded JSON result
is saved as `completed`; a later call with the same identity and digest returns
that cached result. A different digest is rejected.

If the action raises, the record becomes `uncertain` when that update can be
saved. A process interruption or save error can leave it `activating`. Both
states block automatic replay. The store has no reconciliation or reset API:
an operator must inspect the retained record and actual target state, preserve
the evidence, and add a reviewed recovery procedure before any future repair
tool is allowed to resolve it. A completed or started action cannot be marked
as undone by `cancel()`.

The store retains all operations and replay identities. It rejects new work at
4096 operations or when the encoded state would exceed 16 MiB; it never silently
prunes an identity to make room. Corrupt, unreadable, unsupported, or unsafe
state fails closed and remains available for inspection. A busy store reports
an error instead of waiting; activation currently holds one target-wide lock,
so long callbacks block all admission-store calls on that target.

These records bind identity and ordering only. They do not roll back arbitrary
application side effects, protect against a malicious process running as the
same effective UID, establish authorization by themselves, or guarantee that
the target can execute an artifact. Extend the target policy and deployment
contracts before wiring this backend to a transport or service.

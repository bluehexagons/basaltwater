# Storage enrollment delivery and acceptance

Status: planned; no enrollment implementation or live qualification is claimed.
The [design](storage-enrollment.md) owns CLI intent, identity, credential and
access contracts. This plan owns bounded delivery, dependencies and checks.
The initial target is managed Debian storage and agent VMs, with controllers
holding verified root SSH access to both. Provider provisioning remains owned
by the existing setup engine.

## Implementation map

Confirm actual owners and all callers before editing; the following identifies
integration seams rather than prescribing new monolithic modules.

| Area | Existing boundary to extend |
| --- | --- |
| CLI and saved state | `lib/arg_parser.py`, `lib/config.py`, runtime configuration and setup/patch/history readers and writers; include default handling during deserialization. |
| System lookup and identity | Workspace/saved-system resolution and target-owned setup metadata; do not confuse generic saved systems with Proxmox-only VM lookup. |
| Credentials and transport | `lib/credentials.py`, `lib/git_credentials.py`, SSH helpers and private setup payload handling. Existing workspace passwords remain a supported explicit flow, not a new enrollment dependency. |
| Gogs | `web/gogs_steps.py`, Gogs health helpers and server/web plugin composition. Use supported administrative operations, not direct database writes. |
| SMB | `smb/samba_steps.py`, SMB client mount helpers, `plugins/smb.py` and share-only/panel reconciliation callers. |
| Mutations and recovery | `remote_setup.py`, relevant profile/capability builders, `plugins/post_setup.py`, target setup locks and `lib/operation_state.py`. |
| Documentation and acceptance | Git access, Samba, agent VM, CLI and transaction-recovery guides; existing domain suites, coverage map and disposable release qualification. |

Read complete target files before edits. Use the repository's validators and
the capability helper appropriate to each operation. Keep mutation logic in
its owning package and expose it through target-side setup helpers. Controller
orchestration must not bypass target operation ownership. Version request/state
formats and reject incompatible peers before changing accounts or credentials.

## Milestones

### 1. Close contracts and prove service administration

Resolve the design's repository scope and share/removal precedence. Pin down
optional-argument parsing, explicit username/adoption behavior, account naming,
system identity lifecycle, state schemas and inventory bootstrap. Define which
read-only observations are possible offline and which require SSH.

On disposable services, prove the supported Gogs user/credential operations,
including non-administrator creation, private Git/LFS authentication, secret-safe
inputs and administrative recovery. Determine whether generated passwords or
tokens satisfy the initial account and rotation contract. Check Samba account
and session-revocation behavior. Do not infer live support from mocks.

**Exit:** document the chosen interfaces and limits, settle product decisions
needed by later milestones, and have a concrete recovery/secret-transport
design. Keep raw probes and real endpoints private. A planning commit does not
satisfy this gate.

### 2. Configuration, identities and remote records

Add repeatable enrollment declarations and optional usernames; support setup,
patch, saved reruns, refresh and reconstruction. Add durable target system IDs,
server-owned enrollment/account records, client intent and applied generations.
Implement deterministic lookup and import/bootstrap without copying controller
secrets. Preserve unknown/newer remote state and reject stale writes.
Establish target lock ordering and durable operation ownership here, before
later milestones expose account, credential or membership mutations. Milestone
5 extends recovery and lifecycle coverage rather than introducing those guards
after enrollment is already available.

Suppress only implicit GitHub authentication when Gogs enrollment is selected;
preserve explicit GitHub auth and existing target credentials across every
load/save path. Reject credential-removal conflicts and conflicting explicit
Git/SMB configurations before mutation. Define account sharing and multi-origin
behavior consistently with existing scoped Git helpers.

**Exit:** parsing, serialization, redaction and mocked remote-state tests pass;
no enrollment secret is serialized in controller history or operation records.
Dry runs neither create identities nor contact/mutate targets. Missing and
ambiguous names, unsafe state, unsupported schemas and account collisions fail
with actionable errors. Older configurations without enrollment fields retain
their current behavior.

### 3. Gogs account and client enrollment

Implement server-side account/credential preparation and client HTTPS Git/LFS
configuration through the verified SSH coordinator. Retain the selected
operation identity across account creation and client installation. Retrieve
and validate the advertised origin/certificate and install Git LFS before
requested clones. Add repository creation/grants only under the resolved scope;
never reinterpret `--repo` as permission to create a repository.

**Exit:** a new ordinary account can perform its permitted Git and LFS operations
without manually creating a user/password. Reruns reuse credentials and leave
other accounts/helpers unchanged. Git-only enrollment works without Samba or
controller GitHub authentication. Mismatched identity, inaccessible origin,
wrong certificate and denied repository access remain distinct failures.

### 4. Samba membership and client enrollment

Support a share without initial members, with explicit deny-all behavior.
Reconcile the union of direct grants and enrollment contributions from durable
server state. Update full setup, share-only, panel and local-refresh paths so
none drops enrolled clients. Add per-client SMB account provisioning, logical
share resolution, client packages, private credentials and native automounts.
Preserve mount guards and active-data filesystem restrictions.

**Exit:** two clients can enroll independently without removing each other;
read/write policy is enforced; an empty share denies authenticated users as
well as guests. SMB-only enrollment requires no Gogs. Server refresh needs no
controller credential store, preserves access and does not reset passwords.
Client reboot and temporarily unavailable storage do not redirect writes to
an underlying local directory. Shared account consumers are tracked correctly.

### 5. Recovery, lifecycle and another controller

Implement bounded distributed coordination using target locks and durable
operation records. Define lock order and refusal/recovery behavior for
concurrent setup, share changes and enrollments. Add targeted detach, grant
revocation, explicit rotation and identity/adoption recovery according to the
final interface; preserve data and unrelated consumers.

**Exit:** a fresh controller with an empty workspace credential store, supplied
only discovery bootstrap and root SSH access, reconciles and repairs both
enrollment types. Interruptions and uncertain SSH responses preserve inspectable
state; a second controller can recover supported cases without generating a
replacement operation or credential blindly. Rotation failure is recoverable;
revocation handles active SMB sessions within its stated disruption scope.
Clone/new identity and intentional restore/old generations have separate tests
and documented outcomes.

### 6. Operator workflow and qualification

Publish implemented commands, migration/legacy behavior, credential backup and
restore requirements, inventory bootstrap, readiness reports, targeted removal
and transaction recovery. Show a full two-command Debian VM example with
separate Git and Samba disks and no manual service-user/password creation.
Keep desktop/pairing and provider authorization inputs explicit. Add command
completion and useful diagnostics without displaying secrets.

**Exit:** finish the acceptance matrix below, record sanitized tested scope in
the coverage map, and identify unqualified restore/platform cases. No private endpoint,
secret, user inventory or raw operational evidence belongs in committed plans.

## Acceptance matrix

Use temporary directories and mocked system/SSH calls for automated tests.
Actual services, reboots and disconnects need authorized disposable systems.

| Contract | Automated checks | Disposable acceptance |
| --- | --- | --- |
| CLI and defaults | Optional usernames, flag placement/repetition, invalid arities/values, conflicts, saved round trips, Gogs/GitHub precedence and legacy configuration. | Run actual two-command provisioning with prompts/supplied inputs as documented. |
| Independent services/storage | Plugin composition, correct capability checks, distinct data roots, mount validation and no implied cross-service configuration. | Git-only, SMB-only, combined and separate-server arrangements; reboot with independent data disks. |
| State and identity | Safe ownership/permissions, symlink and schema rejection, IDs across rename, stale generation refusal, imports and ambiguous names. | Reconcile from another controller; rename without account duplication; reject wrong server identity. |
| Secret handling | Canary secrets absent from argv, logs, exceptions, saved configs, history, operation markers and artifacts; private staging cleanup after errors. | Inspect selected credential flows and permissions without publishing raw secrets or logs. |
| Git/LFS | Origin-scoped helper/CA setup, preservation of GitHub and unrelated origins, account collisions and unsupported administrative interfaces. | Push an allowed ref and LFS object, fresh-clone/download with empty local cache, reject unauthorized repository access and invalid trust. |
| Samba | Deny-all empty shares, union/removal of grants, all reconciliation callers, logical export resolution, mount conflicts and shared account consumers. | Two clients, read-only/write grants, direct-user overlap, server refresh, active-session revocation and unavailable-server recovery. |
| Operations | Inject failure before/after secret persistence, account/grant change, client publication, verification and completion; lock contention/deadlock refusal; retained guards and idempotent recovery. | Drop SSH at selected phases; recover with another controller; run competing controller changes without lost grants or credential churn. |
| Lifecycle | Targeted detach versus revoke/delete, rotation generations, last-consumer cleanup, clone versus restore and older/missing state. | Interrupted rotation, intentional restore and new clone enrollment; verify stored data survives removal. |
| Desktop/provider boundary | Enrollment does not override provider login, desktop policy or browser permissions. | Agent doctor plus an actual desktop start/application action; complete selected provider authorization separately. |

## Delivery and validation policy

Commit and push the reviewed planning documents to `main`, then deliver coherent
validated implementation milestones when implementation is authorized. Update
status and remaining acceptance in place, without progress chronologies or
claims that unimplemented flags are available.

For planning-only changes, run `python3 scripts/check_cli_docs.py`,
`python3 scripts/check_doc_links.py` and `git diff --check`, then review the final
diff and privacy boundary. No VM mutation or browser task is required to validate
these documents.

During implementation, register meaningful behavior tests in the owning suites,
run affected selections while editing, and run `make check-ci` for shared
configuration/plugin changes. Follow the [contributor guide](../agents/contributing/README.md)
and [release protocol](../release-qualification.md). Tests and dry runs do not
establish live Git/LFS, SMB, cross-controller, desktop or recovery qualification.

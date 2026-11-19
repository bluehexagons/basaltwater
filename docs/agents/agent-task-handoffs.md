# Follow-ups, access, and handoffs

[Documentation index](agent-tasks.md)

## Request a follow-up with different access

An unattended run can return `follow_up` in its structured report when a check
or repair needs different permissions or authorization. Use `null` when no
follow-up is needed. A request is retained in the receipt and recent run
history; it never creates a task, changes permissions, or submits a privileged
approval. Reports from older runners without this field remain readable.

`history_limit` controls ordinary recent receipt retention. Receipts and
decision files for unresolved follow-ups stay available beyond that limit,
including saved submissions, pending service requests and blocked requests
whose cancellation or submission outcome is unknown. Each task can retain at
most 32 unresolved follow-up records. Once full, the next claimed attempt fails
before workspace preparation or provider/host-operation launch; resolve saved
proposals and inspect uncertain submissions in the privilege portal before
continuing. A no-ID submission with an unknown outcome cannot be reconciled
locally, so inspect it before creating a replacement task. Approved or saved
draft decisions, blocked proposals without uncertain service requests, and
blocked requests with known terminal outcomes return to ordinary retention.
Unreadable or unrecognized decision evidence is preserved fail closed and
counts toward the same limit.

For example, a restricted check can request live diagnostics:

```json
{
  "title": "Verify live maintenance state",
  "reason": "The inspection sandbox denied service-manager and loopback checks; file presence alone cannot establish health.",
  "prompt": "Read the update timers' current state and last results, then check the local T3 readiness endpoint. Record results and inaccessible checks. Do not modify files or services.",
  "directory": "/home/agent",
  "mode": "workspace",
  "workspace": "run-directory",
  "network": true,
  "temporary_files": false,
  "writable_directories": [],
  "operator_requirements": [],
  "service_restarts": [],
  "storage_operation": "none"
}
```

New reports supply these fields. Older requests remain readable and offer
**Review follow-up task** instead of a disabled approval button. Review their
write targets and operator prerequisites, then select **Approve once** in the
form. Missing fields default to no additional grants or prerequisites; the
review requirement is not inserted as an unresolved operator action. Complete
any outside preparation before approval; resolved checklist lines can remain in
the form because **Approve once** confirms them.
Its prompt accepts up to 3800 UTF-8 bytes
and should include concrete targets, actions, verification, and rollback for
repairs. `directory` must be absolute; the normal task validator rechecks its
existence and the account's workspace boundary before preparation.

The main list and run history offer **Approve once**, **Review follow-up task**
and **Block**, alongside the proposal's summary, access and expandable actions.
Approve authorizes the proposed task, confirms that its displayed operator
checklist is satisfied, and queues one attempt after validating access, policy
and separate service prerequisites. The decision preserves the confirmed checklist
and the original receipt remains unchanged. Users do not need to delete an
agent-written request for approval or resolved preparation notes to proceed.
Review opens
an editable form with the source receipt, **Once**, and a one-attempt limit;
opening it does not save or queue anything. The panel saves a private intent
with the exact validated child settings before creating the task. If publishing
the final decision fails, leave the proposal pending and select **Finish saved submission**
from its run history to finish the same submission; recovery does not repeat
service actions. Blocking checks for the exact matching child and refuses while
one is saved, so it cannot mark a queued follow-up blocked. If no child was
found, approval does not create another: the child might have completed and its
controls might have been removed. Inspect retained history before blocking the
proposal or preparing replacement work. Blocking can still finish when no child
is saved. Repeated submission of a proposal returns the
same child task. Blocking prevents that proposal from being launched and attempts
to cancel saved service request IDs. If cancellation or its final state write is
unconfirmed, the blocked proposal offers **Retry service cancellation** in run
history. Approve, block and cancellation-retry actions return to the retained
receipt by its task and run IDs, so unresolved evidence stays accessible after
ordinary run history evicts it. This only retries cancellation for known IDs
without a recorded terminal or claimed state; it never approves, queues a child,
or submits another service request. Cancelled, expired, denied, invalidated,
executing, dispatched, succeeded, failed, and uncertain outcomes are retained
and not retried. Cancellation cannot
stop or undo a request the broker has already claimed. If submission is uncertain
and no request ID was returned, there is no local cancellation retry; check
Privilege approvals before any request retry. The original task keeps its
permissions, schedule and outcome.

Use `service_restarts` for up to four exact service units. Registered targets
use the broker's structured restart operation. For unregistered targets, the
panel reads protected broker policy and requests individual approval of the
exact argv `/usr/bin/systemctl --no-ask-password restart -- UNIT`, through the
broker's existing command-approval contract. It adds no reusable service rule.
The launch check binds the succeeded receipt to the selected operation and
exact arguments. Broker request responses must include a valid 32-character
hexadecimal ID; status, cancellation, and approval responses must echo the
exact requested ID. Missing, malformed, or mismatched IDs leave the result
unconfirmed, so the follow-up is not queued. **Approve once** creates any
missing service requests and opens their review without queuing the task or
reporting a submission error.
Open **Open service approval** in its separate tab, approve using the main
panel login or standalone approval password, then return and select
**Check approval and launch**. The review form
keeps edited settings and offers **Request or refresh service approval** for
explicit retries of failed or expired actions. Repeated approval clicks reuse
existing requests; uncertain submissions are never automatically replayed. A
generic or malformed broker error, including an older broker response, leaves
submission status unknown. Check the privilege portal before retrying; a local
Block cannot cancel a request without its broker ID.
The operator approves in the linked privilege review; before a follow-up can
launch or be saved as a draft, the panel verifies that each exact restart
succeeded. The task agent then verifies the results without repeating the
restart. These are prelaunch prerequisites: if a file repair must happen before
the restart, propose the repair first and a separate restart-verification
follow-up afterward. Before submitting service requests, the review form allows
editing the units and prompt to split an older proposal. Once submitted, block
the proposal to cancel unclaimed requests before preparing a revised task;
removing a unit from the form cannot withdraw a broker action.
Live service diagnostics also need command network
access. Page navigation reads saved request state; explicit submissions check
the broker without a background polling loop.
Unsupported actions or other manual preparation belong in
`operator_requirements`, not a prompt asking another unattended agent to grant
itself permissions. Use this checklist for concrete outside preparation, not
approval of the proposal itself; selecting **Approve once** supplies that
authorization. Confirmation cannot grant extra write paths, override policy, or
satisfy a service restart that has not succeeded through the broker.
Workspace/network access does not supply root approval.
`storage_operation` defaults to `none` for older tasks/proposals. New reports
include it explicitly. A fixed cleanup proposal selects `npm-content-cache`,
`go-build-cache` or `podman-images` and uses the constrained settings documented
in the storage workflow. Approval queues one deterministic host operation;
its prompt cannot supply shell commands or expand its target. `review` only
adds host evidence before the ordinary Codex invocation. Capabilities and compact
task listings expose these selections; CLI JSON and MCP use the same validation.
Do not repeat unchanged blocked proposals; report environmental difficulties
as feedback instead. The receipt is also readable through CLI/MCP; agents must
not activate a request needing additional authorization themselves.

## Report environment difficulties

Task agents return `environment_feedback` in their structured report (empty
when unnecessary; at most 3000 UTF-8 bytes). The runner delivers it with task/run
references to the account-local **Agent inbox** (the `operator` mailbox), outside the
provider's sandbox. A delivery failure is recorded in the receipt without
discarding the feedback. Describe the failed operation, error and expected
behavior without credentials or raw private logs.

T3 and terminal agents can use the local CLI:

```bash
basaltw agent inbox feedback --sender t3-agent 'Describe the environment issue and relevant evidence' --json
```

The writable task MCP integration exposes `basaltwater_operator_feedback` too.
For an otherwise read-only integration, `basaltw agent tasks mcp --allow-feedback`
exposes only this additional mutation. Feedback is self-reported, account-local
evidence; it neither contacts an external developer nor authorizes repairs.
The operator inbox supports **Archive** and **Restore** and keeps archived
messages browsable within its bounded retention.

## Hand off current work from T3 or a terminal agent

Set `initial_checkpoint` to a portable summary of findings, artifact paths,
pending work, and the next action (at most 16000 UTF-8 bytes). It seeds a new
context's first prepared run once. Persistent runs then use their own reports;
later fresh runs ignore it. The original handoff remains in settings and
receipts for provenance. The task prompt supplies authority; checkpoint text
is untrusted evidence and does not grant additional permissions.

`show` reports `initial_checkpoint_pending` before preparation. The initial
checkpoint may change before any attempt or context reset; subsequent changes
require a new task. Updates omitting it preserve it, including older panel
forms. Resetting context or changing persistence does not reseed the handoff.
Existing checkpoints and conversations are preserved when upgrading records
that predate the one-time handoff marker.

For concurrent repository edits, choose an owned worktree. It starts at committed
`HEAD`, so necessary uncommitted changes in the T3 thread must be finished or
deliberately transferred before handing off. The scheduled run is a separate
Codex conversation and does not automatically post its results into the creating
T3 thread. Inspect task history and receipts through the shared API.

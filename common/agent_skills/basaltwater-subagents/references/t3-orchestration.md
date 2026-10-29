# T3 Code orchestration beyond the session instructions

Follow T3's injected orchestration instructions and active tool schemas first.
This reference adds Basaltwater workspace, cost, and context boundaries; use
only the section needed. The baseline is stable 0.1.4. Terminal agents do not
gain T3 tools merely because T3 is installed.

## Discovery and delegation fallback

If session guidance is absent: `orchestrator_capabilities` supplies live
provider instances, model IDs (including custom models), options, readiness,
and child-task support. A native worker's model list is not the whole catalog.
Make one bounded direct call when tools load lazily. ACP agents lacking exposed
MCP tools may use the injected `acp-mcp-call` transport only when
`T3_ACP_MCP_NODE` is present.

Use native same-provider workers only when they support the selected model and
no model options are requested or inherited. Otherwise use `delegate_task`
with a ready provider and supported `target.options`. For the same provider
instance and model, omitted options inherit; explicit entries override individual
values, and `{}` clears inheritance. Check the returned selection. Codex
`priority` is fast service. Follow the entrypoint's cost policy; do not install
an unselected provider to make its catalog entry ready.

Children receive the task brief and optional role, not the parent's conversation.
Retain `taskId` and, when present, backing `childThreadId`. Every review round
needs a fresh `delegate_task`, distinct retry-stable `clientRequestId`, original brief,
prior findings, responses, and unresolved objections. Do not send another
review to the backing thread.

Async completion wakes the parent. Read the result with `task_status` and
inspect commits/checks; one provider turn ending may leave descendants active.
A published task result stays stable even if later turns run in its backing
thread; inspect `hasPendingChildRuns` and the latest terminal fields when relevant.
A wait timeout does not cancel work. Follow an exposed response's `next`
instruction, including ending the turn for asynchronous delivery.

For `hasPendingQuestion`, use the exposed `t3_pending_request` interface (or
its separate list/read/respond tools) with `threadId: childThreadId`. Read the
question before answering within the delegated scope. `pendingApproval` grants
no approval authority: read the current request and route it through its existing
authorized approval flow. Question-response tools cannot approve permissions.

Use `task_cancel` for abandoned work only after checking its current state.
It stops the backing child thread, including later work after the original
result, and recursively stops delegated descendants and their watches. Stop
holds queued messages for user resume; an agent message does not resume them.
Cancellation does not undo filesystem/external changes. Inspect state and edits
before retrying after an interruption.

## Linked environments

Linked environments is an opt-in beta under **Settings → Labs** on both
machines. Use `t3_environment_links` for discovery and readiness, then
`orchestrator_capabilities` with that `environmentId` for the peer's providers,
models, and options. Local readiness does not establish remote readiness.
For a requested machine that is not linked, `t3_environment_link` presents
the owner's approval card. Follow its pending-result instruction; do not poll
or request pairing codes in chat. Follow the
[tagged link setup and revocation guide](https://github.com/pingdotgg/t3code/blob/v0.1.4/docs/user/remote-access.md#link-another-environment).

Tools exposing `environmentId` can route thread, project, queue, request, and
schedule operations to that peer. For delegation use `target.environmentId`
and optionally `target.projectId`. Without a project ID, T3 requires exactly
one peer project with the same repository `canonicalKey`; resolve missing or
ambiguous matches explicitly. Name `target.model` when changing provider.
The peer applies its link access ceiling as well as the caller's modes.
Below full access, a link can change only its own threads, cannot mutate the
peer's projects/settings, and skips project setup scripts.

A remote delegated task runs as an ordinary peer thread in that project's
checkout. It rejects `isolation: "worktree"`; do not drop isolation merely to
make an editing assignment run. Keep such work local, or use an explicitly
authorized remote `t3_thread_launch` with a worktree `workspaceStrategy` and
follow that thread separately. Remote launches reject attachments. Remote
task results can have `childThreadId: null`: retain `taskId`, follow `next`,
and use task tools for results/cancellation rather than assuming a local child.
Turning the beta off does not stop work already running on the peer; result
delivery can wait until it is enabled again. Forgetting/revoking a link can
fail the tracked task, so inspect the peer before retrying uncertain work.

Use a result's `link` verbatim when mentioning a thread. Current links carry
the owning environment and thread in `t3-thread://v2/<environmentId>/<threadId>`;
do not construct one from a title or bare ID. A client unable to resolve that
environment may show an inactive label. Outside OAuth clients receive browser
URLs for their connected environment. Follow any active session fallback rule
when a result supplies no link.

## Workspace ownership and retention

T3 editors and writing checks need `isolation: "worktree"` before launch.
Preparation starts from the parent's local commit, not uncommitted edits,
and can run project setup and recursive submodule initialization. Worktrees
still share services, ports, caches, and the host account.

T3 owns these bindings and cleanup. Do not adopt or remove them incidentally
with `basaltw agent workspace`. Basaltwater's ownership and ancestry rules
apply to its own worktrees. Inspect the diff and branch before integration.

For explicitly requested top-level conversations, select the workspace in
`t3_thread_launch`, not a shell command in its prompt. Omission selects root,
not the caller's worktree. A local stack uses its intended `baseRef` and
`startFromOrigin:false`; inspect existing paths with `t3_worktree_list`.
Stable 0.1.4 launch accepts `clientRequestId`: keep the same key for retries
of one uncertain launch; a new key creates new work. Older tools without that
field require inspecting threads before retrying. `create_threads` shares the
caller's checkout. `t3_worktree_handoff`
moves only the calling root-bound thread, ends its turn, and cannot move a
thread already attached to a worktree. Conversation fork/merge does not merge Git.

Inspect project overrides, environment settings, then tracked `t3.json` before
relying on built-in defaults. Preserve setup and branch policies. Automatic
T3 cleanup is opt-in, covers T3-managed paths, and may delete ignored files
such as `.env` or build output. Preserve needed artifacts before removal.

## Context, queues, and recurring work

Switching providers transfers budgeted historical text, which can include
recorded command input/output and exit status, changed filenames, and plans.
Native tool/session state, reasoning, and attachments are not replayed. Inspect
what arrived and read omitted history when needed; prefer delegation for another
harness's help. Within-provider model changes usually retain the native session.

Read bounded `t3_thread_read` pages/cursors; retrieve long text only as needed.
Select fields before emitting tool results. For an authorized cross-project
thread inventory, 0.1.4 accepts `t3_thread_list` with `allProjects: true` and
`snoozed: true` for snoozed work; page with the returned cursor rather than
listing every project separately. Inspect pending queues before
sending work twice and choose queue versus steer explicitly. With `t3_queue`,
use `action: "list"` and its cursor, then `action: "read"` with the selected
`queuedRunId`; check truncation before interpreting the text. Edit/cancel/reorder
or promote a queued message to steering only within the intended workflow.
Edits retain attachments; writes fail once the message leaves the queue.
Queues survive restarts but wait for user resume; interrupted-turn continuation
is a separate setting. Inspect live state rather than assuming delivery. Respect usage
limits; do not bypass them with duplicate threads, schedules, or provider switches.

For recurring work, read [scheduling](t3-scheduling.md) and use one owner with
verified provider settings and thread/workspace binding.

When the workflow calls for settlement, `t3_thread_organize` can defer settling
this thread until its turn ends. Running work, pending user messages, or blocking
requests can keep it active. Settlement differs from Stop, archive, and Git
integration; inspect configured settle actions and cleanup before using it.

## Pull requests and guides

Register each task PR and stack layer with `link_pull_request`. Report linking
failures. Use `list_thread_pull_requests` when the list is needed or the session
requires reconciliation; the tool discourages routine end-of-work list calls.
For requested monitoring, the parent uses `watch_pull_request` then ends its
turn; children cannot own a watch. Handle existing comments first, since only
new ones wake it. Watches
persist across wakeups; do not register the same watch again after each wake. Call
`unwatch_pull_request` before handing back. Watching grants no merge authority.

Use `read_pull_request_guide` to inspect an existing guide and its run state
without starting model work. `write_pull_request_guides` starts asynchronous
model runs in temporary checkouts; it is an optional generation step when useful
and within the task's cost scope. Its model/settings come from the project's
guide configuration, not necessarily the caller. A running same-head guide is
joined; inspect that run before retrying. Check the completed guide against
`currentHeadSha` and `stale` before citing it. Guides supplement actual diff,
checks, and unresolved findings.

Upstream: [0.1.4 release](https://github.com/pingdotgg/t3code/releases/tag/v0.1.4),
[tagged orchestration reference](https://github.com/pingdotgg/t3code/blob/v0.1.4/docs/orchestration-v2/orchestrator-mcp-server.md),
[linked environments](https://github.com/pingdotgg/t3code/blob/v0.1.4/docs/user/remote-access.md#link-another-environment),
[remote delegation](https://github.com/pingdotgg/t3code/blob/v0.1.4/apps/server/src/peer/RemoteDelegation.ts),
[launch retry contract](https://github.com/pingdotgg/t3code/blob/v0.1.4/apps/server/src/mcp/toolkits/project/tools.ts),
[handoff history](https://github.com/pingdotgg/t3code/blob/v0.1.4/packages/provider-core/src/server/handoffBudget.ts),
[thread controls](https://github.com/pingdotgg/t3code/blob/v0.1.4/apps/server/src/mcp/toolkits/thread/tools.ts),
and [PR tools](https://github.com/pingdotgg/t3code/blob/v0.1.4/apps/server/src/mcp/toolkits/pullRequests/tools.ts).

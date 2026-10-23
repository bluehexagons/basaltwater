# T3 Code orchestration beyond the session instructions

Follow T3's injected orchestration instructions and active tool schemas first.
This reference adds Basaltwater workspace, cost, and context boundaries; use
only the section needed. The baseline is stable 0.1.2. Terminal agents do not
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
Retain `taskId` and backing `childThreadId`; every review round needs a fresh
`delegate_task`, distinct retry-stable `clientRequestId`, original brief,
prior findings, responses, and unresolved objections. Do not send another
review to the backing thread.

Async completion wakes the parent. Read the result with `task_status` and
inspect commits/checks; one provider turn ending may leave descendants active.
A wait timeout does not cancel work. Use `task_cancel` for abandoned tasks;
cancellation does not undo filesystem/external changes. After interruption,
inspect state and edits before retrying. A child question is not permission.

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
Launch has no idempotency key: inspect threads before retrying an uncertain
launch. `create_threads` shares the caller's checkout. `t3_worktree_handoff`
moves only the calling root-bound thread, ends its turn, and cannot move a
thread already attached to a worktree. Conversation fork/merge does not merge Git.

Inspect project overrides, environment settings, then tracked `t3.json` before
relying on built-in defaults. Preserve setup and branch policies. Automatic
T3 cleanup is opt-in, covers T3-managed paths, and may delete ignored files
such as `.env` or build output. Preserve needed artifacts before removal.

## Context, queues, and recurring work

Switching providers transfers a budgeted message selection without prior
reasoning, tool results, or attachments. Prefer delegation for another
harness's help. Within-provider model changes usually retain the native
session; inspect actual settings/state.

Read bounded `t3_thread_read` pages/cursors; retrieve long text only as needed.
Select fields before emitting tool results. Inspect pending queues before
sending work twice and choose queue versus steer explicitly. Queues survive
restarts but wait for user resume; interrupted-turn continuation is a separate
setting. Inspect live state rather than assuming delivery. Respect usage
limits; do not bypass them with duplicate threads, schedules, or provider switches.

For recurring work, read [scheduling](t3-scheduling.md) and use one owner with
verified provider settings and thread/workspace binding.

## Pull requests and guides

Register each task PR and stack layer with `link_pull_request`; reconcile
`list_thread_pull_requests`. Report linking failures. For requested monitoring,
the parent uses `watch_pull_request` then ends its turn; children cannot own a
watch. Handle existing comments first, since only new ones wake it. Call
`unwatch_pull_request` before handing back. Watching grants no merge authority.

When exposed, PR guide tools supply an asynchronous reading aid. Read the
finished guide and its staleness before citing it; retain actual diff/check
evidence and unresolved findings.

Upstream: [0.1.2 release](https://github.com/pingdotgg/t3code/releases/tag/v0.1.2)
and [tagged orchestration reference](https://github.com/pingdotgg/t3code/blob/v0.1.2/docs/orchestration-v2/orchestrator-mcp-server.md).

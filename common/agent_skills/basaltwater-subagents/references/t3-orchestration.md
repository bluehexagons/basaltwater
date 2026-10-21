# T3 Code native orchestration

Use this reference inside T3 Code when delegation or thread coordination is
authorized. It follows stable 0.1.1; inspect the active tool schemas for the
connected environment. Tool names may have a harness-specific MCP prefix.
Terminal agents do not gain these tools merely because T3 is installed.

## Discover and choose workers

Call `orchestrator_capabilities` for the live provider instances, model IDs,
custom models, options, readiness, and child-task support. A native subagent
tool's model list is not the whole T3 catalog. If tools load lazily, make one
bounded direct capabilities call before reporting them absent. For ACP agents
that lack exposed MCP tools, use T3's injected `acp-mcp-call` transport only
when `T3_ACP_MCP_NODE` is present; follow the session's supplied invocation.

Prefer native same-provider subagents when they support the selected model.
Use `delegate_task` for unsupported native selections, cross-provider work,
or explicitly T3-owned children. Choose a ready provider instance and explicit
supported model/options. Apply the model-selection reference's cost policy;
do not inherit fast service, `max`, or `ultra` without the user's selection.
Do not install unselected providers just to make catalog entries ready.

Switching providers mid-thread transfers a budgeted selection of messages,
omitting prior reasoning, tool calls/results, and attachments. Prefer delegation
for another harness's help. Changing models within a provider usually retains
its native session; inspect the actual selected settings and session state.

## Delegate and collect results

Give each child a self-contained brief: objective, relevant findings and paths,
project/skill instructions, allowed files, workspace, access and cost limits,
acceptance checks, and required handoff. Children receive the task prompt and
optional role, not the parent's conversation. Do not assume inherited browser
attachments or loaded skills.

Use a distinct `clientRequestId` for each task, stable across retries of that
same request. Retain its `taskId` and `childThreadId`. Every independent review
round needs a new `delegate_task` with the original brief, prior findings,
responses, and unresolved objections. Do not use `t3_thread_send` on the backing
`childThreadId` for another delegated review round.

For `mode: "async"`, continue independent work or end the turn; completion wakes
the parent. Do not repeatedly poll while awaiting that wake. A bounded wait can
time out without cancelling the child. Use `task_status` to distinguish active
work, waiting for descendants, and an available result; one provider turn ending
does not prove the whole nested task finished. Read the published summary and
inspect commits and validation before integration. Use pending-question tools
to list/read/respond to child questions when exposed. Answering a question does
not approve a permission request.

Use `task_cancel` for abandoned work; it stops the delegated task and descendants
and holds queued work. Cancellation does not undo filesystem changes or external
actions. After interruption or restart, inspect recorded task state and retained
edits before retrying; do not duplicate still-active work.

## Bind workspaces before editing

`delegate_task` defaults to a shared checkout. Read-only reviewers may share it;
editors and checks that write files need `isolation: "worktree"`. T3 creates the
checkout from the parent's local commit and runs project setup. Uncommitted
edits are not copied. Commit or explicitly hand off required changes first.
Worktrees still share services, ports, caches, and the host account.

T3 owns those worktree bindings and lifecycle. Do not adopt or remove them with
`basaltw agent workspace` as incidental cleanup. Basaltwater ownership records
apply to worktrees created or deliberately adopted through Basaltwater. Inspect
the actual diff and branch before authorized integration with either owner.

Use `t3_thread_launch` only when the user requests a separate/new/top-level
conversation. Put work in `message` and choose `workspaceStrategy` explicitly:

- `{"type":"worktree","baseRef":"feature/base","branch":"feature/next","startFromOrigin":false}`
  starts from local commits; use `true` only when fetching from origin is intended.
- `{"type":"existing_worktree","worktreePath":"/absolute/worktree","branch":"feature/next"}`
  binds an inspected existing checkout, including a Basaltwater-owned one.
- `{"type":"root"}` uses the project root. Omission also selects root; it does
  not inherit the caller's worktree.

Inspect existing paths with `t3_worktree_list`. A shell `cd` or `git worktree add`
in the launch prompt does not update T3's binding. `create_threads` is only for
requested batches intentionally sharing the caller's checkout; it has no
workspace override. `t3_thread_launch` has no idempotency key: save the returned
thread ID, inspect preparation via read/wait, and check `t3_thread_list` before
retrying an uncertain launch.

To move the current root-bound thread, inspect `t3_worktree_status`, then use
`t3_worktree_handoff` as the last call with a continuation prompt. It ends the
turn and moves this thread; it cannot move another thread or a thread already
attached to a worktree. Fork/merge-back transfers conversation context, not Git
changes. Review and integrate code separately.

Inspect project overrides, environment settings, and tracked `t3.json` before
relying on defaults; that is their precedence order, followed by built-in
defaults. Project setup/actions and recursive submodule initialization can run
while preparing worktrees. Preserve the project's setup policy and branch
naming rules; do not add a second bootstrap script or overwrite overrides
incidentally. Automatic T3 cleanup is opt-in and only covers T3-managed paths.
Its local-file policy can allow deletion of ignored files such as `.env` and
build output. Inspect retention and preserve needed artifacts before settlement
or removal; Basaltwater's ancestry-based cleanup rules are separate.

## Queues and context

Use bounded `t3_thread_read` pages and cursors for omitted messages or activity.
Retrieve long text only as needed. Check pending queue entries before sending
duplicate work. Choose explicit queue versus steer behavior; automatic follow-up
behavior is configurable. Queue tools can inspect, edit, cancel, reorder, or
promote a message to steering. Persistent queues survive restarts but wait for
user resume. Interrupted-turn continuation is a separate General setting;
inspect live state instead of treating either feature as a delivery guarantee.
Usage-limited work can wait for reset; avoid bypassing limits with duplicate
threads, schedules, or provider switches.

For recurring thread work, read [T3 scheduling](t3-scheduling.md). Use a single
scheduling owner and verify the selected provider settings and target binding.

## Pull requests and review guides

Register every PR created or worked on with `link_pull_request`, including each
stack layer, and reconcile `list_thread_pull_requests` before finishing. CLI Git
or `gh` commands do not make this association. Do not link unrelated background
PRs. Report a failed linking call rather than claiming success.

When asked to monitor a PR, the parent uses `watch_pull_request` and ends the
turn; children cannot own a watch. Handle existing comments first, since only
new comments wake the watcher. T3 wakes the parent for checks, comments, or
conflicts; do not run a separate polling loop. Call `unwatch_pull_request`
before handing the work back to the user.
Watching does not grant merge or publication authority.

When `write_pull_request_guides` and `read_pull_request_guide` are exposed, use
them for a concise reading path through the relevant changes. Writing starts
asynchronous work; read its result and staleness before citing a guide.
A guide neither verifies behavior nor supplies approval; retain the
actual diff, check results, and unresolved findings in the handoff.

Upstream baseline: [0.1.1 release](https://github.com/pingdotgg/t3code/releases/tag/v0.1.1)
and [tagged orchestration reference](https://github.com/pingdotgg/t3code/blob/v0.1.1/docs/orchestration-v2/orchestrator-mcp-server.md).

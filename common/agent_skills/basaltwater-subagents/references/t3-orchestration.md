# T3 Code integration boundaries

Follow the active T3 instructions and live schemas. For native delegation,
thread controls, queue operations, and tool results, use the
[tagged T3 tool definitions](https://github.com/pingdotgg/t3code/tree/v0.1.13/apps/server/src/mcp/toolkits).
Basaltwater's installed guidance does not grant access to those tools.

## Discovery and delegation fallback

Use the live provider catalog when the task calls for delegation. Discovery
reflects the selected environment and provider account. Authentication,
runtime modes, and options may differ on another machine. Apply the subagent
skill's cost policy to the selected options. A fresh worker needs a
self-contained brief, relevant paths and bounded evidence. If the active
session supplies an ACP bridge, use its instructions for reaching T3 tools.

## Linked environments

Use the [T3 linked-environment guide](https://github.com/pingdotgg/t3code/blob/v0.1.12/docs/user/remote-access.md#link-another-environment)
for enrollment and revocation. The peer's grant limits operations in addition
to the caller's runtime mode. Inspect provider readiness on the peer and
resolve ambiguous project matches explicitly.

Remote delegated tasks use the peer's project checkout. For an editing task
that requires isolation, use a local bound worktree or an explicitly requested
remote thread with a T3-bound worktree. Disabling linking can leave work running
on the peer. Check the peer before retrying after a disconnect or revocation.

In 0.1.12, the **Environments** map shows known routes and peer links.
Links are directional, and establishing a link through the map creates a
separate grant on the destination. A linked thread can stream read-only via
the connected environment. **Connect to interact** requires the client to
reach the destination directly. One-step pairing also requires a full-access
link and permission to manage access on the connected environment. Verify
which environment owns a thread, route, or grant before acting on it.
[Linked thread access](https://github.com/pingdotgg/t3code/blob/v0.1.12/docs/user/remote-access.md#read-a-linked-machines-threads)

## Workspace ownership and retention

T3 binds its worktrees at launch. A shell directory change does not rebind a
thread. Preparation starts from a commit, while worktrees still share the
host account, services, ports, and caches. Keep each editing task isolated
and inspect the branch and diff before integration.
T3 0.1.13 supports local worktree isolation for delegated tasks in projects
with several repositories. In a plain folder, the child starts from each
repository's default branch. In a Git-rooted project, T3 resolves the parent's
commit and uses its branch name for the other repositories when that branch
still points at the same commit. Verify the selected checkouts and bases, and
inspect shared paths before assigning isolated edits.
[Delegation implementation](https://github.com/pingdotgg/t3code/blob/v0.1.13/apps/server/src/mcp/OrchestratorMcpService.ts)
· [Multi-repository worktrees](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-project-development/references/t3-workflows.md#multi-repository-projects)

For an explicitly requested new thread, 0.1.11 supports `clientRequestId`
on `t3_thread_launch`. If the initial call included a key and the reply was
lost, retry the same request with that key from the same provider session.
If the initial call had no key, look up the thread before retrying. Follow
the active tool schema for available launch options.

Use T3's own lifecycle for its worktrees and scratch. Reserve
`basaltw agent workspace` for Basaltwater-managed paths. Inspect the owner's
[retention settings](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/project-settings.md#storage-cleanup)
before settling or removing work. Enabled retention can remove ignored files,
captures, and projectless folders whose contents have no Git recovery.

## Context, queues, and recurring work

Provider switches can transfer bounded conversation text. Recheck omitted
evidence and tool access in the receiving session. Use **Restart agent
session** to reload guidance. An authorized `/clear` starts a fresh provider
context, so save relevant state in a durable checkpoint first.

Read only needed history and queued messages. Reconcile uncertain delivery,
approval, or mutations before retrying. For existing schedule migration or
recovery, use [scheduling](t3-scheduling.md). Independent Basaltwater jobs
start with their own permissions and context.

## Pull requests and guides

Follow the active T3 instructions for linking and watching PRs. A missing
approval response does not resolve a workflow approval hold. Check the awaited
run and current PR head before reporting readiness. PR guides can launch
separate model runs under the project's guide settings. Read an existing
guide first and compare its recorded head to the current head before citing it.
T3 0.1.11 adds native PR mutations and CI rerun tools. For an authorized
mutation, verify the target PR, head, check run, and any affected stack
layers. Link and watch calls track state. See the
[native PR tools](https://github.com/pingdotgg/t3code/blob/v0.1.11/apps/server/src/mcp/toolkits/pullRequests/tools.ts).

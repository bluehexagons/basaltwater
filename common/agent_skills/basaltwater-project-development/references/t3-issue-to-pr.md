# T3 issue-to-PR workflow

Use the live issue, pull request, and CI tools exposed by the active T3
connection. Tracker and forge access depend on that connection and account.
Remote writes require the applicable grant and upstream account permissions.

1. Read the assigned issue with `read_issue`, including its latest comments
   and linked PRs. Use `list_issues` when the user asks to choose from a project
   issue list. Attach the issue to the active thread with `link_issue`. Confirm
   the intended repository and base using the thread binding and Git state.
2. Run the repository's canonical checks in that workspace. A T3 project
   action can invoke the same commands. Use the target revision for any sparse
   worktree preflight. Keep check outcomes with the change so a reviewer can
   reproduce them.
3. When authorized to open a PR, push the tested branch and use
   `pull_request_create` with the intended base, a concise rationale, and the
   checks run. Inspect a returned existing PR before proposing further changes.
   Follow the session's `link_pull_request` or `link_pull_requests` instructions
   for the resulting URL and any other PRs you took over or pushed to.
4. Use `pull_request_read` for the current head, checks and reviews. Use
   `ci_logs` for a failed check, and `ci_rerun` when authorized and the evidence
   supports retrying. Confirm that checks cover the head you intend to report
   or merge. Follow the session's `watch_pull_request` workflow when the user
   asks for monitoring or completion depends on pending checks or reviews.

Linking an issue records thread context. Keep tracker transitions, public
replies, and merges within the requested scope. T3 can settle a thread when a
linked PR merges, so read its worktree action settings before relying on logs
or a long-running command after merge.

If an expected tool or account is missing, check the selected T3 environment
and the thread repository's remote. Connect the account on the environment
host through **Settings → Source control & issues**, then choose **Rescan**.
For GitHub, T3 uses a saved token, the server's `GH_TOKEN`, or a signed-in
GitHub CLI version 2.81.0 or newer. If using the CLI, run `gh --version` and
`gh auth status` as the T3 service account. Git clone and push use their own
Git transport credentials. Connect Linear in the same settings and select its
team for the project. Remote tracker writes require `source-control:write`.

[T3 source control](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/source-control.md)
· [Project actions and worktrees](t3-workflows.md#project-commands-and-worktree-setup)

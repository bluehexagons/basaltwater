# A complete managed-worktree lifecycle

Choose the integration branch and use normal branch merges before assigning
editing work. The parent stays in the primary checkout and owns integration and
cleanup. This example splits a website task into user guides and historical
records; a read-only reviewer can inspect the primary checkout after merging.
These commands work in both bash on Debian and fish on CachyOS. Supply the
actual repository path when the workspace differs from `~/repos`.

## Prepare and assign

Inspect the primary checkout and preserve unrelated changes. Choose a committed
base; new worktrees do not include uncommitted changes. Stop competing writes
before committing, integrating, or changing the primary branch.

```bash
git -C ~/repos/PROJECT status -sb
basaltw agent workspace create ~/repos/PROJECT user-guides --base HEAD --json
basaltw agent workspace create ~/repos/PROJECT historical-records --base HEAD --json
```

Save each returned absolute `path`, `branch`, and `base_commit`. Assign the first
agent only `docs/guides/`, and the second only `docs/history/`. Specify the desired
result and validation. Reserve navigation, shared configuration, package
manifests, generated output, and lockfiles for the parent unless explicitly
assigned. Give workers their worktree as the working directory. A tool that
cannot set a working directory must receive explicit absolute paths for edits,
`git -C /absolute/returned/path` for Git operations, and an explicit working
directory for builds and tests. Absolute edit paths alone do not isolate Git,
package-manager, or build commands. If the tool cannot direct those commands to
the assigned checkout, keep the task read-only or use another supported tool.

Worktrees isolate repository files. Git configuration and hooks, tool settings,
caches, desktop sessions, and listening ports can remain shared. Assign distinct
ports and output paths for concurrent previews, and coordinate commands that
change shared state.

Keep task prompts bounded. Workers should report a needed change outside their
scope rather than edit another worker's files. Limit concurrency to the host's
available headroom; the successful light website task is not evidence that heavy
builds or many concurrent Electron jobs will fit.

## Worker handoff

Each editor validates and commits only its assigned files, then returns:

```text
Worktree: /absolute/returned/path
Branch: agent/user-guides
Base commit: <base_commit returned at creation>
Final commit: <full git rev-parse HEAD>
Changed files: <paths from git diff --name-only BASE_COMMIT..HEAD>
Validation: <commands, outcomes, and any checks not run>
Unresolved concerns: <remaining issues or none>
Model: <exact ID, reasoning effort, standard service, selection rationale>
```

Include all commits if there are several. Commit references and validation
outcomes are evidence the parent reviews, not a replacement for inspecting the
actual diff. Workers leave their worktrees intact.

## Review, merge, and validate

The parent checks the returned worktree state, changed paths, and each commit:

```bash
basaltw agent workspace status GUIDE_WORKTREE --json
basaltw agent workspace status HISTORY_WORKTREE --json
git -C ~/repos/PROJECT log HEAD..agent/user-guides --oneline
git -C ~/repos/PROJECT log HEAD..agent/historical-records --oneline
git -C ~/repos/PROJECT diff HEAD...agent/user-guides
git -C ~/repos/PROJECT diff HEAD...agent/historical-records
git -C ~/repos/PROJECT merge --no-ff --no-edit agent/user-guides
git -C ~/repos/PROJECT merge --no-ff --no-edit agent/historical-records
```

The explicit merge preserves task ancestry after another branch is integrated.
A fast-forward merge also works when possible. Resolve conflicts in the
integration checkout and inspect their effect on both tasks. Then run the
project's combined checks/build, request a read-only review if useful, and
record each model outcome only after assessing the result. A reviewer must not
run a formatter, install, build, or test that writes to the shared checkout.
Use an isolated worktree when its review needs those operations. Keep the
reviewer's branch unchanged if it only runs checks; a writing review is an
editing task and needs the same handoff and integration as other editors.

Push only when authorized, to the explicit destination, and verify the remote
commit. If the remote advanced, integrate and validate it before retrying.

## Cleanup

After the primary checkout contains both task branches and validation passes:

```bash
basaltw agent workspace remove GUIDE_WORKTREE --dry-run --json
basaltw agent workspace remove GUIDE_WORKTREE --json
basaltw agent workspace remove HISTORY_WORKTREE --dry-run --json
basaltw agent workspace remove HISTORY_WORKTREE --json
basaltw agent workspace list ~/repos/PROJECT --json
```

Use the actual returned paths in place of the uppercase placeholders. Removal
checks ancestry against the primary checkout's current `HEAD`, so keeping the
integration commit only on another branch or the remote is insufficient.
End worker sessions and stop task-owned previews or builds before removal; a
clean Git status does not show processes still using the directory. Untracked
output blocks removal; ignored output does not. Copy any deliverables that must
survive before removing the worktree.

## If cherry-pick or squash was chosen

These copy changes without making the original task commits ancestors. Managed
cleanup deliberately refuses the branch even if its patch is present. Retain
the worktree until the parent reviews and performs a normal merge of the source
branch. Review that merge's result and rerun affected validation: it may produce
no file changes, or conflicts and additional changes. Never automatically use
`merge -s ours`, force-remove the worktree, or force-delete the branch to mark
unverified work as integrated.

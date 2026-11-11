# Coordinate workstation measurements

## Reserve shared resources

Cooperating Linux agents running as the same account can reserve GPU profiling
and shared build output roots around a foreground command:

```bash
basaltw agent reservations run --resource gpu:all \
  --build-output ~/repos/my-game/build --owner renderer-agent --run-id baseline-01 \
  --timeout 1800 -- ./scripts/measure-renderer
basaltw agent reservations status --resource gpu:all \
  --build-output ~/repos/my-game/build --json
```

Each run records an owner, run ID (generated when omitted), supervisor PID and
UTC start time in private account state. All requested locks are acquired
without waiting before execution; a conflict reports the current owner/run and
releases any partial acquisition. Up to 32 keys are accepted. Commands receive
literal argv, closed stdin, streamed output and a 1–86400 second process-group
deadline (default one hour). Their exit status is preserved; timeout returns
124 and interruption returns 130.

Use one agreed GPU name, such as `gpu:all`, for every participating profiler.
Different GPU names do not conflict. Build keys use canonical absolute output
paths, including symlink resolution. Every writer must reserve the same output
root for the entire build and consumption interval. Parent/child directories
are different keys; choose the common root deliberately. Isolated worktree
outputs remain preferable when the project supports them.

Reservations are advisory and account-scoped, not cross-account or cross-host
enforcement. They cannot stop uncooperative tools, desktop activity or hardware
contention. They require local Linux filesystem `flock` semantics; do not move
the registry to a network share. State lives under the account's fixed
`~/.local/state/basaltwater/reservations`, independent of XDG overrides.

The supervisor closes locks after success, failure, timeout, Ctrl-C or SIGTERM.
Lock descriptors are also passed to the child, so a supervisor crash does not
release a reservation while the child retains those descriptors. Commands must
stay in the foreground and retain inherited descriptors; daemonized work,
children that close them, and escaped process groups require project-owned
supervision. Never delete lock files or force-release a running reservation:
that could create a second lock inode for the same resource. Status checks the
kernel lock; an old metadata record alone does not mean a resource is busy.
Completed records remain as last-owner evidence and are replaced on reuse.

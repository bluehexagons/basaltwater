# Controller setup source preparation

`lib.build_setup_sources` prepares pinned inputs for the public build-on setup
controller. It does not connect that controller, run builds, write deployment
caches, select a provider, or contact a target or builder. The parent controller
owns planning, delivery, credentials and the directory lifecycle.

```python
from __future__ import annotations

from tempfile import TemporaryDirectory

from lib.build_setup_sources import prepare_setup_sources

with TemporaryDirectory() as destination:
    prepared = prepare_setup_sources(
        [["one.example,two.example", "https://example.com/project.git#tag=v1"]],
        destination,
    )
    # Complete planning/delivery while these files remain available.
```

The API is
`prepare_setup_sources(deploy_specs, destination, *, limits=DEFAULT_SOURCE_LIMITS, runner=None)`.
It returns `list[PreparedSource]`, preserving deployment order. The frozen
dataclass fields are `destination: str` (one `DOMAIN[/PATH]` spec),
`manifest: Manifest`, `source: dict[str, str]` (revision, snapshot and manifest
digests), and `source_path: str` (absolute snapshot filename). Each result has a
detached manifest and identity mapping so planning one destination cannot alter
another result. Identical inputs share the same read-only snapshot file.

Inputs use the standard list of two-string deployment pairs, with comma-separated
destinations expanded to 1 through 16 total plans. Existing deployment, host,
route and repository-selector validators run before preparation writes or Git
commands. Filesystem deployment destinations are unsupported. Selectors follow
`parse_repository_source`: absent means the clone's default HEAD; explicit
selectors are `#branch=NAME` or `#tag=NAME`. Revision expressions are unsupported.

The destination must already exist, be empty, caller-owned, and exactly mode
`0700`. Its components must contain no symlinks. Private clones, controller Git
home, template, source temporaries and snapshots live beneath it. Snapshot files
are published mode `0400` by the existing [source backend](build-source.md). The
caller must keep the directory private and prevent concurrent mutation. On any
failure the directory can contain partial results; there is no automatic retry,
replacement or cleanup. A second call requires a fresh empty directory. Dry-run
planning can use this exact function inside a temporary-directory context.

## Pinning and manifest identity

Each distinct repository URL is cloned once with full history and tags, using a
bare, no-checkout clone into a private `.git` directory. Local configuration then
enables the worktree. All requested refs, including the original default HEAD,
resolve to complete commit IDs before any detached checkout. Explicit heads
and tags first use `show-ref --verify --hash` to require their exact namespaces.
Only the returned full object ID is then peeled with `rev-parse --verify
OBJECT_ID^{commit}`, preserving annotated tags without namespace fallback.
A missing branch cannot resolve through a tag such as
`refs/tags/refs/heads/NAME`, and a missing tag cannot resolve through a branch.
Default HEAD continues to use `rev-parse --verify HEAD^{commit}`. There is no
shallow clone, LFS fetch, submodule recursion, branch guessing or subsequent
remote refresh.

Each distinct URL/commit pair gets one checkout and one
`create_source_snapshot()` call. Multiple destinations and different selectors
resolving to that pair reuse its snapshot. The manifest must be an explicit
tracked `basaltwater.json`; no project-type or manifest inference occurs.
Preparation reads at most 256 KiB with no final symlink traversal, parses strict
JSON and the existing manifest schema from those exact bytes, and computes their
digest. Before returning, both that digest and the pinned revision must match
the snapshot backend's identity. This prevents planning from one manifest and
shipping a different committed manifest. Built-in checkout conversions affecting
manifest bytes are refused if their digest differs from committed bytes.
The backend retains its existing refusals for filters, LFS pointers, submodules,
links, unsafe paths, oversized files and dirty or changed checkouts.

## Git execution boundary

The initial transport slice accepts public HTTPS repositories with no embedded
credentials or query parameters. Private repositories, SSH, local paths, other
transports and HTTP redirects are unsupported. A clone failure explicitly names
the public-only credential boundary; no token is invented or requested.

Every command, including snapshot metadata commands, uses an allowlisted
environment: private HOME/XDG configuration and temporary directories, disabled
system/global Git configuration and attributes, no terminal prompts, no SSH
agent/proxy/config environment inheritance, and disabled askpass/SSH commands.
Configuration disables credential helpers, fsmonitor, hooks, external global
attributes and automatic maintenance. Clone uses the private empty template and
commands disable hooks before any checkout. Repository filter-driver
configuration cannot arrive from the caller's Git configuration or templates.
Source filter attributes are subsequently refused by the snapshot backend.

Commands run in isolated process groups with null stdin unless the source backend
supplies bounded input. Diagnostics are discarded. Positive stdout bounds use a
private bounded spool; `max_output_bytes=0` discards stdout through `DEVNULL`
and returns `b""`, including Git transaction acknowledgments. Individual output
files retain their `RLIMIT_FSIZE`, and process-group
termination follows timeout, interruption or an observed storage failure.
A fresh isolated Python interpreter applies the file limit before executing
Git; no Python `preexec_fn` runs alongside the storage monitor thread.
Each command is bounded by `SourceLimits.command_timeout_seconds` and any outer
CI/CD deadline. Preparation additionally has an overall command deadline of
`(1 + expanded_plan_count) * command_timeout_seconds`. Snapshot creation retains
its own single-operation deadline. The optional runner uses the existing source
backend's runner contract; it is intended for fixtures and replaces native Git
execution, while validation and storage checks remain active.

## Aggregate controller storage

The entire destination shares one budget, independent of repository count:

```text
bytes = max_source_bytes + 2 * max_history_bytes
        + max_snapshot_bytes + 2 * max_metadata_bytes
entries = 4 * max_entries + 128
free-space margin = max_metadata_bytes
```

All terms come from `SourceLimits`, whose fixed ceilings cannot be raised.
Default aggregate bytes are 11 GiB plus 128 MiB, and entries are 400,128.
Accounting includes directories and uses the larger of logical size and
allocated blocks for each entry. Traversal does not follow symlinks. Retained
snapshots and all prior clones continue to count; this can conservatively refuse
a later plan even when each individual source fits its own snapshot limits.

Before clone, checkout and snapshot preparation, both aggregate remaining space
and current filesystem free space must cover their respective headroom:
source plus history, source alone, or snapshot plus history. Free-space checks
also retain the margin. Checks run between commands, during native Git command
polling, and in a periodic monitor throughout preparation (including Python
snapshot writes). The monitor latches failures, preventing subsequent commands
and successful return. An active Git process group is terminated by its runner.
Clones and other Git-created files are additionally subject to the history-file
limit; the snapshot backend supplies its history bundle limit explicitly.

This is sampled admission and enforcement, not a hard filesystem reservation or
quota. Scans, process cleanup and in-progress writes permit temporary overshoot;
other controller processes can consume free space after admission. Concurrent
preparations need parent-level coordination or an independent filesystem quota.
Neither the directory nor this API isolates hostile processes sharing its UID.

The deployment suite's existing `tests/test_build_*.py` wildcard includes the new
tests. They use temporary directories, mocked Git and mocked process/disk
observations, including actual snapshot framing through the existing Git
fixture. No native Git, network, SSH, provider or build command is exercised.
Public repository cloning, Git version compatibility and native resource-limit
behavior require separate parent qualification.

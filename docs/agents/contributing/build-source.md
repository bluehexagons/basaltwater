# Shared build source snapshots

`lib.build_source` freezes a clean tracked Git commit for a shared build. It
does not select a worker, connect over SSH, read build configuration, or admit a
job. The controller uses the returned source identity in the
[`build_protocol` request](build-contracts.md#build-requests).

## APIs

```python
source = create_source_snapshot(repository, destination)
checkout_path, history_bundle_path = unpack_source_snapshot(
    snapshot_path,
    private_output_directory,
    expected_source=source,
)
```

`create_source_snapshot()` requires an existing worktree and a new snapshot file
whose parent already exists, is owned by the calling UID, and has no group or
other permissions. It rejects a dirty index/worktree, visible untracked files,
shallow repositories, local graft metadata, hidden/sparse index entries, and a
destination inside the repository. It hashes each tracked worktree file against
its committed Git blob ID with bounded, no-follow reads, so repository stat-cache
settings cannot hide same-size edits. When raw worktree bytes differ, a private
Git metadata view normalizes them using the pinned commit's attributes, bounded
local `.git/info/attributes`, and validated built-in conversion settings. This
accepts clean `text`/`eol`, `ident`, and `working-tree-encoding` checkouts. The
view has no original filter-driver configuration; external filters are refused
before Git status runs. Ignored local files are not copied. Source bytes always
come from the resolved full `HEAD` object via Git plumbing, never from the live
worktree. It also checks that `HEAD` and the dirty state remain unchanged before
publishing.

The return value is exactly the build-protocol `source` mapping:

```json
{
  "revision": "<full Git object ID>",
  "snapshot_sha256": "<digest of the complete framed snapshot>",
  "manifest_sha256": "<digest of committed basaltwater.json bytes>"
}
```

The manifest hash is computed from the exact tracked `basaltwater.json` bytes.
The snapshot hash covers the revision, entry metadata, every committed file,
and the separate Git history payload. Neither digest appears in the bytes it
hashes, so the source identity has no circular digest. The return value and
errors contain no file contents, tag names, local Git configuration, or
history object contents.

`unpack_source_snapshot()` requires the expected source record and a new output
directory with a private caller-owned parent. It returns paths to the source
checkout and the separate `history.bundle` file. The extractor creates only
regular files and directories, normalizes files to `0644`/`0755`, directories
to `0755`, and the private output root to `0700`. The snapshot file and history
bundle are read-only after publication. The worker must materialize Git state
from the verified bundle under its unprivileged build UID; extraction itself
never runs Git or interprets Git metadata as configuration. The caller must
securely hand ownership/access for the published directory and bundle to that
UID; this API does not change ownership.
`lib.build_git.prepare_history()` performs that unprivileged materialization
inside the supervised service: local unbundle, pinned HEAD and tag installation,
index reset, then a clean-tree check. It does not fetch or run checkout hooks.
Version commands can inspect the retained commits and tags without historical
file blobs. Git's [bundle command](https://git-scm.com/docs/git-bundle) requires
the filter revision option after the destination filename.

Both functions accept a `SourceLimits` value for tighter per-call bounds. The
optional `runner` argument on `create_source_snapshot()` accepts a callable
with keyword arguments `cwd`, `env`, `input_data`, `timeout`,
`max_output_bytes`, and optional `file_size_limit`; it returns bounded stdout
bytes and is intended for mocked Git tests. Production commands use a minimal
environment, bounded spooled output, process-group timeout cleanup, disabled
hooks/fsmonitor/optional locks, and no network command.

## Snapshot format and retained Git metadata

The format is a byte-framed sequence, not a tar archive:

1. `BASALTWATER-SOURCE\0\1\n`, full revision, and entry count.
2. Directories in parent-first order, followed by files in path order. Each
   record has a type, normalized executable flag, UTF-8 relative path, and for
   files a byte count, exact bytes, and SHA-256 checksum.
3. A separate history record containing a bounded Git bundle, its byte count,
   and SHA-256 checksum, followed by an end marker.

The full snapshot digest covers every frame byte. Unpacking validates the
expected commit and both source digests, every file and bundle checksum, entry
and byte limits, canonical ordering, parent directories, path uniqueness,
control names, truncation, and trailing data before no-replace publication.
No privileged tar extraction or replacement of an existing destination is
used. A post-publication filesystem-sync error can leave a complete result for
the operation owner to reconcile.

The history payload is a Git bundle pinned to the resolved commit and its
reachable tag references, using the `blob:none` filter. The bundle's advertised
`HEAD` and tags are checked against that commit and the tags selected from it.
This retains commit/tag metadata needed for `git describe --tags --always` while
leaving historical file blobs out of the bundle. It carries no original
remotes, local config, hooks, credentials, or unrelated refs. The source
checkout itself has no `.git` directory. Tracked `.gitignore`, `.gitattributes`,
and `.gitmodules` remain ordinary source files; their contents are never
executed by snapshot preparation or extraction. Git filter attributes are
refused before Git status. The ambiguous `unset` and `unspecified` values are
also refused when a matching filter driver is configured, so original local
configuration cannot cause a clean, smudge, or process helper to run.

The first slice rejects tracked symlinks, gitlinks/submodules, special Git
modes, files beginning with the Git LFS pointer signature (including extended
pointers), malformed/non-portable paths, case-folding path collisions, and
reserved Git/credential metadata names. The LFS check conservatively rejects
the signature line without assuming where an extension-bearing pointer places
its `oid` or `size` fields. It does not attempt to scan arbitrary application
source for secrets. Exact index comparison detects staged changes, and Git
status identifies visible untracked files. Bounded no-follow checks compare
tracked worktree bytes after Git's built-in path normalization; tracked status
flags are not used as a substitute for those checks because repository stat
settings can report clean converted files as modified. Git-ignored files are
outside the snapshot and do not affect its identity.

## Fixed ceilings

Callers may lower these values through `SourceLimits`; they cannot raise the
module ceilings.

| Bound | Default | Maximum |
| --- | ---: | ---: |
| Entries, including directories | 100,000 | 100,000 |
| One source file | 64 MiB | 64 MiB |
| Total committed file bytes | 2 GiB | 2 GiB |
| History bundle | 2 GiB | 2 GiB |
| Complete snapshot | 5 GiB | 5 GiB |
| Framing/Git metadata | 64 MiB | 64 MiB |
| One relative path | 4,096 bytes | 4,096 bytes |
| Git command time | 120 seconds | 3,600 seconds |

The manifest is limited to 256 KiB. Git object reads are grouped into bounded
64 MiB batches. The snapshot destination is a read-only regular file; extraction
publishes a private directory and refuses pre-existing paths.

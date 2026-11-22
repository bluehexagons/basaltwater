# Host storage maintenance

Debian hosts use the automatic recovery and daily maintenance below. CachyOS
has [setup-time package recovery](../workstations/cachyos.md#cleanup-during-setup);
Windows provides [explicit native storage cleanup](../workstations/windows-maintenance.md#low-space-recovery).

Basaltwater's Debian setup and deployment paths share a storage exclusion lock.
This feature does not require coding-agent tooling or the web-server profile.
Ordinary setup installs `basaltwater-storage.timer`, which checks daily with a
random delay. The job inventories root, managed `build-*` accounts whose passwd
homes exactly match Basaltwater's build-home convention, and the setup account.
Application service accounts are separate from build accounts; no service is
stopped by cache maintenance.
The target setup completion summary includes a concise disk-usage section:
used/total capacity, available space and free inodes, once per relevant filesystem.
It covers root, temporary, build, web and setup-account storage without scanning
directory contents; unavailable observations do not change the setup result.
The daily job also dispatches retention for installed managed builder and target
roles. It does not require those roles on ordinary hosts.

## Preview and apply

On the target, from the installed source directory:

```bash
cd /opt/basaltwater
sudo python3 -m lib.managed_storage
sudo python3 -m lib.managed_storage --apply
sudo python3 -B -m lib.managed_storage --pressure
sudo python3 -B -m lib.managed_storage --pressure --apply
sudo python3 -m lib.managed_storage --account developer --budget-mib 768 --target-mib 2048
sudo python3 -m lib.managed_storage --min-free-inodes 20000
sudo python3 -B -m lib.managed_storage --payloads
sudo python3 -B -m lib.managed_storage --payloads --apply
```

Wheel installations also provide `basaltw-storage` with the same arguments.
Output is JSON: per-account candidates include exact paths, UID ownership,
allocated bytes, category, rebuild cost, status and free bytes/inodes before and
after. Native failures are reported per candidate and do not abort other accounts.
System pressure operations include `before`/`after` capacity and signed
`net_freed_bytes`/`net_freed_inodes` changes (inodes where supported). Setup prints
these changes too. A successful native command can reclaim nothing; concurrent
disk use can produce a negative net change. Missing final observations or a
changed mount are failures, and preview reports no simulated savings.
The final `filesystems` section reports capacity once per observed mount, including
remaining byte and inode shortfalls. Pressure mode includes system-only mounts
such as a separate `/tmp`. Unavailable observations and unmet targets cause a
nonzero exit, including when payload cleanup could not resolve them.
Partial inventories, linked cache roots, nested mounts, mixed ownership and
hardlinks defer cleanup. Internal symlinks also defer ordinary native caches;
the disposable build paths described below unlink them without following them.
Allocated usage is an observation, not guaranteed reclaimable space.
Shared extents and other concurrent disk users can change the measured savings.

The default combined cache budget is 1024 MiB **per account**; the free-space
target is 1536 MiB, with 10,000 free inodes (`--min-free-inodes`), on each
candidate's filesystem. On separate non-root tmpfs/ramfs mounts, maintenance caps
each target at 10% of the filesystem's total bytes or inodes. Setup still enforces
its configured workload estimates. A cache is selected when the
account exceeds its budget or its filesystem falls below the target. `--force`
selects all eligible candidates. Before setup starts, a failed capacity check
triggers one pressure cleanup pass, under exclusive storage ownership. Setup then
rechecks capacity under its normal shared lock. It never replays setup steps or
repeats cleanup on a later build failure. Unsafe or active
caches remain protected; insufficient space still stops setup. Preflight reports
all failing temporary and ordinary paths together, so the single cleanup pass can
address them all. Each disk's cleanup target is at least 1536 MiB, increased to
that filesystem's configured setup requirement when larger. Separate non-root
memory filesystems cap that reserve at 10% of capacity while still meeting their
full setup requirement. Paths sharing a mount retain the strictest requirement.
Automatic cleanup selects only filesystems reported by the failed preflight and
also responds to insufficient free inodes. A full separate tmpfs does not trigger
cache or log deletion on an unrelated disk. Cleanup still cannot enlarge a tmpfs
or reclaim storage outside its eligible paths.
Deployment operations do not trigger automatic cleanup. Raise the target
to accommodate your measured deployment peak plus a reserve. Use a systemd
override for the service's `ExecStart` to persist different policy arguments.

For root and explicitly selected host accounts, these conventional caches are eligible:

| Path below home | Native operation | Preserved |
| --- | --- | --- |
| `.npm/_cacache` | `npm --cache "$HOME/.npm" cache clean --force` | `.npmrc`, `_npx`, global packages |
| `.nvm/.cache` | `nvm cache clear` | Installed Node versions, aliases, global packages |
| `.cache/go-build` | `go clean -cache` | Module downloads, downloaded toolchains, source |

Commands run as the owning account, with explicit cache paths and without login
profiles. Go configuration, hooks and automatic toolchain downloads are disabled.
Custom cache locations are outside this policy. Missing tools remain failures for
root and ordinary host accounts. npm/nvm cleanup also takes the
existing per-account Node runtime lock. When nvm is absent, npm cleanup uses the
system npm executable. Each owner worker revalidates its home, cache inode and
cache inventory before running a native command. Cache workers and native system
cleaners inherit the maintenance lock so it remains held while they run if the
launcher exits unexpectedly. Missing or incomplete
post-cleanup measurements are failures, rather than successful reclamation.

Managed build accounts use additional disposable paths when their filesystem is
below the free-space or inode target: `.npm/_logs`, `.npm/_npx`, `go/pkg/mod`, `.cache/uv`
and `.cache/pip`. This discards npm diagnostics, downloaded tools and modules,
including Go toolchains. Subsequent builds download them again and may take
longer. For exact managed build homes only, a failed native cache command falls
back to deleting its validated cache directory as the owning account. This also
handles npm failing to allocate its temporary configuration directory or files
when bytes or inodes are exhausted. Read-only Go cache directories are made
writable first. Sources, configuration and other
home content remain outside this policy. Missing homes are reported as absent.
Each additional cache is selected using its own filesystem's capacity, including
when its home has space available on another mount.

`--pressure` also cleans apt package archives and rotates/vacuums systemd journals
to 64 MiB and seven days on filesystems below the target. Persistent journals in
`/var/log/journal` and volatile journals in `/run/log/journal` are checked and
vacuumed separately; apt cleanup explicitly selects `/var/cache/apt/archives`.
Pressure cleanup also runs `systemd-tmpfiles --clean` separately for `/tmp`,
`/var/tmp` and `/var/lib/systemd/coredump` when that path's filesystem needs
reclamation. This applies the installed system administrator's age and exclusion
rules; it does not shorten retention or use `--remove`, `--purge` or `--boot`.
Absent directories are skipped. Paths without an age-based cleanup rule may
reclaim nothing. Preview lists these native operations without executing them.
It then removes unused
older Node versions in inactive managed build homes, retaining the newest version
of each installed major and every exact or partial version selected by an NVM
alias. Versions referenced by running processes, unsafe inventories and busy
runtime locks defer deletion. Removed versions include their installed global
packages. Runtime capacity is checked at `.nvm/versions/node`, including when it
uses a separate mount from the build home; pressure reports include that mount.
Current services stay running; application data and deployed releases
are preserved. Pressure cleanup is automatic before a setup refused for capacity
and in the daily timer, alongside its ordinary cache and payload policy. Existing
installations receive the timer change on their next setup. Manual preview
does not change logs or runtime directories.

## Managed build payloads

`--payloads` adds local role-aware inspection; `--apply` selects eligible deletion.
The installed daily timer includes both flags. The default retained payload
budget is 4096 MiB per role, with the same free-space target as cache maintenance
and a seven-day grace period. Override with `--payload-budget-mib`,
`--payload-min-age-hours` and `--target-mib`. Each pass changes at most 32 eligible
records per role and stops selecting deletions once policy is satisfied. Preview
does not simulate reclaimed bytes. Protected content can prevent either budget
or headroom from being reached; reports include remaining byte shortfalls and
the command exits unsuccessfully in that case.

On builders, old certain-terminal requests use the existing verified settlement
and source/log pruning operations. Successful bundles stay downloadable by
default. `--release-builder-bundles` explicitly ends retrieval of eligible old
successful artifacts; review all consumers' retention needs before enabling it.
Queued/running/uncertain work, readers, scratch and ambiguous publication evidence
remain protected. Pending pruning requires the existing exact-request retention
recovery procedure; a scheduled pass never guesses completion.

On targets, only proven completed bundles superseded by a newer installed release
qualify. Every current bundle is verified and pinned, including during pressure,
so local reconciliation remains possible without the builder. Unfinished
conversion, setup, activation or uncertain admission evidence defers collection.
Live releases, data directories and services are never removed. Durable retirement
intent precedes unlink; completion precedes releasing its logical byte charge.
Compact intent/ready/retirement records remain. Missing or corrupt current bundles
refuse collection before deleting older bundles.

An interrupted target retirement appears with `retirement_requires_exact_retry`.
After reviewing the operation and preview revision, finish that exact retirement:

```bash
sudo python3 -B -m lib.build_storage_maintenance \
  --retry-target OPERATION_ID --expected-revision SHA256
```

The retry repeats current-release/reference checks. It cannot override a pinned
or uncertain operation. Corrupt or incomplete markers require reconciliation.
Both roles report finite record runway separately: payload collection preserves
replay identities and does **not** free record slots. Warnings start at 80% usage.
Replay-safe record retirement remains separate protocol work; payload collection
does not provide indefinite operation within finite metadata limits.

Controller workspaces are owned by their controller account and are not swept
by a root host timer. Use the explicit completed-attempt collector in the
[build operations guide](../services/build-operations.md#collect-completed-controller-payloads).

## Exclusion and headroom

Setup and deployment hold a shared `/run/basaltwater-storage.lock`; maintenance
takes it exclusively without waiting. An active setup or deployment defers the
whole maintenance pass. Any process on an ordinary selected account defers its
cleanup; relevant root package-manager, compiler, installer and Node processes
also defer root cleanup. Incomplete process visibility fails closed. This is
deliberately conservative on interactive workstations.

External build launchers must hold this same shared lock for their entire run:

```bash
sudo flock --shared /run/basaltwater-storage.lock your-build-command
```

The lock file must first be created by setup or a maintenance invocation. A
process snapshot cannot exclude a new uncoordinated manual build. This protocol
does not coordinate arbitrary external launchers or older installed Basaltwater
code; stop such intake before applying maintenance. Running services remain up.

Cache ownership is released before role dispatch. Target collection reacquires
storage exclusion, then target maintenance, admission and artifact ownership.
Builder collection uses existing nonblocking worker, input, payload and job
locks; a worker or reader starting after inventory still excludes deletion.
Neither path relies on a process snapshot to protect managed payloads.

Preflight checks downloads, build homes, staging and temporary directories,
including separate `/tmp` mounts and `TMPDIR`. Each filesystem must provide the
estimated build peak plus a reserve, with enough available inodes. Defaults are
512 MiB peak, 512 MiB reserve and 10,000 inodes. Temporary paths use a separate
128 MiB peak plus 64 MiB reserve by default. Override these estimates for a
known workload using `BASALTWATER_STORAGE_PEAK_MIB`,
`BASALTWATER_STORAGE_RESERVE_MIB`, and `BASALTWATER_STORAGE_MIN_INODES` in the
target setup/deployment process environment. Temporary estimates use
`BASALTWATER_STORAGE_TMP_PEAK_MIB` and `BASALTWATER_STORAGE_TMP_RESERVE_MIB`.
A small tmpfs can fail independently
of the root disk. Increase its capacity or select a suitable temporary directory
and verify the workload's actual peak before lowering estimates.

A failed preflight reports phase, path, mount, available bytes/inodes, requirements
and bounded managed-build-home storage measurements. Cleanup reports any remaining
byte or inode shortfall and exits unsuccessfully when its target cannot be reached;
it does not remove deployed releases, databases or swap to satisfy policy. Pressure
cleanup can remove the unused older build runtimes described above.
The total reported for managed build homes includes retained runtimes and other
protected files; it is not a reclaimable-byte estimate. If setup still refuses,
preview `sudo python3 -B -m lib.managed_storage --pressure` on the target and inspect
`sudo journalctl -u basaltwater-storage.service -n 100 --no-pager` for skipped
caches or native-tool failures. Apply reviewed cleanup with `--apply`. Increase
capacity if protected content and the workload's peak cannot fit together.

## npm maintenance

The weekly Node updater now updates npm on its automatically owned LTS runtime
even when broader global package upgrades are disabled. It retains the dependency
minimum-age policy and uses `--engine-strict` so an incompatible npm release fails
without forcing an unsupported engine. Set `BASALTWATER_NPM_AUTO_UPDATE=0` in a
systemd service override to disable this independent update. An unmarked legacy
runtime is also eligible for npm-only updates when it is the selected default
LTS runtime. A malformed ownership marker never grants eligibility. Project-marked
runtimes remain outside automatic global-tool changes; their owners must update
them deliberately. Other global packages still require the ecosystem upgrade
opt-in and automatic runtime ownership.

Native command boundaries follow the [journalctl documentation](https://www.freedesktop.org/software/systemd/man/latest/journalctl.html),
[systemd-tmpfiles documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd-tmpfiles.html),
[npm cache documentation](https://docs.npmjs.com/cli/cache/),
[nvm documentation](https://github.com/nvm-sh/nvm), and
[Go clean documentation](https://pkg.go.dev/cmd/go#hdr-Remove_object_files_and_cached_files).
Unit tests mock native commands and verify policy, preservation and exclusion.
Disposable native checks also verified npm cache refill from a local archive,
nvm archive cleanup with its runtime retained, and Go recompilation after build
cache cleanup, with persistent test files preserved. These exercise the native
command layer; they do not establish live target deployment recovery on every
host. After apply, deploy a representative project and inspect the maintenance
journal.

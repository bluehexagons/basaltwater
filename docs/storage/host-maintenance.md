# Host storage maintenance

Basaltwater's Debian setup and deployment paths share a storage exclusion lock.
This feature does not require coding-agent tooling or the web-server profile.
Ordinary setup installs `basaltwater-storage.timer`, which checks daily with a
random delay. The job inventories root, managed `build-*` accounts whose passwd
homes exactly match Basaltwater's build-home convention, and the setup account.
Application service accounts are separate from build accounts; no service is
stopped by cache maintenance.
The daily job also dispatches retention for installed managed builder and target
roles. It does not require those roles on ordinary hosts.

## Preview and apply

On the target, from the installed source directory:

```bash
cd /opt/basaltwater
sudo python3 -m lib.managed_storage
sudo python3 -m lib.managed_storage --apply
sudo python3 -m lib.managed_storage --account developer --budget-mib 768 --target-mib 2048
sudo python3 -B -m lib.managed_storage --payloads
sudo python3 -B -m lib.managed_storage --payloads --apply
```

Wheel installations also provide `basaltw-storage` with the same arguments.
Output is JSON: per-account candidates include exact paths, UID ownership,
allocated bytes, category, rebuild cost, status and free bytes/inodes before and
after. Native failures are reported per candidate and do not abort other accounts.
Partial inventories, symlinks, nested mounts, mixed ownership and hardlinks defer
cleanup. Allocated usage is an observation, not guaranteed reclaimable space.
Shared extents and other concurrent disk users can change the measured savings.

The default combined cache budget is 1024 MiB **per account**; the free-space
target is 1536 MiB on each candidate's filesystem. A cache is selected when the
account exceeds its budget or its filesystem falls below the target. `--force`
selects all eligible candidates. There is no automatic cleanup-and-retry loop
during setup, avoiding repeated eviction and download cycles. Raise the target
to accommodate your measured deployment peak plus a reserve. Use a systemd
override for the service's `ExecStart` to persist different policy arguments.

Only these conventional cache paths are eligible:

| Path below home | Native operation | Preserved |
| --- | --- | --- |
| `.npm/_cacache` | `npm --cache "$HOME/.npm" cache clean --force` | `.npmrc`, `_npx`, global packages |
| `.nvm/.cache` | `nvm cache clear` | Installed Node versions, aliases, global packages |
| `.cache/go-build` | `go clean -cache` | Module downloads, downloaded toolchains, source |

Commands run as the owning account, with explicit cache paths and without login
profiles. Go configuration, hooks and automatic toolchain downloads are disabled.
Custom cache locations are outside this initial policy. Missing tools are failures,
never a reason to substitute recursive deletion. npm/nvm cleanup also takes the
existing per-account Node runtime lock. When nvm is absent, npm cleanup uses the
system npm executable. Each owner worker revalidates its home, cache inode and
cache inventory before running a native command. Workers inherit the maintenance
lock so it remains held if the launcher exits unexpectedly. Missing or incomplete
post-cleanup measurements are failures, rather than successful reclamation.

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
byte shortfall and exits unsuccessfully when its target cannot be reached; it does
not remove runtimes, deployed releases, databases or swap to satisfy policy.

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

Native command boundaries follow the [npm cache documentation](https://docs.npmjs.com/cli/cache/),
[nvm documentation](https://github.com/nvm-sh/nvm), and
[Go clean documentation](https://pkg.go.dev/cmd/go#hdr-Remove_object_files_and_cached_files).
Unit tests mock native commands and verify policy, preservation and exclusion.
Disposable native checks also verified npm cache refill from a local archive,
nvm archive cleanup with its runtime retained, and Go recompilation after build
cache cleanup, with persistent test files preserved. These exercise the native
command layer; they do not establish live target deployment recovery on every
host. After apply, deploy a representative project and inspect the maintenance
journal.

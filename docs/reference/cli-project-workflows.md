# Project tooling and game publishing

[Documentation index](command-line.md)

## Project manifests and Node runtimes

These commands operate on local project files and toolchains without selecting
a saved host. Paths default to the current directory.

`manifest init` proposes a version 1 `basaltwater.json` from project metadata
without executing project scripts. `--dry-run` previews the proposal; normal
initialization writes it only when no manifest exists. Existing manifests are
validated and preserved. Review inferred commands, outputs, service settings,
and routes before committing the proposal. `--kind` accepts `auto`, `node-site`,
`node-package`, `node-service`, `go-service`, `full-stack`, `static`, or `publishing`.
`manifest validate` checks an existing manifest without modifying it. Both
subcommands support JSON output.

`manifest check-t3-worktree` inspects the selected Git commit for T3 sparse
checkout settings. Use `--ref REF` to select a revision and repeat
`--require-file PATH` to check setup inputs. A committed `worktreeLayout` requires
checking a disposable T3 worktree. See the
[project worktree guide](../agents/project-manifests.md#t3-code-project-actions-and-worktrees).

`node status` reports the selected installed runtime, and `node env` prints a
quoted PATH export for shell activation. Selection honors an explicit
`--version`, project `.node-version` or `.nvmrc` pins, and package Node engine
requirements. `node exec` runs the command after `--` with that runtime while
retaining the current working directory; `--project` selects metadata rather
than changing directories. These commands do not download a missing runtime.
Use `--json` for one result from `node status`, `env`, `resolve`, `install`,
or `doctor`. `node exec` forwards the child's streams and exit code. An
input or selection failure before dispatch can be returned with `--json`.
Selection requires `^24.22.1 || ^26.12.1`; unsupported pins and system runtimes
fail with guidance rather than running a project command.

`node resolve` prints an exact supported target for the project's pin and Node
engines, using the official release index for rolling aliases and partial pins.
It changes no runtime or NVM configuration. Deployment preparation uses it to
validate every project target before installing any toolchain.

`node install` explicitly installs the requested version or project pin through
managed NVM. On CachyOS it prepares missing user-local NVM on demand, without
requiring an earlier `--node-versions` selection or a full setup rerun.
Repeat `--package-manager NAME@VERSION` to install exact npm, pnpm,
or yarn versions for that runtime. See [Project tooling](../agents/project-tooling.md)
for selection precedence, supported ranges, initialization examples, and
per-runtime package-manager behavior.

## VM game publishing

Run `publish` on the VM as its publishing account. Commands emit JSON and use
VM-local sessions; no controller credential transfer is involved.

```text
basaltw publish status|projects|jobs|runs
basaltw publish runs --id RUN_ID
basaltw publish project ID REPOSITORY butler OWNER/GAME:CHANNEL [--record PATH]
basaltw publish project ID REPOSITORY steamcmd APP_ID --depot DEPOT_ID --username ACCOUNT
basaltw publish complete REPOSITORY ARTIFACT_SUBDIRECTORY INTERNAL_BUILD_ID [--record PATH]
basaltw publish prepare PROJECT_ID
basaltw publish upload ARTIFACT_ID [--wait [--timeout SECONDS]]
basaltw publish cancel RUN_ID
basaltw publish worker
basaltw publish schedule PROJECT_ID [--interval MINUTES]
basaltw publish job JOB_ID pause|resume|remove
basaltw publish job JOB_ID edit --interval MINUTES
basaltw publish draft PROJECT_ID JSON_FILE
basaltw publish export DRAFT_ID [--handoff]
basaltw publish artifact-text ARTIFACT_ID PATH DRAFT_ID
basaltw publish remove-artifact ARTIFACT_ID
basaltw publish promote-itch ARTIFACT_ID DESTINATION_PROJECT
basaltw publish release RUN_ID [--branch BRANCH]
basaltw publish beta-prepare RUN_ID BRANCH
basaltw publish beta-promote RELEASE_ID
basaltw publish auth login|logout butler|steamcmd [--username ACCOUNT]
```

Pass `prepare`'s `id` to `upload`. Use the returned `job.id` with `runs --id`
to read the run or with `cancel` to request cancellation. `job.state` describes
the observed run outcome and `job.native_state` preserves the publisher's state.
`--wait` observes completion for up to 600 seconds by default. Exit 124
returns the same handle with `issue: wait_timeout` while the worker continues.
The `projects`, `jobs` and `runs` list commands return named arrays.

`auth` requires a human non-root terminal and accepts no secret arguments.
Provider dialogue uses stderr, and the final JSON result uses stdout.
The HTTPS panel provides guided login and the sole managed human approval
action. `draft` cannot import approval fields. `export --handoff` respects the
reviewed timing window and release gate; it never publishes remotely.
`schedule` rejects another non-removed job for the same project. `job edit`
changes cadence without resuming or accepting changed project settings. Pause,
edit and removal cancel queued scheduled uploads; running uploads require their
separate cancellation action. Removal keeps job/run history and receipts.
Steam default/public release and rollback always use the manual Steamworks
handoff. Optional beta promotion requires a separately configured VM-local
publisher key and observed branch/build state. Live provider qualification
remains open. See [VM game publishing](../workstations/game-publishing.md) for the completed-build
contract, unattended job ownership, exact-text review, recovery and limits.

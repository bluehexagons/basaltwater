# Agent diagnostics and storage commands

[Documentation index](cli-setup-services.md)

Select the relevant command below; this is a catalog, not a sequence to run.
Manifest is read-only; doctor is read-only unless `--fix` or `--record` is
selected. Visual comparisons write private evidence; revision captures run a
project command in new worktrees. Blender smoke renders a bundled scene and
retains private evidence. Updates, workspace creation, and maintenance
holds also change state:

```bash
basaltw agent manifest --json
basaltw agent blender smoke --json
basaltw agent visuals compare before.png after.png --json
basaltw agent visuals check pause-menu --review-manifest captures/review.json --json
basaltw agent visuals check-revisions pause-menu --before main --after HEAD --settings ~/capture-settings.json --json
basaltw agent visuals capture --before HEAD~1 --after HEAD --settings ~/capture-settings.json --json -- ./tools/capture-scene --settings '{settings}' --output '{output}'
basaltw agent doctor
basaltw agent doctor --tool codex --tool claude --json
basaltw agent doctor --all-capabilities --json
basaltw agent doctor --capability development --json
basaltw agent resources --json
basaltw agent storage inspect --json
basaltw agent storage clean-podman --json
basaltw agent containers inspect --engine podman --json
basaltw agent graphics doctor --context xvfb --json
basaltw agent browser resolve-preview --port 8080 --json
basaltw agent doctor --tool codex --tool opencode --capability browser
basaltw agent doctor --capability browser
basaltw agent doctor --capability t3code --capability host
basaltw agent doctor --capability t3code --capability host --record
basaltw agent doctor --last-record --json
basaltw agent doctor --capability t3code --fix
basaltw agent doctor --capability t3code --wait-for-t3-startup
basaltw agent doctor 10.0.0.10 agent --tool codex --json
basaltw agent update --dry-run
basaltw agent update --tool codex --tool claude
basaltw agent update --json
basaltw agent update 10.0.0.10 agent --tool codex --dry-run
basaltw agent workspace create ~/repos/project api-check --base HEAD --json
basaltw agent workspace list ~/repos/project --json
basaltw agent workspace status WORKTREE --json
basaltw agent workspace remove WORKTREE --dry-run --json
basaltw agent models init ~/repos/project --json
basaltw agent models recommend ~/repos/project coding --available SESSION_MODELS.json --json
basaltw agent models report ~/repos/project --json
basaltw agent maintenance hold --hours 8
basaltw agent maintenance status --json
basaltw agent maintenance release 10.0.0.10 agent
basaltw agent support-bundle --output ~/agent-support.json
```

`agent manifest` lists active tools, desktop application workflows, workspace
conventions and explicit branch-to-deployment mappings. It does not launch
applications or check authentication. Optional committed declarations in
`basaltwater-agent.json` contain reusable project information and intentionally
public deployment mappings; keep personal/private inventory outside Git.
Undeclared mappings remain unknown. See
[environment manifests](../agents/agent-environment.md) and
[desktop development](../workstations/desktop-development.md), including Blender instructions.

`agent blender smoke` deliberately runs the active PATH's Blender without a
desktop, using a bundled 128 × 128 Cycles CPU scene, eight samples, a fixed seed,
two render threads and disabled denoising. It isolates configuration, scripts,
data and cache paths and disables blend-file Python auto-execution. It retains
`scene.blend`, `render.png`, `settings.json`, `blender.log` and `report.json` in
a new mode-`0700` directory under `~/.local/state/basaltwater/blender/`.
`--output NEW_DIRECTORY` selects a destination with an existing parent; an
existing destination is rejected. `--timeout` accepts 1–600 seconds, default
120; timed-out processes are terminated through the managed command runner.
The command exits nonzero for startup, Python, rendering or artifact-validation
failures and retains available failure evidence. `--json` prints the report.
Success verifies this CPU render path; desktop editing, Eevee, add-ons and GPU
backends remain separate checks. See [Blender workflows](../workstations/desktop-development.md#blender).

`agent visuals compare` embeds two PNGs and optional `--before-settings` and
`--after-settings` JSON in a standalone viewer with synchronized zoom/scroll,
overlays and pixel differences. `capture` resolves both revisions to commits
and runs the supplied argv in separate managed worktrees with shared settings
and distinct PNG output paths. `--repository` defaults to the current checkout;
`--timeout` bounds each capture to 1–3600 seconds (default 600). Both commands
accept `--output NEW_DIRECTORY` and otherwise use private managed state.
Worktrees, logs and evidence are retained on success or failure; captures never
switch the primary checkout or publish evidence. Read
[visual comparisons](../workstations/visual-comparisons.md) for harness requirements and cleanup.

`agent visuals check` runs a declared recipe and retains its private evidence.
If the recipe declares a `version_check`, it checks the selected executable
before launch and records the result in `tool_version`. See
[recipe version preflight](../workstations/visual-comparison-capture.md#check-a-recipe-tool-version).
When the recipe emits a batch manifest, `--review-manifest RELATIVE_JSON`
also generates a gallery and links it in the check report. See
[recipe gallery generation](../workstations/visual-comparison-capture.md#review-a-recipes-captures).
`agent visuals check-revisions` runs the same named recipe in two pinned
worktrees with identical requested settings and one case comparison report.
Both commands support opt-in Godot preparation with `--prepare` and an
explicit `--asset-bundle`. See
[case checkpoints](../workstations/visual-comparison-capture.md#retain-case-results-and-captures)
and [native worktree preparation](../workstations/cachyos-development-session.md#prepare-assets-in-a-fresh-worktree).

The default doctor check covers GitHub CLI, Codex CLI, Claude Code, and OpenCode.
Missing credential files are reported as sign-in reminders but do not make an
otherwise installed tool unhealthy. A present Codex file also gets a non-secret
freshness check; known invalid or refresh-overdue ChatGPT credentials make the
Codex doctor result unhealthy.
`--capability browser` additionally verifies managed launchers, MCP registration
for installed compatible agents, and a local Chromium interaction/rendering
smoke test.
`--capability development` inventories managed Godot, Go, nvm/Node, and native
game-development toolchains. It verifies installed engines and compilers,
reports whether matching Godot export templates are visible, checks that Go includes
`gofmt` and a compiler when CGO is enabled, and ensures Node retains the
npm/PNPM baseline promised by `--node`.
The native inventory reports saved `--game-dev` selection, fixed compiler/build/
debug commands, and pkg-config module versions without launching graphics or
project scripts. A selected baseline fails when required commands or host
modules are missing. Missing project-owned SDL3/SDL3_image is reported in
`project_bootstrap_required` and does not fail host readiness. Unsafe selection
state remains an explicit failure. See [Debian native tooling](../agents/project-native-builds.md#debian-native-game-development).
Absent optional toolchains do not fail the capability.
`--capability t3code` checks the managed service, native runtime, pairing helper,
endpoint, provider authentication, Git identity and credential helper, and the
managed agent skill. Its `--fix` mode can rebuild blocked native dependencies,
configure the GitHub HTTPS helper after a successful login, enable the service
at boot, and restart it when inactive.
The normal check remains a quick snapshot. To wait for T3 Code startup after an
update, opt in with:

```bash
basaltw agent doctor --capability t3code --wait-for-t3-startup
```

This allows up to 30 minutes of actual monotonic waiting for HTTP and service
readiness. Readiness requires three stable checks from the same invocation ID;
a systemd failure ends the wait early. The flag does not require `--fix`, and
leaves a healthy running migration alone. The panel uses this wait after an
update, with its readiness subprocess and outer action budgets including the
wait window. When `--fix` performs native npm dependency repair, it skips that
repair if stopping the service fails or times out, reports the diagnostic, and
leaves T3 readiness unhealthy.
`--capability host` reports memory and swap headroom, filesystem and bounded
agent-storage use, T3 service cgroup counters, recurring maintenance timer
state (including installed Node and Godot update jobs), the agent maintenance
hold, and pending reboots. Advisory pressure appears as warnings; critical
filesystem pressure or a recorded maintenance
failure makes the capability unhealthy.
It also reports the current process's affinity, inherited visible cgroup CPU
quota and memory high/max, and timestamped pressure in `workload_limits`.
T3 counters include descendants. `agent resources --json` returns these resource
observations independently on Debian or CachyOS. Missing interfaces stay
unavailable; CPU budget is an upper bound rather than a concurrency setting.
Add `--container-storage` to opt into bounded aggregate Podman/Docker probes;
the flag also includes host checks and supports the remote `HOST USER` form.
Ordinary host checks and support collection do not invoke container engines.

The `browser_cache` warning reports a Playwright cache larger than 2 GiB.
Review its browser revisions and package references with the
[shared cache guidance](../agents/storage-cleanup-caches.md#shared-cache-conventions).
Start by measuring the cache with
`basaltw agent storage inspect --path "$HOME/.cache/ms-playwright" --json`.

`agent storage inspect` prints JSON capacity and cleanup-candidate evidence
without deleting files, reading their contents or creating reports. Default
roots cover caches, Trash, logs, temporary data, managed runtimes, projects,
sessions and attachments. A separate metadata-only listing covers session
SQLite/WAL/SHM files, at most 1024 entries in each of `~/.codex` and
`~/.t3/userdata`; it never opens databases.
Repeat `--path` for up to eight exact local directories instead. Scans skip
symlinks, nested mounts and remote or unknown filesystems. Defaults are 250000
entries shared across roots, up to 50000 per default root, a cooperative
20-second budget, and depth 24. `--max-entries` accepts 1–2000000 and `--timeout`
accepts 1–60 seconds. Partial and unavailable evidence is explicit; allocated
usage is not a savings guarantee. See [storage cleanup](../agents/storage-cleanup.md)
for retention checks and approved follow-ups.

`agent storage clean-podman` previews by default and operates only on the
current user's local, nonroot rootless Podman image cache. Use `--apply` after
reviewing the candidate IDs; `--dry-run` explicitly selects preview and `--json`
returns structured evidence. It removes unused images, including tagged images,
when their image creation age exceeds seven days, or 24 hours during low
graph-root filesystem headroom. The low-headroom threshold follows the dynamic
user-cache target `min(8 GiB, max(2 GiB, total filesystem size / 5))`. Age is
based on image creation metadata, not last use or pull time. The explicit
`io.basaltwater.keep` image label retains an image; tags do not pin images.
Podman v5.4.2 `image prune --all` can clear persistent cache mounts
implicitly, and recursive pruning can remove keep-labelled parent images. The
helper therefore selects eligible images from age, label, and reference
inventory and removes exact full 64-character image IDs, children before
parents, while preserving ancestors of retained images. If an eligible image
has multiple names, it first untags that exact ID with `podman untag ID`, since
image removal otherwise refuses multiple names. It then uses native
`podman image rm --no-prune --ignore ID...` without `--force` or `--all`.
Native non-forced removal protects images referenced by containers; no broad
prune, parent recursion, or storage deletion is used.

Old, unmounted abandoned Buildah working containers may also be removed by
matching exact IDs with `buildah rm`, only after native metadata and same-store
validation and quiet process checks; they use the same container-creation-age
cutoff. Mounted builders and keep-labelled builders are preserved. Rootful,
remote, or unsafe stores are refused. Active build or mutation processes,
incomplete evidence, or failed checks defer cleanup; running work is never
forced. Work in disposable test stores should be considered temporary because
cleanup may remove its resources, and sessions must recreate them as needed.

The helper serializes work with a lock. A timeout leaves completion uncertain;
inspect store state before retrying. Preview IDs and savings are preflight
estimates: shared layers and reference changes can affect actual removals.
Reports show actual filesystem free bytes before and
after separately from reported image size. Readonly container inspection and
preflight remain readonly. Daily full user-cache maintenance invokes this
helper automatically; its `--agent-storage-only` mode does not.
See [authorized Podman cleanup](../agents/storage-cleanup.md#podman-image-cleanup).

`agent storage project ABSOLUTE_PROJECT` reads optional project cleanup JSON
and measures declared roots without executing any repository command. The
default path is `.basaltwater/cleanup.json`; `--policy` selects another literal
project-relative path. Script rules describe preview/apply argv; agent rules
describe curation and protected archives. The read-only result includes
`generated_cleanup_commands` with preview/apply argv for generated cleanup
strategies. Defaults are 50000 entries and a 10-second shared scan budget. If
the policy is absent, the result includes an optional starter layout but
creates nothing. Follow existing project tooling and instructions first.

`agent storage clean-generated ABSOLUTE_PROJECT --rule NAME` previews by
default; use `--apply` only after reviewing the planned cleanup. A rule must
name an existing strategy generated from a reviewed version 1 cleanup policy.
`--policy` selects a project-relative JSON policy path. Generated strategies
select exact, disjoint relative directories and cannot overlap preserved paths,
archives, or agent roots. They have no age filter or archive step. The command
never executes policy commands and accepts no arbitrary path arguments. Before
removal, guards check Git tracked files, ownership, private checkout ancestry,
links and mounts, bounded inventory, and active use. Process checks are best
effort; stop jobs using generated files first. Apply requires both explicit
`--apply` and authorized cleanup scope, including writes to target parents when
using an isolated checkout. Existing script and agent rules remain supported.
See [project cleanup](../agents/project-cleanup.md) for policy and strategy
details, and [session retention](../agents/session-retention.md) for app-owned
cleanup.

`agent containers inspect` observes local storage; repeat `--engine` to narrow
the inventory. `preflight` compares graph-root space with an explicit estimated
uncompressed image size, `--additional-copies` (default 1, range 0–8), and
`--reserve-bytes` (default 1 GiB). It returns nonzero for unknown/insufficient
space; an adequate estimate is never a guarantee. Reclaimable bytes are estimates
and are not free space. No cleanup command is provided. `agent graphics doctor`
requires an explicit Xvfb or existing-display context and reports GLX context
creation/acceleration separately from project rendering, hardware timing,
physical display, HDR and latency. See [project tooling](../agents/project-tooling.md)
for platform-specific use, cleanup proposals, storage migration and cache recipes.

When `--capability` is supplied without `--tool`, doctor checks only the
requested capability instead of requiring the default set of terminal agents.
`--all-capabilities` checks browser, development toolchains, host, and T3 Code
readiness and inventories the default terminal tools in the same text or JSON
result. Installed terminal tools are required; absent tools are reported as
optional inventory with `"required": false` and do not make the comprehensive
check fail. Explicit
`--tool` flags make every selected tool required, including an absent one. The
option is mutually exclusive with a narrowed `--capability` selection and works
with the remote `HOST USER` form.
Supplying `HOST USER` runs the same doctor through managed SSH from the control
system and preserves its text or JSON output and exit status. The target must
have been configured by Basaltwater so `/opt/basaltwater` is present. Add
`--key PATH` when the VM uses a non-default SSH identity.

Add `--record` after a deliberate update or reboot to replace the private
readiness record at
`~/.local/state/basaltwater/agent-readiness.json`. A bare `doctor --record`
checks the default terminal tools and adds host readiness plus T3 Code when a
managed T3 installation is present; explicit `--tool` and `--capability`
selections retain their normal narrowing behavior. The mode-`0600` record
contains versions, aggregate checks, warnings, and the current Linux boot ID,
but omits executable and home paths, Git identity, credential contents,
repository contents, and process details. Use `doctor --last-record` to read it
without running checks. That command exits nonzero when the record is unhealthy
or belongs to a previous boot, so it can distinguish fresh post-reboot evidence
from an older successful audit. Both options support the remote `HOST USER`
form. Doctor JSON returns `tools` and `capabilities` in one result object.
Query the saved evidence with `--last-record --json`.

## Native T3 preview readiness

Run on the Linux T3 environment host. `agent browser resolve-preview` connects
to `127.0.0.1` first and tries `::1` if that TCP connection fails, matching
the T3 preview tunnel. Use `--host` to check one address explicitly. It works
with the core CLI on hosts without a managed gateway.

```bash
basaltw agent browser resolve-preview --port 8080 --protocol https --path '/app/?view=details#summary' --json
```

The defaults are HTTP and `/`. The probe sends the path and query with the
T3 preview's `localhost` host name and keeps the fragment in the returned
`navigation.target`. It checks the initial response without following
redirects. HTTPS verifies the certificate for `localhost` using the probe
host's trust store. Add `--profile godot` to check cross-origin isolation
headers. `upstream_url` identifies the endpoint used for the probe, or the first
address if both connection attempts fail. Browser rendering, tunnel
access and client trust need separate checks.

Default output and `--json` return the same result object. Exit status 0
returns a native environment-port target in `navigation`. Status 1 reports an
upstream failure with `issue` and `remediation`, and status 2 reports an
invalid request. Where the gateway utility is installed,
`basaltwater-web preview resolve --route t3` accepts the same flags.
See [T3 preview routing](../agents/browser-t3-preview.md#collaborative-preview-and-private-networks).

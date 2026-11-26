# Run a CachyOS development session

[Topic index](cachyos-development.md)

## Edit, run, inspect, and hand back

Use the project's actual checkout or [managed worktree](../agents/project-tooling.md).
Consult its `AGENTS.md`, engine requirements, launch arguments, test recipes,
and save-data conventions before executing project code.

`agent manifest` reads the same bounded project metadata as `develop doctor`.
Its development role describes the chosen project: Godot, Node, or generic
native development. Godot editor/run/import commands are null without
`project.godot`; the Node script prefix is null without `package.json`.
Package metadata includes script names and the declared Node requirement,
without script bodies or runtime execution. A project containing both files
advertises both workflows. Discovery never checks the live graphical session;
use `develop doctor` for session prerequisites and the project's own checks
for runtime/build compatibility.

```fish
basaltw agent manifest /absolute/project --json
basaltw local cachyos-doctor --json
basaltw desktop --native develop doctor --project /absolute/project --json
basaltw desktop --native develop import --project /absolute/project --json
basaltw desktop --native develop editor --project /absolute/project --dry-run
basaltw desktop --native develop editor --project /absolute/project --json
basaltw desktop --native develop run --project /absolute/project \
  --scene res://scenes/playtest.tscn --json -- --fixture menu
```

Most `develop` commands work from T3 or same-user SSH without a running portal helper. They
read the existing user manager's graphical environment and check canonical,
owned Wayland/user-bus sockets and the active graphical-session target.
Unavailable or stale session state stops launch with a reason. It does not
create a desktop, unlock KDE, request screen access, or change manager settings.
The application's own display driver remains unchanged; Godot may use XWayland
inside the KDE Wayland session. Only selected task processes receive the
accessibility environment.

The project doctor reads bounded regular `project.godot` metadata and discovers
executables. It reports declared minimum engine requirements and C#/.NET tool
presence without running an engine, importing assets, executing plugins, or
writing state. Features come from the application's `config/features` string
array; multiline declarations are supported, while ambiguous or malformed
declarations stop diagnosis/launch instead of silently selecting the wrong engine.
Minimum features are not an exact engine pin. Runtime version,
imports, native-extension compatibility, GPU, sound, and input stay unverified.
Use `--engine /absolute/engine` or `--engine EXECUTABLE` for an explicitly chosen
runtime; C# defaults to `godot-mono` and also requires a discoverable `dotnet`
command. SDK installation and compatibility still need project verification.
Setup still installs the standard repository Godot package only.

For optional read-only export prerequisites, run:

```fish
basaltw desktop --native develop doctor --project /absolute/project --export-check --json
```

This opt-in runs only the selected engine's `--version`, from `/` with no
project arguments, a three-second probe deadline and 4096-byte output cap.
Probe cleanup can add bounded process termination time. The default doctor
still executes no engine. `project.engine_version` retains the exact observed
version; `project.export_check` reports the expected Godot 4 template directory,
installed version/file inventory, `mismatching_templates`, `missing_templates`
and `truncated`. The template key includes patch/release status and mono/double
features, but excludes the build label and commit hash, following
[Godot's version configuration](https://github.com/godotengine/godot/blob/master/core/version.h).

Inventory covers `$XDG_DATA_HOME/godot/export_templates` (default
`~/.local/share/godot/export_templates`), or `editor_data/export_templates`
beside a self-contained engine with `_sc_`/`._sc_`. It examines at most 1024
entries total across version directories and their immediate children, without
following child symlinks or reading template binaries. A missing, empty, or
metadata-only matching directory fails the diagnostic; other installed versions
are reported as mismatching and do not satisfy it. Additional old versions do
not fail an otherwise matching installation. Truncation and unsupported version
formats fail conservatively. Custom template paths, nested payloads, preset
requirements, template contents and build provenance remain unverified; a
matching directory with a nonempty template file establishes presence only.
No templates are downloaded, no export runs, and no project/cache/state is
written. `export_readiness` remains `unverified`; use the project's reviewed
export checks on its build system for actual compatibility.

Use `develop import` to prepare the selected project's assets explicitly. It
runs `--headless --import`, waits for imports, then exits; Godot can execute
project editor plugins and write its `.godot` cache during this operation.
Wait for the import task to complete before starting dependent tests. Imports
are never part of doctor or dry-run and do not establish gameplay correctness.

`editor` and `run` accept `--headless`, `--quit-after 120`, and
`--rendering-method forward_plus|mobile|gl_compatibility` before the project
arguments. Headless uses Godot's headless display and dummy audio driver.
`--quit-after` accepts 1–1000000 engine iterations; it is an iteration bound,
not a wall-clock timeout or a promise that the project passed assertions.
A renderer override applies to that launch without changing project settings.
Check the selected engine's supported options and retain its actual backend
observations. See the [Godot command-line reference](https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html).

```fish
basaltw desktop --native develop run --project /absolute/project \
  --headless --quit-after 120 --scene res://tests/smoke.tscn --json
basaltw desktop --native develop run --project /absolute/project \
  --rendering-method gl_compatibility --quit-after 120 --json -- --fixture menu
```

Each explicit launch returns a task ID, unit, private directory/log, and current
state. A user-systemd service owns the task's process group and follows the
graphical-session target. T3/SSH disconnection does not stop it. Logout/reboot
recovery is not provided. An initial `running` state proves service activity,
not window readiness or a completed playtest. Applications that forward a
request to an existing instance are not adopted into the new task. An
unacknowledged launch or failed initial unit inspection retains the task ID as
`launch-unverified`; inspect that task before retrying to avoid duplicate work.

```fish
basaltw desktop --native develop list --project /absolute/project --json
basaltw desktop --native develop status TASK_ID --json
# Use the returned task ID; wait does not stop it on timeout:
basaltw desktop --native develop wait TASK_ID --timeout 120 --json
# Save task-owned editor changes first:
basaltw desktop --native develop stop TASK_ID --dry-run --json
basaltw desktop --native develop stop TASK_ID --json
```

`list` reads saved records without querying or activating services. It returns
up to 50 records from at most 500 entries and marks incomplete results with
`truncated`; use `status` for live service/completion evidence. `launch_pid`,
when present, is the original command's PID and can be historical; it is not a
window identity. Status also returns a read-only `processes` snapshot with the
unit's `supervisor_pid`, `control_group` and `tasks_current` (threads as well as
processes). Missing/unavailable metadata is null. These observations can become
stale immediately; cleanup always uses the recorded unit/invocation, never a
historical PID. The stop command checks the recorded transient unit and
invocation before stopping its process group. A slow/unacknowledged stop needs
a fresh status check. An acknowledged stop queues shutdown without waiting for
the full grace period; `stopping` and `stop_requested_at` describe that request.
Check `status` again for `stopped`. Task descendants have a ten-second service
stop bound; the supervisor remains responsive even if an app closes its output.
Stop previews validate identity without changing the task. It never restarts
KDE, Sunshine, T3, or other tasks.

If the user manager is unavailable or unit identity cannot be verified, read-only
status still returns private log paths and saved exit evidence. `state` is
`unverified`, `recorded_state` describes saved completion evidence, and
`service_error` explains the failed live check. `processes` is null in this case.
It does not claim that the process
group has ended. Stop and stop previews require a verified unit response.
Missing or unsafe Xauthority files are omitted from the task environment;
unvalidated DISPLAY/XAUTHORITY values from the manager are explicitly removed.
An owned Wayland session remains usable, while XWayland readiness still needs
application verification.

Records live under `~/.local/state/basaltwater/development/TASK_ID` with directory
mode 0700 and files 0600. Combined stdout/stderr is capped at 16 MiB; further
output is drained and completion reports `log_truncated`. This bound covers the
supervisor's log, not files the project itself creates. Project exit failures
remain task failures without marking the supervisor service failed when it
successfully retained evidence. Logs/records are retained after stop; review
them privately and remove only an exact finished task directory when no longer
needed. Finished successful supervisor units are unloaded automatically after
their process group exits; saved records still provide status and exit evidence.
There is no log/record cleanup timer.

`wait TASK_ID --timeout SECONDS --json` repeatedly reads the same verified
status, at most twice per second. The timeout is an integer from 1 through
3600 seconds (default 600). It emits one final JSON status with the existing
log/report paths and `wait_timed_out`. Exit zero requires verified `completed`
state and a successful recorded exit. Failed, stopped, unavailable or unverified
tasks return nonzero immediately. Deadline expiry also returns nonzero, retaining
the observed state; it never stops or relaunches the task. Each status probe has
its existing three-second deadline plus bounded process cleanup, so an in-flight
probe can delay return beyond the wait deadline. An uncertain/expired wait needs
inspection of the same task handle before further dependent work.

Status includes a `report` path to `result.json`. New completion reports retain
`started_at`, `finished_at` and monotonic `elapsed_seconds` for the supervised
command, including output collection and shutdown. Duration remains null until
worker completion and for older reports without timing evidence. This measures the worker's
lifetime, not later surviving descendant activity or gameplay frame timing.

Arguments after `run ... --` are project arguments. A selected scene must be an
existing `.tscn`/`.scn` within the project, including after symlink resolution.
Generic `exec` commands run an explicit executable and argument vector, without
an implicit shell. Arguments are limited to 100 entries, 4096 characters each,
and 48 KiB serialized in total. `--dry-run` validates prerequisites/arguments
without creating task records or services.

Screen capture and input retain their separate portal consent, session generation,
geometry, and human control revision checks; see [native desktop control](cachyos-desktop.md)
and [persistent desktop control](desktop-control.md). Agent launches and
mutations follow the remembered enabled, human-override, or disabled state.
Before the owner playtests through Moonlight, the owner must explicitly choose
Take control in the UI or approve a state change. A task request alone does not
authorize changing state. A launched game can keep running after the portal
helper stops.

Prefer project-defined playtest fixtures and input actions. Capture the tested
revision/worktree, scene, engine, renderer, resolution, seed, logs, and expected
outcome together. The existing
[graphics recipe runner](visual-comparisons.md#run-a-project-check) retains
reviewable evidence. Queue it as a native development task to recover the
existing KDE environment and retain results after the launch client exits:

```fish
basaltw desktop --native develop check playtest-smoke --project /absolute/project \
  --settings /absolute/playtest-settings.json --timeout 120 --dry-run --json
basaltw desktop --native develop check playtest-smoke --project /absolute/project \
  --settings /absolute/playtest-settings.json --timeout 120 --json
basaltw desktop --native develop status TASK_ID --json
```

`check` uses the existing repository-root `basaltwater-agent.json` recipes.
Declare a reviewed project harness, for example:

```json
{
  "version": 1,
  "recipes": {
    "playtest-smoke": {
      "description": "Import assets, run the fixed menu fixture and assert its observations",
      "argv": ["python3", "tools/playtest_smoke.py"],
      "requires": ["python3", "godot"]
    }
  }
}
```

The project harness owns import ordering, input actions, assertions, expected
errors, captures and structured runtime observations. Read its settings from
`BASALTWATER_VISUAL_SETTINGS` and write evidence under
`BASALTWATER_VISUAL_EVIDENCE`. For Godot, record actual scene/renderer/device,
dimensions, engine version, seed and observed state, then exit nonzero when an
assertion fails. For Electron/native clients, use the same recipe interface
with the project's reviewed test runner. Recipe arguments are literal; shell
logic belongs in an explicit reviewed script. Basaltwater supplies no generic
Godot debugger or input-action bridge.

Native `check` validates the recipe, required executable presence, directory,
settings and 1–3600 second deadline before queueing. Its dry-run omits the
eventual private `--output` path because no task directory is allocated.
`status` returns `recipe`, `evidence` and `check_report` paths; the latter points
to `TASK_DIRECTORY/check/check.json`. Inspect that report and its `check.log`,
`environment.json`, `settings.json` and project artifacts after completion.
The report records commit/dirty state at check start; it does not snapshot or
freeze a checkout being edited concurrently. The recipe inherits the task's
recovered native environment and active toolchain; select a project runtime
explicitly inside the harness when necessary.

### Prepare assets in a fresh worktree

Add `godot_prepare` to the recipe in `basaltwater-agent.json` to opt into
reusing local assets and importing them before the native check. For example,
a `playtest-smoke` recipe can declare these paths.

```json
"godot_prepare": {
  "project": "game",
  "asset_directories": ["assets/audio", "assets/models", "assets/textures", "assets/video"]
}
```

`project` is relative to the repository and defaults to `.`.
`asset_directories` are relative to that Godot project and must be separate,
Git-ignored and untracked. Commit the ignore rules for them and for `.godot/`
at both revisions. Supply an approved directory outside the checkout with the
same asset layout. Keep the bundle path in the invocation so it can vary by
workstation.

```fish
basaltw desktop --native develop check playtest-smoke --project /absolute/worktree \
  --prepare --asset-bundle /absolute/local-qa-bundle --timeout 1200 --json
```

The preparation report lists present, staged and missing directories. A
missing bundle entry blocks import and the recipe. An existing worktree
directory is reused as a whole. The command copies only missing directories
and refuses symlinked staging paths or bundle contents. Each worktree's
headless import writes its own `.godot` cache. If all declared directories
are already present, `--prepare` can run without `--asset-bundle`.

Use `develop status TASK_ID --json` to find `prepare_report` and `check_log`.
The report distinguishes planning, staging and importing. Follow `check_log`
for Godot's live import output. `--timeout` applies separately to the import
and recipe, and `develop wait` has its own wait deadline. A failed or timed-out
import leaves evidence for inspection and prevents the GPU recipe from starting.
Preparation checks both Godot's import diagnostics and retained invalid
`.import` records for existing assets. These can indicate failure even when
Godot exits zero. Correct the named asset and reimport it in Godot, or repeat
the check in a fresh worktree with the corrected assets.

### Compare the native check across revisions

```fish
basaltw desktop --native develop compare playtest-smoke --project /absolute/project \
  --before main --after HEAD --settings /absolute/playtest-settings.json \
  --prepare --asset-bundle /absolute/local-qa-bundle --timeout 1200 --json
```

This queues one desktop task. It pins both commits at submission and runs the
declared recipe at each commit in a separate managed worktree, baseline first,
with the same settings snapshot. The worktrees, import logs and checks are
retained. Use `develop status TASK_ID --json` to find `comparison_report`,
which links to each side's log and report as it runs. Match stable case IDs
using the [case checkpoint format](visual-comparison-capture.md#retain-case-results-and-captures).
Both revisions must declare the named recipe with matching argv, working
directory, tool requirements, and tool version check. With `--prepare`, the
Godot preparation declaration must also match. A declaration mismatch is
reported before launching the candidate recipe. Omit `--prepare` for a recipe
that handles its own prerequisites.

The comparison classifies `regressed`, `persisted`, `fixed`, and unchanged
cases. An incomplete run or renderer failure leaves case comparisons `unverified`
while preserving the observed status from each side. Complete assertion failures
on the baseline still provide comparison evidence. Incomplete runs, renderer
failures and regressions make the comparison exit nonzero. A complete
pair with persistent failures and no regressions can pass the comparison.
Inspect both check reports for actual renderer, settings and test outcomes.

### Exercise the desktop and collect evidence

Use headless checks for the assertions they actually
exercise; verify rendering, audio, focus, and controller behavior on the real
desktop/GPU. Separate game frame times from streaming/encoding/client latency.

Measure GPU tasks sequentially: finish asset imports, launch one measured
recipe/run, then use `develop wait TASK_ID --timeout SECONDS --json` and inspect
its project-owned assertions, logs and report before launching the next sample.
Do not overlap imports, shader warmup, measured runs or exports; concurrent GPU
work makes timing comparisons ambiguous. Retain each sample's revision, exact
engine, renderer/device, scene/settings/seed, warmup/cache state and project
frame-time measurements. Worker `elapsed_seconds` includes startup/output
collection/shutdown and is not frame time. A zero wait exit establishes process
completion only; it does not replace project assertions or visual acceptance.
If wait expires or returns uncertain evidence, inspect that same task and finish
or explicitly stop it before measuring another sample.

For Electron, launch one declared package script using the project's committed
Node/package-manager requirements and preserve its sandbox policy:

```fish
basaltw desktop --native develop node dev:electron --project /absolute/electron-project \
  --json -- --development
basaltw desktop --native develop exec --project /absolute/native-project \
  --json -- /absolute/native-project/bin/application --development
```

`node SCRIPT` requires a declared `package.json` script. It uses this
Basaltwater installation's `node exec --project PATH` to select an already
installed runtime from `.node-version`, `.nvmrc` and `engines.node`. It selects
npm/pnpm/yarn from `packageManager`, defaulting to npm when undeclared;
`--manager` overrides the choice. This selects the manager name; verifying its
exact declared version remains the project's toolchain responsibility.
Arguments after `--` go to the script using
the selected manager's argument convention. It does not install a runtime,
package manager or project dependencies, disable Electron's sandbox, or add a
shell around the launch; the package manager executes the reviewed script.
Corepack network downloads are disabled for this task. Missing/incompatible
runtime or manager errors are retained in its log. `doctor` lists bounded
package-script names and declared requirements without executing them; it keeps
Node/runtime readiness unverified. Use `basaltw node doctor --project PATH
--json` for explicit runtime diagnosis.

The supervisor preserves the caller's PATH and optional validated NVM_DIR for
an explicitly selected project runtime; it does not import the caller's provider
credentials or bus/display overrides into the user manager. Task launches run trusted project code with
the normal account's access; they are not a sandbox.

For native applications, use project-owned run/test commands and check file
dialogs, keyboard navigation, scaling, fonts, and fullscreen behavior as needed.
Asset workflows should retain editable sources and verify exported assets in
the consuming Godot or client project.

## Launch a recorded playtest profile

Declare the command and any fixture files in the project's `basaltwater-agent.json`.
`seed` maps paths in the disposable profile to regular files in the project.
Use reviewed synthetic fixtures. Paths are relative and fixture symlinks are
refused. A profile can seed up to 64 files totaling 16 MiB.

```json
{
  "version": 1,
  "playtests": {
    "training": {
      "argv": ["./bin/game", "--training"],
      "seed": {"data/example-game/save.json": "tests/fixtures/training-save.json"}
    }
  }
}
```

```bash
basaltw desktop --native develop playtest training --project /absolute/project --dry-run
basaltw desktop --native evidence start --project /absolute/project
basaltw desktop --native develop playtest training --project /absolute/project
```

`agent manifest` exposes the commands under `development.playtest_argv`.
Check `desktop status` for the running helper's `recorded-playtest` capability.
Each launch gets a fresh private `profile` directory inside its supervised task
directory. The command receives that path as `BASALTWATER_PLAYTEST_PROFILE` and
gets its own `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and
`XDG_STATE_HOME`. For games that use other paths, declare a reviewed launcher
that passes the profile path to the game's own save/config flags. Verify that the
game uses those paths before a real run. It runs with the desktop account's
ordinary filesystem and network access.

Enabled agent control and an active recorder for the same resolved project
are required to launch. The recorder checkpoints the task ID before submission.
Keep the returned task, profile and recording paths for closeout. The profile
stays with the task records for retention and cleanup. A dry run validates the
declaration, executable and fixtures without creating a task, profile or recorder
entry. Debian XRDP uses the same declaration and commands without `--native`.

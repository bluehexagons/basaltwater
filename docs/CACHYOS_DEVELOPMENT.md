# Remote development on CachyOS

Use these workstations primarily to edit, run, playtest, and debug Godot games
and applications. Electron and other native client software are supported
secondary workflows. Production builds and publishing belong on separate build
systems; containers and isolated project environments are not part of this
workstation role. Managed Git worktrees remain useful for concurrent editing.

The owner logs into the existing KDE Wayland account initially. Subsequent work
normally uses T3 Code, Sunshine/Moonlight, or SSH. Keep that desktop session and
the user's applications intact. SSH access alone does not establish a usable
graphical session. Basaltwater does not configure automatic login, unlock the
desktop, or start a pre-login display.

## Prepare the workstation

Start with the [CachyOS installation guide](CACHYOS.md). Preview the selected
tools as the existing desktop account, without sudo:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --godot \
  --game-dev --git-lfs --sunshine --dry-run
```

Add `--node --node-versions` for Electron/TypeScript work, `--python` for project
tools, and selected creative applications for asset work. `--game-dev` supplies
native libraries and development/debugging tools; `--godot` selects the native
engine package. Project-specific C#/.NET and GDExtension requirements need their
own checks. The [software guide](CACHYOS_SOFTWARE.md) and
[native/Electron guide](CACHYOS_GAME_DEVELOPMENT.md) describe package scope.
Moonlight belongs on the connecting client unless this workstation also needs
to act as a streaming client. Pair Sunshine and configure the intended access
policy through the existing setup and maintenance procedures.

## Verify remote access before leaving the machine

Use the actual remote client to verify each selected access path independently:

1. Connect through Moonlight; confirm video, game audio, keyboard, pointer,
   controller input when used, and reconnection to the existing desktop.
2. Open a T3 provider thread and terminal. Verify provider credentials and
   executable discovery from the KDE-launched app, not only from SSH.
3. Connect through SSH as the same desktop account. Keep it available for
   diagnostics when streaming or a graphical application fails. SSH service
   and firewall configuration are separate from selecting Sunshine.
4. If agent desktop control is wanted, approve the selected monitor and devices
   with `basaltw desktop --native start --remember`. Test restoration from the
   intended T3 context. A saved grant can still require renewed approval; handle
   its KDE dialog through Moonlight when streaming is available.
5. Check monitor-off behavior, desktop locking, and stream disconnection with
   the owner present. Record which cases work; logout/reboot, display changes,
   and different hardware need separate qualification.

T3 desktop and selected Sunshine start after KDE login. A normal quit can leave
either stopped until the next login. Read
[startup and recovery](CACHYOS_MAINTENANCE.md) before changing services remotely.
Target the affected service; preserve unrelated editors, projects, pairings,
and application settings. Reboot and logout require a plan for the next login.

## Edit, run, inspect, and hand back

Use the project's actual checkout or [managed worktree](PROJECT_TOOLING.md).
Consult its `AGENTS.md`, engine requirements, launch arguments, test recipes,
and save-data conventions before executing project code.

```fish
basaltw agent manifest /absolute/project --json
basaltw local cachyos-doctor --json
basaltw desktop --native develop doctor --project /absolute/project --json
basaltw desktop --native develop editor --project /absolute/project --dry-run
basaltw desktop --native develop editor --project /absolute/project --json
basaltw desktop --native develop run --project /absolute/project \
  --scene res://scenes/playtest.tscn --json -- --fixture menu
```

`develop` works from T3 or same-user SSH without a running portal helper. It
reads the existing user manager's graphical environment and checks canonical,
owned Wayland/user-bus sockets and the active graphical-session target.
Unavailable or stale session state stops launch with a reason. It does not
create a desktop, unlock KDE, request screen access, or change manager settings.
The application's own display driver remains unchanged; Godot may use XWayland
inside the KDE Wayland session. Only selected task processes receive the
accessibility environment.

The project doctor reads bounded regular `project.godot` metadata and discovers
executables. It reports declared minimum engine requirements and C#/.NET tool
presence without running an engine, importing assets, executing plugins, or
writing state. Minimum features are not an exact engine pin. Runtime version,
imports, native-extension compatibility, GPU, sound, and input stay unverified.
Use `--engine /absolute/engine` or `--engine EXECUTABLE` for an explicitly chosen
runtime; C# defaults to `godot-mono` and also requires a discoverable `dotnet`
command. SDK installation and compatibility still need project verification.
Setup still installs the standard repository Godot package only.

Each explicit launch returns a task ID, unit, private directory/log, and current
state. A user-systemd service owns the task's process group and follows the
graphical-session target. T3/SSH disconnection does not stop it. Logout/reboot
recovery is not provided. An initial `running` state proves service activity,
not window readiness or a completed playtest. Applications that forward a
request to an existing instance are not adopted into the new task. An
unacknowledged launch or failed state inspection retains the task ID as
`launch-unverified`; inspect that task before retrying to avoid duplicate work.

```fish
basaltw desktop --native develop list --project /absolute/project --json
basaltw desktop --native develop status TASK_ID --json
# Save task-owned editor changes first:
basaltw desktop --native develop stop TASK_ID --dry-run --json
basaltw desktop --native develop stop TASK_ID --json
```

`list` reads saved records without querying or activating services. It returns
up to 50 records from at most 500 entries and marks incomplete results with
`truncated`; use `status` for live service/completion evidence. `launch_pid`,
when present, is the original command's PID and can be historical; it is not a
window identity. The stop command checks the recorded transient unit and
invocation before stopping its process group. A slow/unacknowledged stop needs
a fresh status check. Stop previews validate identity without changing the
task. It never restarts KDE, Sunshine, T3, or other tasks.

Records live under `~/.local/state/basaltwater/development/TASK_ID` with directory
mode 0700 and files 0600. Combined stdout/stderr is capped at 16 MiB; further
output is drained and completion reports `log_truncated`. This bound covers the
supervisor's log, not files the project itself creates. Project exit failures
remain task failures without marking the supervisor service failed when it
successfully retained evidence. Logs/records are retained after stop; review
them privately and remove only an exact finished task directory when no longer
needed. There is no cleanup timer.

Arguments after `run ... --` are project arguments. A selected scene must be an
existing `.tscn`/`.scn` within the project, including after symlink resolution.
Generic `exec` commands run an explicit executable and argument vector, without
an implicit shell. Arguments are limited to 100 entries, 4096 characters each,
and 48 KiB serialized in total. `--dry-run` validates prerequisites/arguments
without creating task records or services.

Screen capture and input retain their separate consent, generation, geometry,
and lease checks; see [native desktop control](CACHYOS_DESKTOP.md). Launches
honor a saved or active human pause. Pause agent control before the owner
playtests through Moonlight, and wait for explicit handback before resuming
input. A launched game can keep running after the portal helper stops.

Prefer project-defined playtest fixtures and input actions. Capture the tested
revision/worktree, scene, engine, renderer, resolution, seed, logs, and expected
outcome together. The existing
[graphics recipe runner](VISUAL_COMPARISONS.md#run-a-project-check) retains
reviewable evidence. Use headless checks for the assertions they actually
exercise; verify rendering, audio, focus, and controller behavior on the real
desktop/GPU. Separate game frame times from streaming/encoding/client latency.

For Electron, use the project's package scripts and committed Node/package
manager requirements via `basaltw node exec`; preserve its sandbox policy.
For example, launch a reviewed project script in the existing desktop:

```fish
basaltw desktop --native develop exec --project /absolute/electron-project \
  --json -- basaltw node exec -- npm run dev:electron
basaltw desktop --native develop exec --project /absolute/native-project \
  --json -- /absolute/native-project/bin/application --development
```

The supervisor preserves the caller's PATH for an explicitly selected project
runtime; it does not import the caller's provider credentials or bus/display
overrides into the user manager. Task launches run trusted project code with
the normal account's access; they are not a sandbox.

For native applications, use project-owned run/test commands and check file
dialogs, keyboard navigation, scaling, fonts, and fullscreen behavior as needed.
Asset workflows should retain editable sources and verify exported assets in
the consuming Godot or client project.

## Development capability roadmap

The [delivery plan](plans/CACHYOS_AGENTIC_DESKTOP.md) prioritizes project-aware
launches and remote recovery, Godot runtime/debugger integration, repeatable
GPU playtests, display/input qualification, multiplayer orchestration,
performance evidence, human handoff, and durable development tasks. Browser
and broader native-app workflows remain useful for the secondary client-software
case. These are implementation slices, not claims that every capability is
already available. Record live results in the
[qualification record](plans/CACHYOS_AGENTIC_DESKTOP_QUALIFICATION.md).

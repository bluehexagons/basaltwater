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
basaltw desktop --native status
# With an already approved running portal session:
basaltw desktop --native exec -- godot --editor --path /absolute/project
```

The existing portal launch requires running control. It enables task-process
accessibility without changing KDE preferences. Screen capture and input keep
their separate consent, generation, geometry, and lease checks; see
[native desktop control](CACHYOS_DESKTOP.md). Pause agent control before the
owner playtests through Moonlight, and wait for explicit handback before
resuming input. A launched game can keep running after the portal helper stops.

Prefer project-defined playtest fixtures and input actions. Capture the tested
revision/worktree, scene, engine, renderer, resolution, seed, logs, and expected
outcome together. The existing
[graphics recipe runner](VISUAL_COMPARISONS.md#run-a-project-check) retains
reviewable evidence. Use headless checks for the assertions they actually
exercise; verify rendering, audio, focus, and controller behavior on the real
desktop/GPU. Separate game frame times from streaming/encoding/client latency.

For Electron, use the project's package scripts and committed Node/package
manager requirements via `basaltw node exec`; preserve its sandbox policy.
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

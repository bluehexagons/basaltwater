# Electron tests across development platforms

Choose the display and runtime for the check. Keep the project's dependency
pins, sandbox policy and assertions on every platform. The script names below
are examples, not scripts supplied by Basaltwater.

| Environment | Unattended checks | Native acceptance |
| --- | --- | --- |
| Debian VM | Isolated Xvfb/X11 recipe below | Intended XRDP session; representative target hardware for performance |
| CachyOS | Isolated Xvfb/X11 for software references | Existing KDE Wayland session, with Xwayland tested separately when supported |
| Native Windows | Project's Windows test command; supervised task for durable runs | Intended interactive Windows account and actual target GPU |
| Ubuntu WSL | Linux test command with explicit Xvfb isolation | WSLg is Linux GUI coverage; test the Windows build natively too |

Keep software reference, native interaction and performance results separate.
Use disposable test profiles/data rather than a personal application profile.

## Linux display isolation

Use an isolated Xvfb display for unattended Electron integration, capture and
stress checks. An inherited desktop `DISPLAY` can send a harness to the shared
desktop and cause graphics staging failures or renderer startup timeouts.
Select the display explicitly before changing application timeouts.
Interactive desktop behavior still needs a separate shared-session check.

### Prerequisites and display selection

Basaltwater's Debian `--game-dev` bundle includes Xvfb, xauth, Mesa diagnostics
and Electron runtime libraries; see
[Debian native tooling](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/project-tooling.md#debian-native-game-development).
Without Basaltwater, install `xvfb`, `xauth`, `mesa-utils` and the runtime
libraries required by the project's pinned Electron version through Debian's
package manager. Keep the project's lockfile, Node/Electron pins and install
command. Run as the ordinary coding account with the project's sandbox enabled;
Xvfb is not a reason to add `--no-sandbox` or disable the GPU sandbox.

Choose one display owner. The following npm script names are examples; replace
them with the project's documented checks.

If the project harness already starts Xvfb when `DISPLAY` is absent:

```bash
env -u DISPLAY -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 npm run test:electron
```

Unsetting `DISPLAY` selects that harness's headless path; it does not start
Xvfb itself. Inspect the harness first. If it launches Electron directly,
wrap the entire test command instead:

```bash
# Query once per invocation; newer wrappers can allocate a display atomically.
xvfb_allocation=-a
case "$(xvfb-run --help)" in
  *--auto-display*) xvfb_allocation=-d ;;
esac
env -u DISPLAY -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 \
  xvfb-run "$xvfb_allocation" \
  --server-args='-screen 0 1280x720x24 -nolisten tcp' \
  npm run test:electron
```

`-a` is the fallback for Debian wrappers without `--auto-display`; serialize
these runs to reduce display-allocation races. Keep Xauthority authentication
enabled. The wrapper supplies the child `DISPLAY` and owns server cleanup.
Clear the inherited display **before** the wrapper; clearing it inside would
remove the newly allocated display. Do not nest Xvfb wrappers, hardcode `:99`,
borrow desktop authority cookies or launch these checks through `desktop exec`.
Remove Wayland advertisements only for the isolated child, preserving the
interactive shell and compositor. Configure the project's Electron launcher for
X11 as well if it explicitly selects Wayland: pass
[`--ozone-platform=x11`](https://www.electronjs.org/docs/latest/api/command-line-switches#--ozone-platformplatform)
to the Electron process when supported by the pinned version. An npm script may
not forward that argument to Electron; check its launch code. Environment
selection alone cannot override explicit application flags. SDL driver settings
do not select Electron's Chromium backend.

Keep resolution, scale, fixture/seed and render settings fixed in the project
recipe. Require application readiness, a rendered frame or interaction assertion,
and a successful exit; a running X server alone is not an application pass.
On failure, retain startup/renderer logs and the selected display/backend before
retrying. Check owned processes after a timeout rather than relaunching blindly.

### CachyOS KDE

For isolated tests, install/discover `xorg-server-xvfb`, `xorg-xauth` and
`mesa-utils` through the workstation's normal package workflow, then run the
Linux recipe in a project shell or put it inside the project-owned test script.
Do not switch KDE's compositor or change global display variables.

For native interaction, keep the owned KDE session and use
`basaltw desktop --native develop node test:electron --project /absolute/project`
when that declared script is the intended desktop check. Native `develop`
recovers the user manager's graphical environment, including Wayland/X11
advertisements. Clearing variables before invoking it does not guarantee
isolation; a supervised isolated test must clear them inside its test script,
before allocating Xvfb and launching Electron. Keep native Wayland, Xwayland
and Xvfb observations distinct. The native task's status/logs and project frame
assertions remain the completion evidence; see the
[development reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-cachyos-desktop/references/development.md).

## Native Windows

Use the project's native Windows checkout, installed runtime and PowerShell 7
command. Do not install Xvfb, clear Linux display variables or substitute a WSL
build to qualify a Windows executable. For a direct check with the correct Node
already selected, use the project's actual script and propagate its exit code:

```powershell
npm.cmd run test:electron
exit $LASTEXITCODE
```

Use this in a dedicated test script or child shell; `exit` closes that shell.
For a durable run, the native supervisor selects the project's installed pinned
Node runtime and retains bounded logs:

```powershell
basaltw desktop develop node test:electron --project 'F:\Repos\game' --timeout 720
# Substitute the returned task ID in these follow-up commands.
basaltw desktop develop wait TASK_ID --timeout 60
basaltw desktop develop logs TASK_ID
```

A caller wait timeout leaves the task running; continue with the same task ID.
Require confirmed successful completion and the project's assertions, then
inspect retained/truncated byte counts before sharing logs. These interactive
tasks do not establish unattended service, locked-desktop or disconnected RDP
rendering support. Record session/lock/reconnect state when testing those cases.
PrintWindow may capture blank hardware-rendered content; validate an actual
application frame and use the authorized project/monitor capture route when
needed. See [Windows desktop tasks](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-desktop.md)
for runtime selection, cleanup and capture limits.

## Ubuntu WSL and WSLg

Keep Linux tools and dependencies in the selected Ubuntu distribution and its
Linux checkout. WSLg supplies X11/Wayland displays for Linux GUI apps; its
[graphics acceleration](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gui-apps)
depends on the Windows host driver and WSL configuration. A display variable
or installed GPU driver is not evidence of the application's active renderer.
For isolated Linux checks, use the Linux recipe above, removing both inherited
display advertisements in the child. For WSLg integration, retain its supplied
environment and report the observed backend. Do not replace WSLg display/socket
configuration or borrow Windows desktop credentials to make a check pass.
The WSL workspace profile does not provision the Debian game bundle: install
native Ubuntu prerequisites explicitly when needed. Record WSL/distribution and
host Windows versions with graphics evidence; WSLg success does not qualify the
native Windows package, input, audio or target performance.

## Graphics evidence

On Linux hosts with Basaltwater's graphics command installed, probe the isolated
context deliberately:

```bash
basaltw agent graphics doctor --context xvfb --json
# For a software reference that must reject unknown/accelerated contexts:
basaltw agent graphics doctor --context xvfb --require-software --json
```

Without that command, run `glxinfo -B` inside the selected Xvfb wrapper and retain
its renderer/OpenGL/acceleration fields. Neither command is a native Windows
diagnostic. The Basaltwater report includes renderer, OpenGL version,
acceleration and context creation;
unknown acceleration stays unknown. This GLX probe is separate from Electron's
Chromium/ANGLE render path. In the project test logs, retain Electron/Chromium
versions, launch flags, actual renderer/backend and graphics feature status.
Electron exposes [`app.getGPUFeatureStatus()`](https://www.electronjs.org/docs/latest/api/app#appgetgpufeaturestatus)
after `gpu-info-update`, and
[`app.getGPUInfo('complete')`](https://www.electronjs.org/docs/latest/api/app#appgetgpuinfoinfotype)
for device/driver details. Collect these through the project's main-process
test harness and check the renderer used by the application's actual frame.
Installed graphics libraries and requested software flags do not prove an
accelerated or software renderer. Record llvmpipe/SwiftShader as software when
observed; Xvfb success does not qualify physical display, HDR or input latency.

## Long runs and performance acceptance

Keep the existing retained-output helper around the chosen command. For a
Linux project harness that owns Xvfb, a ten-minute stress run could use:

```bash
python3 /absolute/skill-directory/scripts/capture_output.py --timeout 720 -- \
  env -u DISPLAY -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 \
  npm run test:stress -- --duration 600
```

Replace the skill path and project arguments with their actual values. For a
direct launcher, put the explicit Xvfb wrapper after `--` instead. Allow startup
and shutdown time beyond the workload duration. The helper retains private
stdout/stderr logs and emits paths, byte counts, duration, completion status and
return code. It saves that result in `summary.json` before emitting it to the
terminal. Use `--output-directory NEW_PATH` before `--` to retain a known new
directory under an existing private parent outside Git. An initial `in_progress`
summary is an uncertain outcome after disconnection; inspect the owned run
before retrying. This foreground helper does not detach or supervise the job.
`completed` can include a nonzero child exit: require return code
zero and the project's completion assertions. Timeout (`124`) is a failure.
Bring only bounded diagnostic excerpts into agent context; preserve the logs.
For durable native CachyOS/Windows tasks use their supervisor's retained logs
and completion receipts instead of wrapping the submission command: timing a
submission measures dispatch, not the submitted workload.

Serialize performance work and record CPU model/vCPU count, available memory,
host contention when known, renderer, workload and elapsed/frame-time results.
An older four-core VM is useful for functional and sustained stability checks,
but software graphics also consumes CPU. Request more CPU capacity when builds
or profiling compete for it; additional vCPUs do not fix slow single-thread work.
Repeat performance acceptance on representative target CPU/GPU hardware with
the same workload and settings. Report VM and target results separately.
Record warmup, measured interval, sample count and frame-time distribution when
the project measures frames. Total worker duration includes startup/cleanup and
is not FPS or frame time. Report build, packaging, functional, stress, rendering
and performance outcomes independently; a ten-minute stable run alone supplies
no target frame-rate acceptance.

See Electron's [headless testing guide](https://www.electronjs.org/docs/latest/tutorial/testing-on-headless-ci)
for the native Xvfb approach. Project scripts own assertions and acceptance;
Basaltwater supplies host prerequisites and evidence tools.

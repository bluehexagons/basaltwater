# Electron tests on Debian VMs

Use an isolated Xvfb display for unattended Electron integration, capture and
stress checks. An inherited XRDP `DISPLAY` can send a harness to the shared
desktop and cause graphics staging failures or renderer startup timeouts.
Select the display explicitly before changing application timeouts.
Interactive desktop behavior still needs a separate shared-session check.

## Prerequisites and display selection

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
env -u DISPLAY npm run test:electron
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
env -u DISPLAY xvfb-run "$xvfb_allocation" \
  --server-args='-screen 0 1280x720x24 -nolisten tcp' \
  npm run test:electron
```

`-a` is the fallback for Debian wrappers without `--auto-display`; serialize
these runs to reduce display-allocation races. Keep Xauthority authentication
enabled. The wrapper supplies the child `DISPLAY` and owns server cleanup.
Clear the inherited display **before** the wrapper; clearing it inside would
remove the newly allocated display. Do not nest Xvfb wrappers, hardcode `:99`,
borrow desktop authority cookies or launch these checks through `desktop exec`.
If the project selects Wayland explicitly, configure its test launcher for X11
using its pinned Electron version's supported options and record those options.

Keep resolution, scale, fixture/seed and render settings fixed in the project
recipe. Require application readiness, a rendered frame or interaction assertion,
and a successful exit; a running X server alone is not an application pass.
On failure, retain startup/renderer logs and the selected display/backend before
retrying. Check owned processes after a timeout rather than relaunching blindly.

## Graphics evidence

Probe the isolated context deliberately:

```bash
basaltw agent graphics doctor --context xvfb --json
# For a software reference that must reject unknown/accelerated contexts:
basaltw agent graphics doctor --context xvfb --require-software --json
```

The report includes renderer, OpenGL version, acceleration and context creation;
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
project harness that owns Xvfb, a ten-minute stress run could use:

```bash
python3 /absolute/skill-directory/scripts/capture_output.py --timeout 720 -- \
  env -u DISPLAY npm run test:stress -- --duration 600
```

Replace the skill path and project arguments with their actual values. For a
direct launcher, put the explicit Xvfb wrapper after `--` instead. Allow startup
and shutdown time beyond the workload duration. The helper retains private
stdout/stderr logs and emits paths, byte counts, duration, completion status and
return code. `completed` can include a nonzero child exit: require return code
zero and the project's completion assertions. Timeout (`124`) is a failure.
Bring only bounded diagnostic excerpts into agent context; preserve the logs.

Serialize performance work and record CPU model/vCPU count, available memory,
host contention when known, renderer, workload and elapsed/frame-time results.
An older four-core VM is useful for functional and sustained stability checks,
but software graphics also consumes CPU. Request more CPU capacity when builds
or profiling compete for it; additional vCPUs do not fix slow single-thread work.
Repeat performance acceptance on representative target CPU/GPU hardware with
the same workload and settings. Report VM and target results separately.

See Electron's [headless testing guide](https://www.electronjs.org/docs/latest/tutorial/testing-on-headless-ci)
for the native Xvfb approach. Project scripts own assertions and acceptance;
Basaltwater supplies host prerequisites and evidence tools.

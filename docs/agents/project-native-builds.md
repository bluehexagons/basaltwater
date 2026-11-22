# Debian native development

[Documentation index](project-tooling.md)

## Debian native game development

Add `--game-dev` to a Debian coding VM or workstation setup for compilers,
CMake/Ninja, native dependency headers, shader checks, debugging/cache tools,
Xvfb captures, and GTK/NSS/audio/graphics libraries used by Electron.
For an existing saved setup, review and apply the patch from the controller:

```bash
basaltw patch HOST USER --game-dev --dry-run
basaltw patch HOST USER --game-dev
```

The bundle provisions host prerequisites through APT. Each project retains its
native library pins, bootstrap, compiler requirements and release SDK; run its
bootstrap and doctor after setup. Basaltwater never builds checkout code during
provisioning. CachyOS has its own
[`--game-dev` bundle](../workstations/cachyos-game-development.md) for development work.

GTK is a runtime prerequisite for Electron. Debian's `libgtk-3-bin`
pulls the release's GTK shared library, including its t64 package name, without
requesting `libgtk-3-dev`. Native GTK addon development is a separate project
requirement. Those headers can transitively require a D-Bus runtime version that
conflicts with newer libraries already installed alongside XRDP. The game
bundle excludes optional recommendations and refuses package removals; it does
not change APT pins or force downgrades to resolve conflicts. If APT reports
"held broken packages", inspect the named dependencies with `apt-cache policy`
and check `apt-mark showhold` separately rather than assuming an actual hold.
After updating the controller checkout, rerun the same saved setup with
`--game-dev` to recover a failed bundle install. Setup verifies the requested
packages before recording the bundle as selected.

The development doctor also inventories native commands and pkg-config module
versions without launching a window or testing GPU acceleration. A saved Debian
`--game-dev` selection makes missing host commands or dependency modules a
readiness failure. Missing project-built SDL modules are listed separately in
`project_bootstrap_required`. Other hosts still receive the inventory without
being required to install this bundle. Removing the selection retains installed
packages; an invalid selection record is reported for explicit repair.
The non-secret selection lives in
`/var/lib/basaltwater-development/game-development.json`, readable by coding
users while the main `/var/lib/basaltwater` runtime state remains private to
root. Rerun saved setup with `sudo basaltw refresh` to reconcile installations
that previously stored this flag inside the private runtime directory.

Bundle `selected` records the explicit setup choice; `installed` indicates that
managed bundle selection, while `commands` records individual tool presence.
None establishes project readiness. An unselected bundle with tools already
present is valid. Enable it deliberately for regular native work through the
saved setup; a diagnostic does not change that selection.
`module_probe_source: host-pkg-config` identifies the sanitized host search
environment used by Basaltwater. It does not describe SDL chosen by an existing
project CMake cache. When versions differ, inspect the project's `CMakeCache.txt`
and its own `pkg-config --modversion sdl3` / `pkg-config --variable=prefix sdl3`
under the intended project environment. Preserve project-built prefixes and SDK
pins rather than replacing system SDL to match a previous build.

`basaltw agent support-bundle` retains the same native readiness issues,
selection, command-presence flags, module versions, and project-bootstrap list
as the saved readiness report. Unknown fields, executable paths and unrecognized
module values are omitted from this shareable diagnostic evidence.

### Opt-in native graphics context check

On Debian or CachyOS, explicitly select the context to probe:

```bash
basaltw agent graphics doctor --context xvfb --json
# Require an observed software renderer before software reference work:
basaltw agent graphics doctor --context xvfb --require-software --json
# From the intended logged-in graphical session (GLX/Xwayland):
basaltw agent graphics doctor --context display --json
```

The first command starts an isolated Xvfb server with private Xauthority;
the second uses the caller's existing `DISPLAY`. For Debian XRDP, launch the
display check through `basaltw desktop exec -- basaltw agent graphics doctor
--context display --json`. On CachyOS, run it in the intended KDE session;
this GLX check does not qualify native Wayland. No setup, manifest, ordinary
development doctor or support collection starts this probe.

The bounded [glxinfo](https://dri.freedesktop.org/wiki/glxinfo/) probe reports
process outcome, context creation, renderer/OpenGL version, and acceleration.
Missing tools/display, a failed process, and exit success without a recognized
context are separate outcomes. Software Mesa can pass context readiness for
subsequent correctness captures; unknown acceleration remains `null`.
`software_renderer` is true for a recognized software renderer, false for an
observed accelerated context, and null when software selection is unverified.
`--require-software` keeps the context/process observations but returns nonzero
with `software_renderer_unverified` for an accelerated or unknown renderer.
An `Accelerated: no` field alone does not establish software selection.
The option checks the observed renderer; it does not force a driver.

Mesa's [renderer environment variables](https://docs.mesa3d.org/envvars.html)
configure requests such as `LIBGL_ALWAYS_SOFTWARE=1` and `GALLIUM_DRIVER=llvmpipe`;
their presence is not renderer evidence. Add `--backend egl --context offscreen`
to probe the implementation's default EGL display with an OpenGL pbuffer, or
`--backend egl --context surfaceless` for the explicit Mesa surfaceless platform.
`--expect-renderer SUBSTRING` asserts the actual renderer in either backend.
See [EGL diagnostics and inherited GPU selectors](../workstations/graphics-diagnostics.md).
A project using SDL offscreen/EGL must report and assert its own actual SDL driver and
GL renderer, even if the GLX probe passes. Fixtures that override the driver
to offscreen do not become X11 fixtures by running them inside Xvfb.
`rendering_verified` stays false until a project renders and checks a frame.
GPU timing, physical display, HDR and input/display latency remain unqualified
even after an accelerated context succeeds. Xvfb results describe that virtual
context, never the user's desktop. Software renderer VRAM counters are omitted.

### Project-owned compiler caching

Installed ccache does not mean a CMake project uses it. If project policy allows
caching, use a fresh build directory and retain the project's toolchain, SDL
prefix, and other required configure arguments:

```bash
cmake -S . -B build-cache -DCMAKE_C_COMPILER_LAUNCHER=ccache \
  -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
ccache --show-stats
cmake --build build-cache --target PROJECT_TARGET
```

CMake's [compiler launcher variables](https://cmake.org/cmake/help/latest/variable/CMAKE_LANG_COMPILER_LAUNCHER.html)
initialize the launcher for supported generators. Verify the resulting cache
and verbose compile commands. Compare ccache statistics and elapsed time before
and after repeating the same target with unchanged sources and equivalent
compile work; a no-op incremental build is not a cache benchmark. Builds that
require a clean rebuild should use the project's reviewed clean-build procedure.
Basaltwater does not set compiler launchers or reset global cache statistics.

### Effective workload limits

`basaltw agent resources --json` works on Debian and CachyOS without invoking
VM maintenance checks. Host/support diagnostics include the same
`workload_limits` record. It describes the calling process's CPU affinity,
tightest visible ancestor CPU quota in CPU equivalents, memory max/high in
bytes, and timestamped guest/workload pressure averages and cumulative stall
microseconds. A quota can be smaller than affinity. Missing or malformed
interfaces remain unavailable, with `null` values rather than zero resources.
`cgroup_version` distinguishes `v1`, `v2`, and `hybrid` mounts. On a hybrid
host, CPU and memory limits follow each controller's own hierarchy; a v2 mount
does not replace limits imposed through v1. A known quota remains a CPU-budget
upper bound even when affinity is unavailable; that missing measurement stays
explicitly unknown.
`ancestry_complete` refers to the visible cgroup mount; namespaces or restricted
mounts can hide tighter host ancestors. The CPU budget is an upper bound, not a
build-concurrency recommendation. v1 memory soft limits are not reported as
v2 `memory.high`. See the kernel's [cgroup](https://docs.kernel.org/admin-guide/cgroup-v2.html)
and [pressure](https://docs.kernel.org/accounting/psi.html) contracts.

Guest RAM capacity, this workload's inherited limits, and T3's
`service-with-descendants` memory counters answer different questions. Builds,
renders and agents can share a service budget. Choose concurrency using those
limits, memory and storage demand, concurrent work, and measurements of the
project's actual workload; raw guest CPU count alone is insufficient.

`memory_accounting` adds a timestamped, read-only breakdown for the invoking
process's cgroup on either OS. Debian host/support records also include a
separate `t3_service.memory_accounting` for the service cgroup reported by
systemd. Anonymous and file-backed bytes, swap, inherited max/high, memory
pressure and readable OOM event counts remain `null` when unavailable. v1 uses
hierarchical `total_rss`/`total_cache` rather than presenting local values as
descendant totals; its usage counter is approximate and unavailable swap/high
values are not invented. No kernel journal, process arguments or cgroup paths
appear in support snapshots.

OOM counters are cumulative evidence. `events.scope` distinguishes hierarchical
v2 counters from local v1 or `memory_localevents` counters. `counter_epoch`
identifies the observed cgroup directory by device/inode; T3 additionally
reports a validated service invocation and active-since monotonic timestamp.
Its `restart_count` is systemd's `NRestarts` count of automatic restarts,
excluding manual restarts, rather than a durable restart history; see the
[systemd contract](https://github.com/systemd/systemd/issues/29348).
Compare before/after samples from
the same known epoch, invocation and boot for job attribution. A single count
or exit 137 does not identify the killed job, event time or cause. Counter
changes narrow the interval; concurrent service descendants still require
corroborating job evidence. A changed cgroup directory during collection
leaves accounting unavailable. See the kernel's
[v2 event/statistics](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory)
and [v1 accounting](https://docs.kernel.org/admin-guide/cgroup-v1/memory.html).

T3's historical peak is compared with observed guest capacity for an advisory
warning, rather than smaller currently ballooned RAM. Peak-time capacity and
timestamp remain unknown. Its current high-share warning includes descendants
and file cache; available host memory, pressure, swap activity and actual limits
are needed to interpret it. Neither a high aggregate percentage nor a historical
OOM count establishes a leak or current exhaustion.

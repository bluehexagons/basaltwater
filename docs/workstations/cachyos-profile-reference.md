# Inspect and maintain the profile

[Documentation index](cachyos.md)

## Package and repository behavior

- Package state is checked with `pacman -Q`; missing packages use
  `pacman -S --needed`. Setup does not refresh package databases or perform a
  system upgrade. Use CachyOS's normal update workflow first, and never use
  `pacman -Sy` as a repair for a partial upgrade.
- Package installation and pruning old pacman downloads may prompt for sudo.
  Agent CLIs, the T3 web runtime, user-cache cleanup, and repository checks are
  otherwise noninteractive. No update or cleanup timers are installed.
- Package pressure recovery runs before installing tools; ordinary cache cleanup
  runs after tool readiness. If cleanup fails, setup reports an
  incomplete result and returns nonzero; installed tools are retained. Resolve
  the cleanup error and rerun setup. This profile has no automatic cache retry.
- This local profile saves a private successful selection for later maintenance,
  rather than a generic host configuration for `patch`, `deploy`, or `cmd`.
- If a CachyOS mirror or DNS lookup fails, fix the resolver or mirror through
  CachyOS's normal maintenance workflow and retry your selected installer.
- `--repo` clones only a missing repository. An existing destination must be a
  Git repository with the requested origin and must be writable by you; setup
  does not delete or repair conflicting directories.

## Cleanup during setup

Every setup run checks for package-cache pressure before installing tools and
performs ordinary cleanup after tool readiness. It installs
`pacman-contrib` for the distro's [paccache version selection](https://man.archlinux.org/man/paccache.8),
but does not enable `paccache.timer` or install another scheduled service.
Current CachyOS already offers cache cleanup in Shelly's Utilities page, and
`paccache` is available through its repositories. This setup pass adds the
conservative retention rules below; it does not replace or reconfigure existing
distro cleanup or update tools. AUR build caches, including Shelly's, stay intact.

- **Package downloads:** retain the newest three cached versions of each
  package, the currently installed version, and any archive accessed or
  modified within 30 days. Only recognized, root-owned regular archives and
  their signatures directly in `/var/cache/pacman/pkg` are eligible. Setup
  previews without sudo, then rechecks candidates before removal if needed.
  Package transaction locks defer cleanup. Private download directories,
  partial downloads, symlinks, custom cache locations, and AUR build trees are
  preserved. Packages are never uninstalled, including orphaned packages.
- **Low-space package recovery:** when `/var/cache/pacman/pkg` has less than
  2 GiB available or fewer than 10,000 free inodes, the early pass retains one
  cached version per package and seven days of accesses/modifications, plus
  every currently installed version. It uses the same path, ownership,
  transaction-lock and identity checks and stops selecting archives when
  capacity recovers. Already selected signatures finish with their archives.
  If `paccache` is not installed yet, the early pass reports that limitation;
  the later ordinary cleanup follows installation of `pacman-contrib`.
- **Developer caches:** reuse the shared user-cache policies for npm/npx, pip,
  uv, Go, Codex, and Electron downloads. Only known rebuildable caches are
  pruned; size/age limits and free-space checks control larger evictions.
  Active tool/process checks defer cleanup where applicable. Codex temporary
  files expire after seven days when Codex is idle; configuration, login files,
  sessions, databases, plugins, and repositories are preserved.
- **Agent releases and logs:** retain the current, rollback, and running Codex
  releases. T3 numbered log rotations are bounded to 14 days and 256 MiB per
  environment, covering both desktop `~/.t3` and the isolated managed web
  directory. Current logs and T3 databases are preserved.

Preview or apply package-cache cleanup from this checkout:

```fish
python3 common/cachyos_cleanup.py --dry-run
python3 common/cachyos_cleanup.py --pressure --dry-run
# Apply the pressure policy only while the cache filesystem is low:
sudo python3 common/cachyos_cleanup.py --pressure --apply
```

The helper reports candidate archive/signature counts. Setup's `--dry-run`
shows the cleanup steps without inventorying or changing the machine. Cleanup
errors make setup incomplete and request a rerun; installed tools remain in
place. Deferred cleanup can run on a later setup invocation.

## Skills, diagnostics, and boundaries

Codex and OpenCode receive the CachyOS workstation, workspace, desktop, shared
subagent, and (when T3 is selected or retained) T3 and T3-preview browser skills
under `~/.agents/skills`. Claude
receives the selected catalog under `~/.claude/skills`. Standard VM, XRDP, gateway,
managed-Playwright, and Godot-web skills are not installed. The preview skill uses
only tools exposed to the session and existing project/browser alternatives.
Personal skills are
preserved. The desktop skill covers autonomous Blender/Inkscape and other
application edits, scripting, editable sources and verified exports. The manifest
uses direct native launch commands instead of Debian XRDP commands.

The shared `basaltwater-subagents` skill provides the same local worktree
lifecycle, worker handoffs, and cost-aware OpenAI model guidance as Debian.
`basaltw agent workspace` and `basaltw agent models` run locally as the desktop
user, without SSH or provisioning. Project policy and reviewed outcomes let
agents evaluate new model IDs without upgrading Basaltwater. Use standard
service and supported safe efforts; fast service, `max`, and `ultra` require an
explicit user selection. See [subagent workflows and model choices](../agents/agent-subagents.md).

Selecting a supported desktop application installs Python GObject, AT-SPI,
GStreamer, its base/PipeWire plugins, GTK3, libxkbcommon and libkscreen
prerequisites. Setup starts no control helper. From KDE, use `basaltw desktop --native doctor`, then
`basaltw desktop --native start`; the owner must select one monitor and allow
keyboard/pointer access in KDE's portal dialog. `handoff` opens the human control
window even while the helper is stopped or agents are blocked. It offers enabled,
human override, and disabled states, plus separate stop/revoke controls. Human
override defaults to 15 minutes, with an indefinite option; permission survives
restarts. Backend lifetime is unlimited by default, with optional expiry through
`start --session-seconds` or `renew`. `stop` closes only automation, preserving KDE,
applications and agent permission. `start --remember` opts into a reusable portal
grant where KDE supports it, after initial owner approval; `revoke` removes saved
access. There is no login autostart. Enabled agents can request display wake with
`basaltw desktop --native wake`, then verify a fresh capture. See
[persistent control](desktop-control.md) and [native control and limitations](cachyos-desktop.md).

The read-only desktop doctor reports package versions, user-bus sockets, and
PipeWire, WirePlumber, the `t3code-bin` package, and managed/upstream T3 unit state.
An inactive managed T3 service is expected in desktop mode:

```fish
basaltw local cachyos-doctor
basaltw local cachyos-doctor --json
basaltw local cachyos-doctor --attention --json
```

`--attention` keeps every deferred/failed observation and counts omitted
successes; omit it when successful checks or versions matter. See
[read-only diagnostics](cachyos-maintenance.md#read-only-diagnostics).

It does not install, launch, capture, open listeners, or write a report. A
successful report proves only the observations it lists; it does not prove GPU
rendering, desktop input, provider authentication, or an end-to-end thread.
The command runs without the Debian maintenance confirmation, including with
`--json` in a noninteractive session. Browser observations recognize Chromium,
Firefox, Brave, LibreWolf, and Cachy Browser native packages. An absent optional browser
package does not mean there is no usable browser; custom installations are not
inventoried, and the doctor does not launch a browser to test it.

The doctor loads the validated saved selection: a missing selected T3 package,
inactive selected web service, or broken selected CLI is reported as failed.
An invalid or unreadable saved selection produces a failed configuration
observation; missing setup history remains normal absence. Inspect the local
record and rerun an explicit setup selection to restore selection-aware checks.
Desktop selections also check the native T3 executable and managed login startup;
an active managed web service in desktop mode or active upstream T3 service
alongside a selected Basaltwater T3 mode is reported as a conflict.
Selected media/graphics, desktop, streaming, sysadmin, and publishing commands
also receive PATH presence checks without launching them. Remmina's selected
protocol/secret dependencies are checked as packages. Command presence does not
verify runtime behavior or executable ownership; setup performs its own readiness checks.
Missing unselected browsers remain informational. It also reports failed units,
root and home filesystem capacity, booted kernel module presence,
firmware/encryption observations,
non-loopback listeners, and potentially broad saved UFW input rules, including
port ranges and Sunshine UDP. It distinguishes broad allows preceded by covering
unconditional denies from uncovered or unparsed rules needing review. It never
elevates privileges; saved rules do not prove effective packet filtering.
When SSH policy is saved, it checks managed service activity and boot enablement
and reports an active password bootstrap window separately. These observations
do not verify authentication, key usability, or client connectivity.
When Sunshine is selected, it checks the user service and, if `vainfo` is installed,
VA-API encoding profiles without starting capture. A crashed Sunshine service is
reported as failed; an inactive service may reflect an intentional quit.
`startup.sunshine` separately checks persistent login enablement, so an enabled
service quit for the session is distinguished from disabled or temporary startup.
`health.mirrors` separately identifies a failed CachyOS mirror refresh service
and points to its local journal and DNS/connectivity checks. It does not contact
mirrors, retry the service, or verify mirror freshness. See
[mirror recovery](cachyos-diagnostics.md#mirror-refresh-failures).
Update observations use existing pacman metadata, which may be stale and does not
cover AUR releases. Setup prints the same host observations before installing
packages.

Successful setup and refresh also print best-effort system restart advice from
the booted kernel's modules and core-package installation dates. Unavailable
evidence is labelled unknown or incomplete, and CachyOS is never rebooted
automatically. Follow distro and firmware notices too; see
[restart notices](cachyos-refresh.md#system-restart-notices).

To check your first setup, record its CachyOS, Plasma, kernel, GPU/driver,
and tool versions; authenticate an agent; complete a small edit/test task and
disposable worktree; and, when selected, test a T3 thread, terminal command,
logout/login, and the intended loopback or LAN listener. Test Godot,
Vulkan/OpenGL, audio, and media on the actual GPU. Repeat
the relevant checks after a normal CachyOS update and record AMD and NVIDIA
results separately.

For contributors, `plugins/cachyos.py` owns composition, `lib/cachyos.py` owns
the local support boundary, and `common/cachyos_steps.py` owns target-side
operations; `common/cachyos_t3.py` owns staged T3 activation and recovery.
`common/cachyos_cleanup.py` owns package-cache pruning. Arch package inputs use
`lib.validation.validate_arch_package_name`, which accepts the distro's
underscore and `@` characters without loosening APT validation. The
CLI routes local targets directly to the CachyOS runner; remote controllers use
`lib/cachyos_remote.py` to stage source and execute that same target boundary
as the existing desktop user. Keep additions explicitly allowed and independently tested;
mock pacman, sudo, downloads, service operations, and hardware probes.

## Later maintenance

For an established setup, use the [maintenance reference](cachyos-maintenance.md)
for updates, saved selections, mode changes, and recovery.

### Upgrade and repeat your last setup

See [saved setup and refresh](cachyos-refresh.md#upgrade-and-repeat-your-last-setup).

### AUR download failures

See [helper diagnostics](cachyos-diagnostics.md#aur-download-failures).

### Switch modes on a later setup

See [mode changes and data boundaries](cachyos-t3-maintenance.md#switch-modes-on-a-later-setup).

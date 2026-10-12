# Project manifests and Node runtimes

Run these commands from the project checkout. They work without provisioning
or changing a saved host setup.

```bash
basaltw manifest init --dry-run
basaltw manifest init
basaltw manifest validate --json
basaltw node status --json
basaltw node doctor --json
basaltw node exec -- npm test
```

## Related checkouts and host roles

CachyOS workstations and Debian hosts both support Antistatic and Godot
development/testing. CachyOS provides the interactive KDE, hardware playtest,
Animator, and asset-authoring environment; Debian also provides reproducible
validation, final builds, release qualification, CI, publishing, and servers.
Use the profile and diagnostics for the actual OS. A successful host doctor
establishes prerequisites; each repository still owns its validation gates.
Use CachyOS development binaries for local testing; final release artifacts and
publishing credentials belong on the Debian build/publishing host. See the
[development comparison](CACHYOS_GAME_DEVELOPMENT.md#comparable-development-checks)
for shared project gates and distinct hardware/software evidence.

Both profiles clone primary repositories under `~/repos` by default. Set
`--agent-workspace /absolute/path` to choose another root. Keep related
checkouts as siblings using their repository basenames, such as `antistatic`,
`antistatic-animator`, `antistatic-assets`, and `basaltwater`. Project examples
using `../NAME` assume this layout; discover the actual checkout before use.
Managed worktrees can live elsewhere: use `git worktree list` to locate the
primary checkout and pass explicit source/game-root options where supported.
Each worktree needs its own ignored dependencies and build products.

Repeated `--repo HTTPS_URL` options clone missing repositories. Existing
checkouts must match the requested origin; a trailing `.git` or slash does not
change that identity. Different owners, hosts, or transports remain distinct.
Setup preserves existing branches and worktree contents without pulling,
resetting, installing project dependencies, or running repository scripts.
Adding a checkout to T3 Code is a separate operation in the selected desktop
or web environment; use its Add project UI or supported project CLI with the
absolute checkout path. Preserve application state and register each checkout
once. The development checkout of Basaltwater and its managed installed source
are separate; `basaltw upgrade` updates the installed CLI on its chosen channel.

See [CachyOS game development](CACHYOS_GAME_DEVELOPMENT.md) for the Antistatic
inventory and [Debian native tooling](#debian-native-game-development) for its
host bundle. Basaltwater and adjacent source repositories remain optional for
ordinary project builds and immutable tagged package installs.

## Initialize a manifest

`manifest init [REPOSITORY]` inspects project files without executing scripts,
installing dependencies, or contacting a deployment target. It writes a
version 1 `basaltwater.json` only when no manifest exists. Existing manifests
are validated and preserved; malformed files, symlinks, and retired `infra.json`
files fail instead of being replaced. `--dry-run` prints the proposal without
writing. `--json` includes the inferred kind, proposal, review guidance, and
documentation link for coding agents.

| Structure | Default proposal |
| --- | --- |
| Vite, Astro, Create React App, Angular, or SvelteKit package | Static output, usually `dist/`; CRA uses `build/` |
| Express, Fastify, Koa, Next, Nuxt, or Nest package with `start` | Node service using its package manager's start command |
| Other Node package, including Electron applications | CI workflows without deployment components |
| Go module with one root or `cmd/*` main package | Built executable and loopback service |
| Go or Node server plus `frontend/`, `client/`, or `web/` site | API service plus static frontend |
| `index.html` at root, `public/`, `static/`, or `html/` | Ready static site |

Override inference with `--kind node-site`, `node-package`, `node-service`,
`go-service`, `full-stack`, `static`, or `publishing`. Conflicting package-manager declarations
or multiple Go main packages require explicit review. Custom layouts need a
hand-written manifest.

Inference is a starting point. Review the actual build output, framework
adapter/SSR settings, commands, routes, and base URL. For example, SvelteKit
needs a static adapter to be served as static files; use a service manifest for
SSR. Configure service arguments, executable/PATH, loopback binding, health
checks, secrets, and persistent state using the [deployment guide](DEPLOYMENTS.md).
The generated Go environment is a suggestion: verify that the server honors
those variables. API and frontend hostnames/base URLs also need review. Commit
the reviewed manifest so developers and CI use the same decisions.

## Repository-owned CI workflows

The optional `ci` object defines `install`, `build`, and `test` as ordered
arrays of literal commands. Each command has `argv`, optional repository-relative
`directory`, and optional `env`. Shell operators are ordinary arguments;
invoke a reviewed shell script explicitly when shell behavior is needed.

```json
{
  "version": 1,
  "components": [],
  "ci": {
    "install": [{ "argv": ["npm", "ci"] }],
    "build": [{ "argv": ["npm", "run", "build"] }],
    "test": [{ "argv": ["npm", "run", "check"] }]
  }
}
```

An empty `components` array requires a CI workflow or publishing metadata and cannot be
deployed as a website. The initializer honors npm/pnpm/yarn declarations and
lockfiles, uses `build` or `compile`, and prefers `check` over `test`. Review
whether a project's check already builds or whether its default test is
interactive. Add the project's full validation gate when required.

Generated deployment components also receive their own install/build hooks
for direct deployment. CI uses the explicit `ci` stages, avoiding a second run
of those hooks. Older manifests without `ci` use component hooks during the CI
build stage. See [webhook CI/CD](CICD.md) for script overrides, generation when
missing, and remote publishing limits.

## Publishing language declarations

Version 1 accepts `publishing.languages` for public communications. Missing
settings default to English only. For English and Spanish use
`{"publishing":{"languages":{"source":"en","supported":["en","es"]}}}`
in a complete manifest. Additional validated language tags are accepted;
English/Spanish workflows have structural tests, with live provider rendering
still requiring qualification. These settings do not assert in-game localization.

`basaltw manifest init --kind publishing` creates a metadata-only game manifest
with `components: []`. Edit and commit language settings in the repository;
the panel reads them without changing the manifest. Credentials, schedules and
human approvals stay in private VM state. Older Basaltwater versions reject
the extension and need upgrading. See [VM game publishing](GAME_PUBLISHING.md)
for uploads, translations and review.

## Select a project Node version

Selection uses `--version` when given, otherwise the nearest `.node-version`
or `.nvmrc` (the former wins in the same directory), otherwise `package.json`
Node engines, otherwise the managed NVM default or system Node. Lookup stops
at the nearest Git checkout boundary, so a frontend can have a different pin
from its server. Pins and engine requirements must both match. Within a major
or range, the highest matching installed stable release is selected.

Stable major/minor/exact pins, npm comparators, caret/tilde ranges, wildcards,
hyphen ranges, and `||` alternatives are supported. NVM `node`, `stable`,
`default`, and available `lts/*` aliases are accepted. Prerelease versions and
unsupported syntax fail with guidance; no runtime is downloaded implicitly.
Commit a stable pin for reproducible builds. A rolling `node` pin deliberately
selects the newest installed release and can differ from reviewed packaging
metadata. A development major and a reviewed packaged runtime are separate
contracts: projects such as Antistatic select development Node independently
and obtain their verified packaged runtime during packaging.

```bash
# Execute in the current directory using the frontend's runtime selection.
basaltw node exec --project frontend -- npm run check --prefix frontend
# Activate it in the current shell; env only prints safely quoted PATH exports.
eval "$(basaltw node env --project frontend)"
# Install the project pin explicitly through managed NVM.
basaltw node install
# Check the selected Node/npm and the project's npm engine requirement.
basaltw node doctor --project frontend --json
# Global tools belong to each Node installation; install exact reviewed versions.
basaltw node install --version 26.10.0 --package-manager npm@12.1.0
```

`node status` and `node exec` work with compatible system Node installations
as well as NVM. `node exec` retains the current working directory; `--project`
only selects metadata. Selection does not alter NVM's default alias. Installation
runs NVM in a child shell; an existing default alias remains NVM-managed.
An exact version that conflicts with project Node engines is rejected before
NVM preparation or installation. Major/minor pins and aliases are checked
against the engines once NVM resolves the installed version.
Package-manager installation is optional and explicit, checks Node engine
compatibility, and runs outside the checkout's package-manager policy.
This explicit installation enables only the selected tools' own setup scripts
under npm 12 and verifies their commands report the requested versions.
Install pnpm/yarn for each selected Node version when needed; avoid exposing
another Node version's global tool directory through PATH.

Explicit `node install` also protects the selected runtime from automatic
maintenance cleanup, including an already-installed version. Scheduled updates
retain preexisting runtimes without ownership metadata and versions marked for
projects; only runtimes created by automatic maintenance can be removed during
cleanup or rollback. Selection and diagnostics are read-only. Use an explicit
install when a project adopts a runtime created by maintenance. See
[maintenance policy](MAINTENANCE.md) for LTS and optional latest-track updates.

On CachyOS, `node install` prepares missing user-local NVM through the same
vendor installer and checks as `--node-versions`, then installs the requested
runtime. Agents can run it when a project pin is missing without requiring a
full workstation setup or an earlier flag selection. Preparation runs as the
local desktop account, keeps shell profiles and the saved workstation selection
intact, and refuses unsafe, incomplete, or missing custom NVM installations.
An existing custom NVM remains usable. Other hosts still require NVM through
their normal setup. `--node-versions` remains available to prepare NVM ahead of
time without downloading a runtime.

`node doctor` checks the selected Node executable and its bundled npm against
`engines.npm`, when declared. It runs version probes in a private temporary
directory with repository scripts, user/global npm configuration, inherited
runtime options, and Corepack downloads excluded. Its JSON includes selection,
observed versions, issues, and maintenance ownership. No downloads or project
commands run; an unusable tool or incompatible npm returns a nonzero status.

Use `basaltw agent manifest --project-runtime --capability development --json`
to include that diagnosis alongside PATH tool inventory. The optional
`project_runtime` record keeps selection/executable provenance, observed Node/npm,
engine compatibility issues, pin value/source and `pin_status` separately from
`tools`, which still describes the invoking shell. Without the flag the field
is null and no Node/npm version probes run. An absent pin is explicit even when
engines or the default select a runtime. An unavailable runtime keeps declarations
and fixed issues with a null selection; manifest remains an inventory command,
while `node doctor` is the executable readiness gate.

Version probes use the shared process supervisor with a ten-second deadline and
4 KiB stdout limit each, outside repository and personal npm configuration.
NVM discovery examines at most 256 directory entries; an oversized inventory
is reported for inspection. Neither command installs tools, executes package
scripts, changes the shell PATH or modifies NVM's default alias.

`basaltw agent doctor --capability development` inventories the managed tools
independently of the current checkout. Node/package-manager version probes run
in a private temporary directory using the selected installation's interpreter;
Corepack network downloads are disabled. A project's npm-only policy therefore
does not make an installed pnpm appear missing. A broken selected installation
still needs repair through saved setup or an explicit per-version install.

These host commands are optional. Project builds and publishing should also
work through ordinary NVM or system toolchains without Basaltwater. NVM's
[project version-file guidance](https://github.com/nvm-sh/nvm#nvmrc) and npm's
[stable range reference](https://docs.npmjs.com/cli/v6/using-npm/semver/)
describe the underlying conventions.

## Debian native game development

Add `--game-dev` to a Debian coding VM or workstation setup for compilers,
CMake/Ninja, native dependency headers, shader checks, debugging/cache tools,
Xvfb captures, and GTK/NSS/audio/graphics libraries used by Animator/Electron.
For an existing saved setup, review and apply the patch from the controller:

```bash
basaltw patch HOST USER --game-dev --dry-run
basaltw patch HOST USER --game-dev
```

The bundle provisions host prerequisites through APT. Antistatic retains its
SDL/SDL_image source pins, native bootstrap, compiler requirements, and release
SDK; run the project's bootstrap and doctor after setup. Basaltwater never
builds checkout code during provisioning. CachyOS has its own
[`--game-dev` bundle](CACHYOS_GAME_DEVELOPMENT.md) for development work.

GTK is a runtime prerequisite for Animator/Electron. Debian's `libgtk-3-bin`
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

## Build for an older release baseline

A newer VM's libc and native libraries can exceed a project's supported
release ABI even when its source builds and tests pass. Use the project's
reviewed SDK/container build and retain its compatibility gates. Runtime
selection alone cannot make newer host libraries compatible with older targets.
Antistatic's `npm run package:linux` builds in its pinned Steam Runtime SDK;
its normal source builds remain independent of Basaltwater.

For managed Debian coding VMs that need container builds, add `--container-tools`
to the saved setup. It expands to `podman`, `uidmap`, `slirp4netns`, and
`fuse-overlayfs` through the existing `--apt-install` package path. Verify
subordinate UID/GID mappings and run `podman info`
as the login user. Rootless Podman does not require a Docker daemon or adding
the coding account to a privileged Docker group. The images and build cache
consume several GB; include their storage in VM capacity planning.

### Container storage and SDK headroom

Read-only container diagnostics are opt-in and work with existing tools on
Debian and CachyOS; they do not install or restart an engine:

```bash
basaltw agent containers inspect --engine podman --json
basaltw agent doctor --capability host --container-storage --json
basaltw agent support-bundle --container-storage
# Example estimate: 4 GiB uncompressed image, one additional layer copy,
# plus the default 1 GiB reserve. Substitute the workload's measured size.
basaltw agent containers preflight --engine podman --image-bytes 4294967296 \
  --additional-copies 1 --json
```

Use the VM host doctor on Debian; on CachyOS use its local doctor plus
`agent containers inspect` and `agent resources`. Each engine probe has a
10-second deadline and 256 KiB output cap. Results distinguish missing,
failed, timed-out and partial observations. Podman uses the local account's
store; Docker uses an existing local rootless socket when present, otherwise
`/var/run/docker.sock`, without saved contexts or credential helpers. Remote
endpoint/context selections are refused. No sudo, registry login, container
start or deletion is performed. Engine-owned local bookkeeping may occur.

The local result includes the graph-root path, its filesystem's available
space and whether it shares the root/home filesystem, aggregate image and
container counts, running containers, size totals, and an engine-reported
reclaimable estimate. Separate build-cache totals remain `null` when the engine
does not expose them. Shareable support/readiness records omit graph-root and
mount paths, registry URLs, image/container names, labels, and repository paths.
Detailed engine output and errors are discarded.

Preflight requires an explicit estimated **uncompressed** new image size.
Required space is `image_bytes * (1 + additional_copies) + reserve_bytes`.
Adjust `--additional-copies` (0–8) and `--reserve-bytes` for the SDK's observed
behavior. `sufficient_estimate` is not a guarantee: rootless ID-mapped copies,
temporary build products, compression, shared layers and concurrent work can
change demand. Missing graph-root capacity returns `unknown`; insufficient or
unknown preflight exits nonzero. Free root space cannot supply a graph root on
a separate home mount. Reclaimable estimates are never counted as free space;
[Podman documents](https://docs.podman.io/en/latest/markdown/podman-system-df.1.html)
that shared layers can overstate recoverable space.

For cleanup, first prepare a local owner-scoped proposal: inspect
`podman system df -v`, `podman ps --all`, and the exact images produced by the
completed task. Record IDs, ownership, all container/child-image references,
and estimated space; preserve SDK caches needed by projects, running and
stopped workloads, and other owners' artifacts. These listings contain private
names and stay local. Podman image removal has no dry-run: the read-only
inventory and proposed exact-ID command are the dry-run evidence. After
cleanup is explicitly authorized, `podman image rm --no-prune EXACT_TASK_IMAGE_ID`
preserves dangling parents and refuses referenced images without `--force`.
See [Podman's removal contract](https://docs.podman.io/en/latest/markdown/podman-rmi.1.html).
Never replace this review with automatic system/image/volume pruning.

For repeated SDK work, provision a larger home or dedicated local data disk
before adding CPUs to address a space failure. A new VM can declare
`--storage home-data POOL SIZE --storage-mount home-data /home ext4 empty`.
An existing managed VM can add a blank named disk at a new empty mount using
the [named-disk workflow](COMMAND_LINE.md#proxmox-provisioning-flags), subject
to its existing storage checks. Mounting new storage does not migrate containers.

For a deliberately configured rootless store, review the existing
`~/.config/containers/storage.conf` and select an absolute `graphroot` on the
larger local filesystem, preserving its storage driver/options, ownership,
UID mappings, and any SELinux labels. An administrator can instead set
`rootless_storage_path` for new stores; see the
[storage configuration](https://github.com/containers/storage/blob/main/docs/containers-storage.conf.5.md).
An existing store migration requires a separate stopped-workload, backup,
copy/restore and verification plan; setup reruns never relocate it. Account
for separately configured image stores and copy-temporary directories too.
The generic preflight covers graph-root space only, not every build mount.

`--debug-tools` similarly adds GDB, strace, Valgrind, ccache, and Ninja for
native diagnosis and builds. These opt-in Debian bundles are saved and sent to
the target as explicit APT packages. They do not alter sudo authority or
container/kernel policy. Remove unwanted packages from the saved selection;
removing a bundle selection does not uninstall packages.

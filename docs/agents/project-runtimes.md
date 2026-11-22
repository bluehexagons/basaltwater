# Node and Python runtimes

[Documentation index](project-tooling.md)

## Select a project Node version

Selection uses `--version` when given, otherwise the nearest `.node-version`
or `.nvmrc` (the former wins in the same directory), otherwise `package.json`
Node engines, otherwise the managed NVM default or system Node. Lookup stops
at the nearest Git checkout boundary, so a frontend can have a different pin
from its server. Pins and engine requirements must both match. Within a major
or range, the highest policy-allowed matching installed stable release is selected.

Stable major/minor/exact pins, npm comparators, caret/tilde ranges, wildcards,
hyphen ranges, and `||` alternatives are supported. NVM `node`, `stable`,
`default`, and available `lts/*` aliases are accepted. Prerelease versions and
unsupported syntax fail with guidance; no runtime is downloaded implicitly.
Commit a stable pin for reproducible builds. A rolling `node` pin deliberately
selects the newest policy-allowed installed release and can differ from reviewed packaging
metadata. A development major and a reviewed packaged runtime are separate
contracts: a project can select development Node independently and obtain its
verified packaged runtime during packaging.

```bash
# Execute in the current directory using the frontend's runtime selection.
basaltw node exec --project frontend -- npm run check --prefix frontend
# Activate it in the current shell; env only prints safely quoted PATH exports.
eval "$(basaltw node env --project frontend)"
# Install the project pin explicitly through managed NVM.
basaltw node install
# Resolve the exact supported target without installing it.
basaltw node resolve
# Check the selected Node/npm and the project's npm engine requirement.
basaltw node doctor --project frontend --json
# Use the latest supported Node 26 release with a reviewed npm version.
basaltw node install --version 26 --package-manager npm@12.2.1
```

Project selection, managed Node installation, the Debian setup baseline, and T3 web setup require
`^24.22.1 || ^26.12.1`. The baseline continues to install LTS by default;
automatic Current-track updates still require their explicit policy opt-in.
Debian setup supplies `libstdc++6` and `libatomic1` for the official binaries;
Node 26 requires `libatomic.so.1` even for a version probe. Existing installations
missing these libraries need setup repair before the affected runtime can run.
Unsupported requests fail before preparing NVM. Aliases and partial version pins resolve to
an exact supported stable release before installation and package-manager work.
Existing project runtimes remain on disk for inspection and ownership protection,
but unsupported versions cannot be selected by managed commands. Generic semver
parsing still accepts older versions when checking package-manager requirements.

`node resolve` prints the exact install target without changing NVM, runtime
ownership or package managers. With no explicit version or project pin, it
intersects Node engines with the newest allowed stable release, or selects
the newest allowed LTS when no engines are declared. Exact stable pins are
checked offline. Major/minor pins and node/stable/LTS aliases use the bounded
official Node release index and intersect policy plus project engines before
any NVM preparation or installation; discovery failure does not fall back to
a cached alias. Local aliases serve installed-runtime selection only.
Explicit installation protects the resolved exact cached target before NVM
runs and verifies both the activated exact version and its executable before
optional package-manager work. Setup and scheduled maintenance pin the default
to their exact validated LTS target.

Setup, explicit installs, and scheduled Node updates serialize NVM extraction
with runtime maintenance and request binaries only (`nvm install -b`). A failed
download or extraction stops instead of falling back to a source build. Before
retrying a target that lacks executable Node or npm, Basaltwater moves its whole
directory into a private `.basaltwater-incomplete-*` directory under that NVM
root and prints the retained path. This preserves global tools and other files
for inspection instead of merging a new archive into a partial installation.
Verified download caches remain available for retry. Ctrl+C during setup stops
the installer and its children; rerun the same setup to recover any partial
target. Completed runtimes and their global tools are reused.

Deployment preparation and builds use this shared selector under the existing
build identity, including nested frontend pins and `.node-version` precedence.
Host readiness rejects an observed unsupported active/default runtime even when
another supported version is installed.

Privileged browser provisioning uses checksum-pinned, root-owned Node 24 LTS
under `/opt/basaltwater-node`; it never borrows a user-writable NVM runtime.
Stable browser testing uses Playwright 1.64.1 independently of MCP 0.0.83's exact
alpha Playwright dependency. That prerelease remains an explicit exception only
in `managed-mcp`. See [browser contexts](browser-automation.md#installed-components-and-agent-configuration).
Debian CI receives an explicitly selected, verified Node from its read-only
tool cache; it does not install distro Node 20.

Go setup uses the newest stable release from the current official download
feed (Go has no LTS), including on ordinary reruns. It reuses an installed Go
only when its version equals that release. `BASALTWATER_GO_VERSION` and detected
go.mod directives are minimum versions, so an older module minimum selects the
newest stable Go rather than reinstalling an unsupported minor line. Setup
fails if the latest stable release cannot satisfy the minimum or lacks a valid
archive for the native architecture. Feed retrieval must succeed to establish
whether an installed version can be reused.

`node status` and `node exec` work with compatible system Node installations
as well as NVM. `node exec` retains the current working directory; `--project`
only selects metadata. Selection does not alter NVM's default alias. Installation
runs NVM in a child shell; an existing default alias remains NVM-managed.
An exact version that conflicts with project Node engines is rejected before
NVM preparation or installation. Major/minor pins and aliases are resolved
against the engines before installation and checked again against the exact
version active in the NVM install shell.
An older compatible installed runtime cannot hide an incompatible resolution.
The resolved executable must also report that version before optional global
package managers are installed; a failure leaves the runtime available for inspection.
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
[maintenance policy](../operations/maintenance.md) for LTS and optional latest-track updates.

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
from `/` using the selected installation's interpreter, without creating temporary
files. Corepack project selection, auto-pinning, latest-version lookup and network
downloads are disabled. A project's npm-only policy therefore
does not make an installed pnpm appear missing. A broken selected installation
still needs repair through saved setup or an explicit per-version install.

These host commands are optional. Project builds and publishing should also
work through ordinary NVM or system toolchains without Basaltwater. NVM's
[project version-file guidance](https://github.com/nvm-sh/nvm#nvmrc) and npm's
[stable range reference](https://docs.npmjs.com/cli/v6/using-npm/semver/)
describe the underlying conventions.

## Python tooling installation checks

During setup reruns, an existing `/usr/local/bin/python` is accepted when it is
a symlink resolving to the selected `python3` executable, even if the setup
process cannot find `python` through its restricted `PATH`. That matching alias
is left unchanged; dangling links, other symlink targets, regular files, and
other existing entries still fail for operator repair.

Managed uv installation accepts an existing `~/.local/bin/uv` only when it is
a regular executable and `uv --version` exits successfully with recognizable
`uv X.Y.Z` output (optionally including build metadata) in the target user's
environment. Local and target-user version probes have a ten-second deadline;
empty or unrelated output, failed execution, and timeouts mean uv is unusable.
This check also runs when updates are disabled, after installation, and after
self-update. Invalid files or symlinks are replaced through the validated vendor
installer; symlink targets are preserved. A directory or other special entry at
that path is preserved and fails explicitly for operator repair. A failed update
can retain a working uv, but an unusable result fails setup.

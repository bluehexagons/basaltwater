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
`go-service`, `full-stack`, or `static`. Conflicting package-manager declarations
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

An empty `components` array requires at least one CI workflow and cannot be
deployed as a website. The initializer honors npm/pnpm/yarn declarations and
lockfiles, uses `build` or `compile`, and prefers `check` over `test`. Review
whether a project's check already builds or whether its default test is
interactive. Add the project's full validation gate when required.

Generated deployment components also receive their own install/build hooks
for direct deployment. CI uses the explicit `ci` stages, avoiding a second run
of those hooks. Older manifests without `ci` use component hooks during the CI
build stage. See [webhook CI/CD](CICD.md) for script overrides, generation when
missing, and remote publishing limits.

## Planned publishing language declarations

The [release and communications plan](plans/GAME_RELEASE_COMMUNICATIONS.md#project-language-configuration)
proposes `publishing.languages` in `basaltwater.json` for public communications.
Projects would default to English only, with English/Spanish qualified first
and additional language tags allowed. This section is not accepted by today's
strict manifest parser. The plan covers schema/consumer changes, publishing-only
projects, panel editing, and keeping credentials and human approvals outside
the repository. These settings would not assert in-game localization support.

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

`node doctor` checks the selected Node executable and its bundled npm against
`engines.npm`, when declared. It runs version probes in a private temporary
directory with repository scripts, user/global npm configuration, inherited
runtime options, and Corepack downloads excluded. Its JSON includes selection,
observed versions, issues, and maintenance ownership. No downloads or project
commands run; an unusable tool or incompatible npm returns a nonzero status.

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

The development doctor also inventories native commands and pkg-config module
versions without launching a window or testing GPU acceleration. A saved Debian
`--game-dev` selection makes missing host commands or dependency modules a
readiness failure. Missing project-built SDL modules are listed separately in
`project_bootstrap_required`. Other hosts still receive the inventory without
being required to install this bundle. Removing the selection retains installed
packages; an invalid selection record is reported for explicit repair.

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

`--debug-tools` similarly adds GDB, strace, Valgrind, ccache, and Ninja for
native diagnosis and builds. These opt-in Debian bundles are saved and sent to
the target as explicit APT packages. They do not alter sudo authority or
container/kernel policy. Remove unwanted packages from the saved selection;
removing a bundle selection does not uninstall packages.

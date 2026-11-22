# Project setup and manifests

[Documentation index](project-tooling.md)

## Related checkouts and host roles

CachyOS and Debian profiles support native game, Godot and asset-authoring
workflows. Choose the profile for the actual OS and the checks the project needs.
Debian also supports Basaltwater's managed build, publication and server roles;
CachyOS supplies native KDE development and hardware playtesting. A host doctor
establishes prerequisites; each repository owns its validation gates. See the
[development comparison](../workstations/cachyos-game-development.md#comparable-development-checks)
for distinct hardware/software evidence.

Both profiles clone primary repositories under `~/repos` by default. Set
`--agent-workspace /absolute/path` to choose another root. Keep related
checkouts as siblings only when the project's scripts expect that layout.
Discover the actual paths before using `../NAME`. Managed worktrees can live
elsewhere: use `git worktree list` and explicit source-root options where
supported. Each worktree needs its own ignored dependencies and build products.

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

See [CachyOS game tooling](../workstations/cachyos-game-development.md) and
[Debian native tooling](project-native-builds.md#debian-native-game-development) for host bundles.
Optional project layouts and release commands belong in the
[integration guides](../integrations/README.md).

## T3 Code project actions and worktrees

Use the repository's `t3.json` to share actions through T3 Code's project
Actions settings. In a Linux checkout, Basaltwater's actions invoke `make check-fast` and
`make check-ci` using the checkout's `.venv` when present. Install development
dependencies using the [contributor guide](contributing/README.md) first.

Projects that benefit from sparse worktrees can set `worktreeSparsePatternsPath`
to a committed non-cone sparse-checkout pattern file. Check the revision you
intend to start from, and repeat `--require-file` for tracked files needed by
worktree setup, project instructions, and the affected area.

```bash
basaltw manifest check-t3-worktree --ref HEAD \
  --require-file AGENTS.md --require-file scripts/worktree-setup.sh --json
```

The command reads committed Git objects and reports the commit, `full` or
`sparse` status, and the pattern path if configured. It checks the sparse
field independently of the rest of the T3 project schema. For sparse
inclusion checks, use a Git version with `sparse-checkout check-rules`.
The command writes no Git or worktree configuration and runs no project
scripts. After preflight, verify setup and checks in a fresh T3 worktree.

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
checks, secrets, and persistent state using the [deployment guide](../services/deployments.md).
The generated Go environment is a suggestion: verify that the server honors
those variables. API and frontend hostnames/base URLs also need review. Commit
the reviewed manifest so developers and CI use the same decisions.

## Project browser tests

Projects made with Basaltwater own their UI compatibility. Declare the supported
engines, locked Playwright version, local browser command, and fast CI command
in the project's README and agent/contributor instructions. Use pinned Chromium
as a useful primary baseline and add Playwright Firefox explicitly when Firefox
support is intended. Native Firefox ESR and an unknown connected preview are
separate targets; a discovered browser or successful runtime doctor does not
prove application compatibility.

Prefer intensive cross-browser suites on developer machines and keep CI on fast
unit/service/render checks. Projects can document a different policy when their
cost or release requirements justify it. For example, a project with the
corresponding package scripts might document:

```bash
npm ci
npm run check             # Fast CI gate, no browser payload download
npm run test:browser      # Local Chromium + Firefox application checks
```

These are project-owned script names, not commands Basaltwater generates.
Install payloads explicitly with that project's locked Playwright CLI; preserve
its package manager and lockfile, and do not borrow an unrelated managed browser
runtime. Bound workers using the developer machine's available memory and CPU,
retain failure evidence privately, use synthetic credentials and disposable
services, and keep certificate verification enabled. Record each engine's
version and outcomes separately. [Basaltwater's own local suite](browser-testing.md)
illustrates this split without introducing a global browser-support manifest
field. Hosted upstream apps retain their own support policy.

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
lockfiles and uses `build` or `compile`. An explicit `check:ci` is the complete
test gate; otherwise the proposal includes both `check` and `test` when present,
since `check` can mean linting and type checks alone. Review the proposal before
use: if `check` already runs the suite, remove the duplicate test step or define
an aggregate `check:ci`. Use that explicit gate to exclude browser-heavy or
interactive checks from CI. Remove a redundant build stage when the selected
check produces all required output, and add any other required validation gates.
Existing manifests stay unchanged.

Generated deployment components also receive their own install/build hooks
for direct deployment. CI uses the explicit `ci` stages, avoiding a second run
of those hooks. Older manifests without `ci` use component hooks during the CI
build stage. Signed webhook builds require a committed manifest and run these
literal commands through the core policy, source store and worker. They produce
build/test artifacts only, without automatic target deployment, status checks
or release publishing. See [signed webhook builds](../services/webhook-builds.md)
for signed pinned pushes, allowlists and optional Cloudflare ingress.

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
the extension and need upgrading. See [VM game publishing](../workstations/game-publishing.md)
for uploads, translations and review.

## Installed CLI and source context

Use `basaltw agent manifest --installation-context --capability installation
--json` to compare the invoked CLI with the managed source. Default roots are
the existing account's `~/.local/share/basaltwater` on CachyOS and
`/opt/basaltwater` on other managed hosts. A custom installation can pass
`--managed-source /absolute/source`, which also requests this context.
Neither flag changes a launcher, checkout, setup record or service.

`installation_context` keeps invoked/managed provenance, source-activation
and copied-snapshot receipts, and CachyOS's last successful setup receipt
separate. `same_revision` compares full known commit IDs only: true means the
recorded revisions agree, false means they differ, and null means evidence
is unavailable. Dirty/unknown worktree state remains in the source records;
revision agreement does not verify identical files or a completed setup.
Code-only upgrades can legitimately differ from the last setup revision.
An intentional developer launcher is retained even when its checkout differs.

On CachyOS, the optional view also observes the managed T3 web unit's state and
invocation through the existing owned user bus. It never starts a bus/service,
reads logs or command arguments, or infers loaded code from installed files.
The service source remains null without a revision receipt; desktop T3 is a
separate application and is not qualified by this unit observation. Other hosts'
running-service and last-successful-setup context remain unobserved here.
Unknown/older receipt schemas, unsafe files and missing revisions stay unknown;
saved setup arguments and private paths are omitted from source context.

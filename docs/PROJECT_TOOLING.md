# Project manifests and Node runtimes

Run these commands from the project checkout. They work without provisioning
or changing a saved host setup.

```bash
basaltw manifest init --dry-run
basaltw manifest init
basaltw manifest validate --json
basaltw node status --json
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
metadata. Antistatic uses an exact pin matching its packaged runtime; other
projects may choose a supported major.

```bash
# Execute in the current directory using the frontend's runtime selection.
basaltw node exec --project frontend -- npm run check --prefix frontend
# Activate it in the current shell; env only prints safely quoted PATH exports.
eval "$(basaltw node env --project frontend)"
# Install the project pin explicitly through managed NVM.
basaltw node install
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

These host commands are optional. Project builds and publishing should also
work through ordinary NVM or system toolchains without Basaltwater. NVM's
[project version-file guidance](https://github.com/nvm-sh/nvm#nvmrc) and npm's
[stable range reference](https://docs.npmjs.com/cli/v6/using-npm/semver/)
describe the underlying conventions.

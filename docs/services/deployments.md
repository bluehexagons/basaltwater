# Deployments and manifests

`basaltw` deploys a repository with `--deploy DOMAIN GIT_URL`. A repository
can use automatic project-type detection, or place a `basaltwater.json` file at its
root to describe one or more static sites and services explicitly. The manifest
is validated before deployment; an invalid file stops deployment instead of
falling back to automatic detection.

See [build systems](build-systems.md) to choose between target builds, selected
builders, webhooks and Actions runners. [Complete example projects](../examples/builds/README.md)
include ready static, Python static and combined SPA/Go applications with
matching enrollment templates.

Use `basaltw manifest init --dry-run` to propose a manifest from Node, Go,
full-stack, or static project structure, then `basaltw manifest init` to create
it. Existing files are preserved. Review the generated commands, output, and
service settings before deployment; see [project tooling](../agents/project-tooling.md)
for inference limits, CI workflows, and project Node selection.

For ordinary target builds, `--dry-run` performs a shallow clone into disposable
local staging and validates the repository type and `basaltwater.json` without
updating the persistent Git cache before target setup begins. It does not execute
repository build commands; projects should run those commands in CI as a separate
build preflight.
Repositories with the same final name (for example, two different `site.git`
URLs) receive distinct upload paths based on their complete URLs. Deployment
source trees must not contain symlinks or special files; the controller checks
this before reading the manifest or uploading the source.
Git URLs must not contain embedded HTTP credentials or query strings. An optional
`#branch=NAME` or `#tag=NAME` selects an explicit branch or tag; all other fragments
are rejected. Quote URLs with selectors:

```bash
basaltw setup server_web web.example.com deploy \
  --deploy app.example.com 'https://github.com/example/app.git#branch=main' \
  --deploy stable.example.com 'https://github.com/example/app.git#branch=release/stable'
```

Each source selector receives its own cache and upload path. Setup fetches the
selected ref on every run and stops if it is missing, including during dry runs.
Without a selector, every cache refresh checks the repository's current default
branch, including changes to that default since the cache was created. If Git
cannot determine it, setup stops instead of using a stale branch. Empty
repositories and clones without a checked-out HEAD commit are also refused.

For ordinary target builds, cache refresh and staging share a repository lock.
Automatic Git maintenance finishes in the foreground, and staging uses Git's
transfer protocol to create an independent clone with the original remote URL.
This avoids copying object files that maintenance may move or remove. A staging
failure stops setup with a nonzero exit status before the setup payload is sent;
batch summaries record it as a failed command.

For ordinary target builds, use the controller's Git credential helper or SSH
key for private repositories. Selected-builder deployments use HTTPS and the
controller's configured global Git credential helper, as described below.
Displayed commands also replace sensitive URLs from older saved configurations
with a placeholder that must be filled in before replay.

## Choose a topic

<a id="build-on-a-saved-builder"></a>
- [Deploy through a saved builder](deployments-selected-builder.md) Preflight, build/delivery boundaries, retention, and recovery.

<a id="basic-deployment"></a>
<a id="manifest-shape"></a>
<a id="static-site"></a>
<a id="godot-web-application"></a>
<a id="static-site-plus-api-service"></a>
- [Deploy and write application manifests](deployments-manifests.md) Basic deployment, schema, and static, Godot, and API examples.

<a id="service-options"></a>
<a id="runtime-and-update-behavior"></a>
<a id="validation-and-troubleshooting"></a>
- [Configure services and diagnose deployment](deployments-runtime.md) Runtime options, update behavior, validation, and troubleshooting.

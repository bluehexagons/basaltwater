# Agent environment manifest

Run `basaltw agent manifest` in a project checkout for a concise discovery
summary, or `basaltw agent manifest /path/to/repo --json` for structured output.
It reports executables on the active session PATH, the current branch/commit
and dirty state, managed workspace conventions, artifact directories, required
tools, named validation recipes, and declared branch-to-deployment mappings.
It performs no installation, cloud
request, deployment, or authentication-file inspection. Tool availability does
not establish health; use the reported doctor command for that separate check.

Use repeatable `--capability` filters for smaller responses, for example
`basaltw agent manifest . --capability web --json` for Node/web work or
`--capability browser --capability skills` for browser guidance. Scopes include
`installation`, `workspace`, `skills`, `browser`, `development`, `publishing`,
and the `web` combination. Unrelated application and publishing prose is omitted.
Every scope retains `basaltwater`, using the same provenance contract as the
support bundle. `setup_snapshot` describes a recorded setup receipt, while
`running_source` describes an observed Git root executing the CLI. Each is
`null` when unavailable. The top-level version/commit/dirty fields prefer the
running source; `evidence_source` states `running-git`, `setup-receipt`, or
`unrecorded`. `installation_type` is respectively `source-checkout`,
`setup-snapshot`, or `unrecorded`. Refresh additionally records observed source
activation in the readable `.basaltwater-refresh.json`. `refresh_receipt` is
separate from `setup_snapshot`; it is written before setup replay and does not
assert that setup completed. If Git metadata is inaccessible, diagnostics use
that receipt with `evidence_source: refresh-receipt` and
`installation_type: managed-checkout`, leaving `running_source` null. Observed
Git remains preferred when available. A checkout revision does not assert that
setup ran. Wheels and non-Git trees without a receipt remain unrecorded, and
`workspace.commit` still describes the separately selected project. No branch,
changed filename or installation path appears in this provenance object.

An existing developer launcher can intentionally invoke a checkout rather than
the managed VM source. Inspect that invocation, then compare the installed
runtime explicitly when diagnosing missing commands or stale guidance:

```bash
basaltw agent manifest . --capability installation --json
/usr/bin/python3 /opt/basaltwater/basaltwater.py agent manifest . --capability installation --json
```

Compare their reported revisions and evidence sources. Setup preserves unmanaged
launchers; keep a development override explicit instead of assuming it follows
managed VM updates. Neither report proves that a long-running service reloaded
its Python modules; verify its service lifetime separately after a source update.

`skills` inventories known Basaltwater entrypoints in both `~/.agents/skills`
(Codex/OpenCode) and `~/.claude/skills` (Claude Code). It compares entrypoints and
supporting files with the running CLI's source, reporting `current`, `different`,
`incomplete`, `unsafe`, or `personal`. A differing bundle may be older or edited;
refresh it through the platform's setup, preserving personal changes. Missing
optional skills are not health failures. The inventory does not include arbitrary
personal, repository, plugin, or system skills and does not inspect provider
configuration or credentials. `session_access` remains `unverified`: check the
active agent's skill list and enabled settings separately. See
[skill access by launch context](agent-skills.md#access-from-different-sessions).
Desktop skill guidance links relevant local entrypoints from either provider
catalog, including Claude-only installations.

Installed desktop applications appear with supported workflows and a link to
the [desktop development guide](../workstations/desktop-development.md). Blender also includes
a background-render recipe and guidance on argument order and capture settings.
`desktop_applications` includes only applications found on the active PATH;
their `readiness` remains `unverified`. Discovery never launches a window,
renderer, device probe, or audio session. `desktop_skills` links available local
desktop instructions; it does not establish that a desktop session is attached.

`host_profile` distinguishes the native CachyOS workstation from the Debian
management profile; it does not infer that a Debian host is a VM.
`publishing` inventories Butler and SteamCMD on the active PATH and links the
[publishing guide](../workstations/game-publishing.md). Debian `readiness` remains `unverified`;
discovery never reads native session files, opens the publishing database, runs
provider tools or contacts a storefront. Guidance directs human authentication
to a configured Debian HTTPS panel or the owner's interactive terminal. On
CachyOS `publishing.readiness` is `outside-host-role` and `management_command`
is null: this workstation is for Antistatic/Godot development and testing, with
final builds and publication handed to Debian. Installed tools remain inventory;
discovery does not direct provider sign-in or uploads here. CachyOS development
records include `intended_use: development-and-testing` and
`final_build_host: debian`. Development-scoped tool inventory includes native
compiler/debugger, sanitizer-symbolizer, shader and capture prerequisites.
Authorized unattended uploads on the publishing host use
`basaltw publish`. Agents can prepare drafts and translations, while exact public
text approval and confirmation belong to a human. Steam default/public releases
stay manual on Steamworks. Reviewed post exports remain human editor handoffs.
Storefront uploads and VM HTTPS game previews are separate workflows.

`browser` prefers capabilities exposed by the active agent session. T3 Code
agents should check `preview_status`, then `preview_open` when needed. The
manifest cannot verify session tools from a standalone CLI. Its managed
Playwright entry reports only wrapper presence on PATH, without launching it.
`browser.contexts` separates managed Chromium/optional Firefox, project-pinned
Playwright engines, interactive Firefox/Firefox ESR, and collaborative preview.
It records existing runtime versions and leaves startup/rendering unverified;
use the returned engine-specific doctor command when that coverage is requested.
See [engine and context checks](browser-automation.md#engine-and-context-coverage).
`workspace.legacy_browser_artifacts` identifies the older artifact directory
convention; even an existing directory does not establish a browser capability.
The reported health command uses the workstation doctor on CachyOS.

`desktop_backend` distinguishes CachyOS `native-session` from Debian
`shared-xrdp`; `automation_command` is respectively `basaltw desktop --native`
or `basaltw desktop`. Each application's `launch_argv` begins with the active
executable on CachyOS, and with `basaltw desktop exec --` on Debian. Append
literal document arguments; manifest launch guidance uses the same routing.
CachyOS skills cover native application scripts/exports and explicitly started,
user-approved [KDE Wayland automation](../workstations/cachyos-desktop.md). Discovery does not
request consent or query desktop contents.

Projects can commit an optional `basaltwater-agent.json` at their Git root.
This discovery file is separate from the `basaltwater.json` build/deployment and
publishing manifest and does not change CI or deployment behavior. For example:

```json
{
  "version": 1,
  "required_tools": ["node", "yarn"],
  "recipes": {
    "test": {
      "description": "Run the project test suite",
      "argv": ["yarn", "test"],
      "requires": ["node", "yarn"]
    }
  },
  "artifact_directories": [".artifacts", "dist"],
  "deployments": {
    "dev": {
      "environment": "development",
      "provider": "aws",
      "region": "us-east-1",
      "url": "https://dev.example.com/"
    },
    "staging": {
      "environment": "preproduction",
      "provider": "aws",
      "region": "us-east-1",
      "url": "https://staging.example.com/"
    }
  }
}
```

Use the project's verified mappings rather than assuming what a branch name
means. Each exact branch key requires `environment`; `provider`, `region`, and
`url` are optional. URLs use HTTPS without credentials, query strings, or
fragments. Omit credentials and environment-variable values. Unknown fields,
invalid Git branch names, malformed JSON, unsupported versions, symlinked or
non-regular files, and files larger than 64 KiB fail the command.

Artifact directories are relative to the repository and cannot escape it.
The summary reports whether Git ignores each declared directory; it does not
edit `.gitignore`. Tools missing from PATH appear as `null` in JSON, even if
another shell or Node selector could activate them. The command does not run
package-manager shims to discover versions because those can download tools.

`required_tools` declares executable names, including project-specific tools
outside the built-in inventory. The result reports each as `available` or
`missing`; availability still means PATH presence, not tested readiness.
Missing requirements are discovery findings and do not change the command's
exit status. Tool names contain letters, digits, underscores, plus, dot, or
hyphen and start with a letter or digit; paths and command flags are rejected.

Each recipe requires a short `description` and an `argv` array of literal
arguments. Optional `directory` defaults to the repository root (`.`) and
must stay inside it, including through existing symlinks. Optional `requires`
lists the executable names needed for that recipe; the result's `missing_tools`
identifies absent prerequisites. Commands are displayed with shell quoting,
never executed by the manifest. Arguments do not expand environment variables,
substitutions, or globs. Choose whether to run a recipe according to the user's
task and review project commands before execution.

`basaltw agent visuals check RECIPE --repository PATH --json` explicitly runs
one declared recipe and retains its output, settings and environment evidence.
This separate command is useful for post-refresh rendering checks; manifest and
doctor remain prerequisite/discovery observations. See
[project visual checks](../workstations/visual-comparisons.md#run-a-project-check) for execution
scope, private artifacts and project-owned verification.

For a Blender project, declare `blender` and a recipe such as
`["blender", "--background", "scenes/validation.blend", "--render-output",
".artifacts/render-", "--render-format", "PNG", "--render-frame", "1"]`.
Record scene, camera, engine, device, frame, and resolution in project capture
settings; the recipe does not prove desktop or GPU readiness. Keep recipes
limited to non-secret commands. Each array/object is limited to 100 entries,
each string to 256 characters, and the entire declaration to 64 KiB.

`project_source` identifies the declaration used; `current_deployment` is the
mapping for the current branch, or `null` when undeclared or detached. Missing
`dev` and `staging` mappings are explicitly reported as unknown. The command
does not infer destinations from CI scripts, AWS credentials, or branch names.
Keep the declaration updated alongside changes to the project's deployment
configuration. Builds, tests, Git, and native rendering retain their existing
toolchains and commands.

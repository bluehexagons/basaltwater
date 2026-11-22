# Skill bundle maintenance

[Documentation index](agent-skills.md)

## Reconciliation and ownership

Setup copies repository-owned `SKILL.md` files and complete `references/`,
`scripts/`, `assets/`, and `agents/` trees into each selected personal catalog.
Scripts retain executable permission, other resources use mode 0644, and
individual files are limited to 16 MiB. Installing a bundle never runs its scripts.
A rerun refreshes files containing `managed-by: basaltwater` and leaves identical files
alone. It removes obsolete Basaltwater-managed desktop/browser skills when the
selected capability combination changes, while preserving unrelated skills and
user configuration. It refuses symlinked paths, directories owned by another
user, and a same-name skill without the managed marker.
The refresh runs after provider configuration is copied, so an older managed
catalog included in `--agent-config active` cannot replace the current guidance.

Supporting resources are tracked with SHA-256 digests in the private
`.basaltwater-files.json` inventory. Refreshes preflight each bundle before
replacing files and refuse untracked same-name resources or modified tracked
resources. Move or rename those personal files before retrying; setup will not
overwrite them. Obsolete unchanged resources are removed, while edited resources
and unrelated personal files remain. Retirement removes the managed entrypoint,
unchanged tracked resources, and empty directories only. Symlinked source or
destination entries, special files, and unsafe inventory paths are rejected.
Each file replacement is atomic; the entire catalog is not a single transaction,
so rerun setup after correcting a reported collision. New resource ownership is
recorded before writing files, and previous digests remain until refresh finishes.
An interrupted installation can therefore be retried without treating its new
resources as personal files. An explicit completion flag is cleared before any
bundle files change and set only after the refresh finishes. Interrupted bundles
fail readiness and report `incomplete` in the catalog even when their files
still match the old digests or already match the new source. Older inventories
without this flag remain readable; missing recorded resources fail readiness.
Readiness verifies recorded resources are present, unchanged, user-owned, and
not writable by other users; older entrypoint-only installations remain readable.

The Playwright doctor includes the shared selected browser workflow skill in
capability health, and the T3 doctor requires exactly one T3-capable browser
variant in every installed skill-compatible provider's catalog. Running both
checks on a combined VM therefore verifies the combined skill rather than
accepting independent Playwright-only and T3-only guidance.

An older VM receives the current base set when its saved setup is rerun from an
updated Basaltwater control plane. The same setup rerun also updates selected
Codex, Claude Code, and OpenCode executables through the verified user-scoped
updater; `basaltw agent update` remains available for an agent-only update.
Setup refreshes the selected skills from its source checkout. The agent-only
update refreshes neither Basaltwater nor skills; upgrade Basaltwater separately
before rerunning setup to install newer guidance.

Current skills invoke `basaltw`; their `basaltwater-*` IDs remain stable.
Normal agent setup installs `basaltw` before refreshing skills. When
upgrading an older VM with explicit `--steps`, include
`install_agent_cli_launcher` before `install_agent_workflow_skills` or another
capability step that refreshes the catalog, for example
`--steps 'install_agent_cli_launcher install_agent_workflow_skills'`.
Capability-only runs must not publish new guidance while leaving an old-only
launcher installation. On CachyOS, rerun the current installer to refresh the
user bootstrap before updating skills. Remaining infra-tools installations
require the [intermediate migration version](../migration-guide.md) first.

## Maintaining the catalog

For project discovery, `basaltw agent manifest` reports tools, workspace
conventions and declared deployment mappings. See [Agent environment manifest](agent-environment.md).

Skill sources live in `common/agent_skills`. Keep each entrypoint short and
self-contained, with a precise discovery description and non-obvious platform
behavior. The project-development, project-adoption, and frontend-design skills
are cross-project guidance: keep them repository-agnostic and defer platform
details and delegation mechanics to the relevant workspace and subagent skills.
Add shared skills to `BASE_AGENT_SKILL_NAMES` in `common/agent_steps.py` and the parallel
`CACHYOS_SKILLS` catalog in `common/cachyos_steps.py`. Browser variants belong in
`BROWSER_AGENT_SKILL_NAMES` and the capability selector; other capability skill
tuples should extend the base constant so standalone capability setup remains
complete. Platform-neutral skills such as `basaltwater-subagents` also belong
there; keep platform-specific skills in their own catalog. Add shared skills
intended for Ubuntu WSL to `WSL_SKILL_NAMES` in `common/wsl_steps.py`, wired by
`plugins/wsl.py`.

Use relative cross-bundle links only when the target bundle is installed
alongside the entrypoint in every supported catalog. Keep T3 release
qualification in the [operator records](t3-code.md#agent-workflow-integration).

Keep essential discovery and operational boundaries in the entrypoint and link
detailed procedures to shipped relative references. For example,
`basaltwater-t3code` keeps host-side update and migration instructions in
`references/updates.md`. Supporting trees are packaged in wheels as well as
deployed from source. Do not ship credentials, caches, generated bytecode, or
unnecessary assets, and do not assume an application checkout contains
Basaltwater documentation. The agent suite covers bundle refresh, collisions,
ownership, and retirement; the wheel artifact check requires the T3 project-workflow,
shared subagent, T3 operations, unattended task, and CachyOS native-control references.

During an audit, check command examples against their parsers and implementation,
check readiness claims against doctor results, and review all three browser
variants together. Select diagnostics for the task instead of treating examples
as a mandatory checklist. Keep deployment checks scoped to the repositories
being changed and make mutation effects explicit. Platform-specific certificate
enrollment lives in [Client CA trust](../services/client-ca-trust.md), while browser skills
retain the trust-verification boundary and fallback behavior.

Validate changes with the repository tests and the Codex skill validator when
it is available:

```bash
make docs-check
python3 -m unittest tests.test_agent_skills tests.test_t3_agent_skills
python3 -m unittest tests.test_agent_skill_contracts tests.test_web_panel_agents
python3 -m unittest tests.test_godot_web_host
```

The skill contract tests parse fenced bash, sh, and fish Basaltwater command
examples with the current CLI parsers without executing them and verify shipped
Markdown references. `make docs-check` also checks local file and heading links
across operator docs, plans, and skill resources. Template tests check task
validation and command permissions; they do not launch paid prompts or prove an
agent will follow every instruction.

Also run `git diff --check` and verify that every skill retains the
`managed-by: basaltwater` marker before committing.

### Compare skill behavior

For a substantial instruction change, use representative requests and known
starting states. Repeat runs with the same model, reasoning effort, tools,
fixture revisions, and budget, changing only the skill version. Include a
narrow request to see which guidance the agent loads. Inspect outputs against
the same rubric without knowing which skill produced them when possible.

For frontend guidance, use a new website, an existing app workflow with a
recoverable error, and a Godot UI reached in normal play. Review captures at
the intended display size and verify task outcomes through the actual controls.
Record component reuse, material findings resolved across passes, coverage
gaps, and run cost. Reuse project recipes and keep raw traces and captures in
private storage.

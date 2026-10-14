# Readiness and updates

Select the relevant checks; these are alternatives, not a checklist to run in
full. Combine capabilities and explicit tools in one invocation when needed:

```bash
basaltw agent doctor --capability host --json
basaltw agent doctor --capability development --tool codex --json
basaltw agent doctor --all-capabilities --json
```

The comprehensive check inventories every default terminal client but requires
only those currently installed. Use explicit `--tool` flags when an absent
client should make readiness fail; each selected tool is then required.
The development capability inventories Godot, Go, Node, and native game tooling
independently. An unselected toolchain is informational; a broken baseline is
unhealthy. `node_pnpm_missing` means the promised `--node` baseline is broken,
not that pnpm is optional. Rerun the saved setup to repair and verify Node,
npm, and pnpm together; do not install a separate package manager ad hoc.

Debian `--game-dev` selects compilers, build/debug tools, and native dependency
headers without installing a desktop or distro SDL. The `native` inventory
reports that selection, fixed commands, and pkg-config modules. Missing SDL3 or
SDL3_image appears in `project_bootstrap_required`; build the project's required
versions rather than treating their absence as a broken host baseline.
See [project tooling](https://github.com/bluehexagons/basaltwater/blob/main/docs/PROJECT_TOOLING.md)
for native bootstrap, repository Node versions, and capability limits.

Preview terminal-agent updates before applying them as the account that owns
the installation. Apply an update only when the user requested it:

```bash
basaltw agent update --dry-run
basaltw agent update --tool codex
basaltw agent doctor --last-record --json
```

`agent update` manages Codex, Claude Code, and OpenCode. It does not update
GitHub CLI, T3 Code, Basaltwater itself, managed skills, or system packages.
Refresh managed skills by rerunning saved setup from the updated control plane;
editing a VM's installed copy is overwritten by setup. `--last-record` reads
saved evidence, not a fresh check; inspect its timestamp and `current_boot`.
Do not substitute a vendor updater unless the user requests it; the managed
path verifies the tool, retains one prior executable, rolls back a broken
update, and records redacted post-update readiness.

From a control system, add `HOST USER` to `doctor` or `update`. Run the remote
dry run first.

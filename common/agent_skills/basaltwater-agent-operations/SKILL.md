---
name: basaltwater-agent-operations
description: Check or deliberately update coding agents, rotate their credentials, or protect long work from host maintenance on a Basaltwater agent VM.
metadata:
  managed-by: basaltwater
---

# Agent VM operations

Use the managed commands so checks, updates, and handoffs stay bounded and
redacted.

## Readiness and updates

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
The development capability inventories Godot, Go, and Node independently, so
an unselected toolchain is informational while a broken installed baseline is
unhealthy. `node_pnpm_missing` means the promised `--node` baseline is broken,
not that pnpm is optional. Rerun the saved setup to repair and verify Node,
npm, and pnpm together; do not install a separate package manager ad hoc.

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

## Maintenance holds

Create a hold only when work must cross the normal restart window:

```bash
basaltw agent maintenance hold --hours 8
basaltw agent maintenance status --json
basaltw agent maintenance release
```

Release it when the protected work ends. Holds expire after at most 72 hours
and do not override the host's forced-restart deadline.

## Credential rotation

Inspect credential status from the controller. Start a login only when the
user requested authentication or recovery:

```bash
basaltw agent auth status HOST USER --json
basaltw agent auth login HOST USER
```

Login defaults to a separate ChatGPT subscription session on the target.
Relay the device URL/code to the user; never authorize their account yourself.
`--open-browser` optionally opens the page on the controller. The controller
needs no Codex installation. API-key mode requires `--method api-key` and
warns that API usage is separately billed, outside the ChatGPT subscription.
Use a hidden prompt, protected `--api-key-file`, or `--api-key-stdin`; never
print secrets or put them in arguments or repositories.

Setup with `--agent-auth login` preserves or renews target credentials and
starts device authorization when recovery is needed from an interactive
terminal. Unattended setup reports the required login command. Daily and
boot maintenance continue to renew the target's own file-backed session.
Credential pulling is removed; do not share rotating subscription tokens
between VMs. `auth set` remains for explicit file imports for other tools,
including `--tool gh --active` or a protected `--file`.

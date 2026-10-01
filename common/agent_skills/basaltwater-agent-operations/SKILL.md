---
name: basaltwater-agent-operations
description: Check or deliberately update coding agents, rotate their credentials, or protect long work from host maintenance on a Basaltwater agent VM.
metadata:
  managed-by: basaltwater
---

# Agent VM operations

Use the managed commands so checks, updates, and handoffs stay bounded and
redacted.

For quick project discovery, run `basaltw agent manifest --json` from its
checkout. It reports available tools, workspace/artifact conventions, and
explicit branch-to-deployment mappings from optional `basaltwater-agent.json`.
Projects can also declare required executables and named validation recipes;
the summary reports missing prerequisites and displays commands without running
them. Use relevant project recipes when choosing checks for the user's task.
Undeclared `dev` and `staging` destinations remain unknown; tool presence does
not prove health or authentication. See the
[environment manifest guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/AGENT_ENVIRONMENT.md)
or `docs/AGENT_ENVIRONMENT.md` in a Basaltwater checkout.

For image evidence, `basaltw agent visuals compare BEFORE.png AFTER.png --json`
creates a private standalone viewer with synchronized zoom/scroll, overlays,
pixel differences, and optional per-image settings. `agent visuals capture`
replays a project capture command and settings in two managed Git worktrees;
it retains worktrees and evidence rather than swapping primary assets. Read the
[visual comparison guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/VISUAL_COMPARISONS.md)
or `docs/VISUAL_COMPARISONS.md` before running revision captures.

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

## One-time privileged actions

On a VM configured with `--privilege-broker`, request a supported root action
through `basaltw agent privilege request` instead of trying `sudo`:

```bash
basaltw agent privilege request command.run \
  --reason "Refresh package metadata" --command /usr/bin/apt-get update
basaltw agent privilege wait REQUEST_ID --timeout 300 --json
```

The request returns a `review_url` for the separate HTTPS approval portal,
normally on the VM's port 9444. Give that URL to the operator; the coding
account cannot approve the request or receive the approval password. The
portal displays the exact arguments and allows one approved attempt. Do not
put a shell, `sudo`, environment assignments, or secrets in `--command`.
Managed `basaltwater-web` publication and owner-scoped live gateway actions
already work without this approval flow. See
[Privilege approvals](https://github.com/bluehexagons/basaltwater/blob/main/docs/PRIVILEGE_APPROVALS.md)
for the supported actions and portal setup.

If the action becomes unnecessary, use
`basaltw agent privilege cancel REQUEST_ID --json`.
It withdraws your pending or approved request until the
worker claims execution. Already expired requests stay expired; claimed actions
cannot be stopped or undone by cancellation. A cancelled request needs no
operator approval and remains in the audit history.

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

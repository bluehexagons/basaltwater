# CachyOS workstation setup and diagnosis

[Skill entrypoint](../SKILL.md)

## Install and refresh tools

Use the existing desktop account. Preview an explicit selection, then apply it:

```fish
basaltw setup agent_cachyos localhost --codex --dry-run --godot --node
basaltw setup agent_cachyos localhost --codex --godot --node
```

Add only the requested tool flags. Existing optional selections are preserved
unless an explicit full setup replaces the saved selection; omitted flags do
not uninstall packages. This profile uses its private local setup record and
does not populate the generic saved-host `patch` or `deploy` workflow. To update
Basaltwater on its selected channel and replay the last successful local setup,
use `basaltw refresh --dry-run` and then `basaltw refresh`. Refresh merges
supported selection changes; finish active work before an update that may
restart selected services.

For remote setup, use `basaltw setup agent_cachyos HOST USER --claude` (or
another requested provider flag); the target must
already accept SSH for its existing non-root desktop account. Review the
[remote development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-development.md)
for controller setup, desktop-session limits, and service commissioning.

Packages use pacman. Leave full OS upgrades to the user's CachyOS workflow and
never repair a package failure with a partial `pacman -Sy` upgrade. Update
externally managed tools with their original package manager. Setup cleanup is
bounded and runs only during setup/refresh; there is no CachyOS cleanup timer.
See the [CachyOS maintenance guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-maintenance.md)
for cleanup and diagnostics.

GitHub CLI is included by default. Select at least one coding provider explicitly
with `--codex`, `--claude`, or `--opencode`.
Keep existing credentials and provider configuration. Use normal
provider login for authentication. `--node` installs the system runtime, while
`basaltw node install` prepares user-local NVM and applies a project's committed
Node pin without rerunning full setup. Do not change a project's runtime merely
because a newer host version is installed. When Codex is selected, keep its
default `CODEX_HOME`; custom locations are outside this profile.

Use the [software guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-software.md)
for supported flags and package mappings. Most applications use native
repository packages; Material Maker, Butler, SteamCMD, and T3 desktop are
explicit AUR exceptions. Preserve package-review prompts and the selected AUR
helper's policy. Do not launch SteamCMD as a readiness probe because it can
update itself and initialize personal Steam state.

## Diagnose and report

Start with the unprivileged, read-only workstation diagnosis:

```fish
basaltw local cachyos-doctor --attention --json
```

This runs the full inventory, shows all deferred/failed observations and counts
omitted successes. Deferred observations can be expected; omit `--attention`
when successful-check details or versions are needed. It does not activate
services, launch applications, capture content, or prove
that a listed socket, package, or permission works. For project-specific
Godot prerequisites use `basaltw desktop --native develop doctor --project
PATH --json`; then run only the project's declared checks. Keep observed
renderer, device, runtime, and actual project outcome separate from installed
package inventory. The GLX graphics doctor is an isolated context probe; it does
not qualify native Wayland, a project's renderer, HDR, input, or latency.
Follow the project capture recipe in the
[game development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-game-development.md).

The workstation doctor does not verify effective UFW rules. For an authorized
privileged check, use KDE's existing polkit agent when available or the owner's
terminal. Explain the command before requesting authentication; never ask for
a password in chat or tool input. Keep diagnosis read-only and unprivileged.
See the [privileged-check procedure](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-diagnostics.md#privileged-checks-from-an-agent).

Optional webhook targets are stored in the private local setup selection and
receipt. Previews send nothing; delivery failure is reported separately from
the setup result. Treat URLs as credentials and do not copy them into shared
logs or task notes.

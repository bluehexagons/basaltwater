---
name: basaltwater-cachyos-workstation
description: Install or diagnose coding tools on a locally managed CachyOS KDE workstation using its existing human account.
metadata:
  managed-by: basaltwater
---

# CachyOS coding workstation

This is an existing human-operated CachyOS KDE desktop. The `agent_cachyos`
profile installs selected tools; it does not manage the operating system,
graphics drivers, login manager, account groups, network addressing, or power
policy. Firewall changes require explicit `--lan-access`, `--access-source`,
or `--no-lan-access` options.

The main role is remote Godot development and playtesting through T3 Code,
Sunshine/Moonlight, and same-user SSH after KDE login. Production builds and
publishing use separate systems. Do not request publishing credentials or
add storefront tools for this workstation role. See the
[remote development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-development.md)
for commissioning and project/task commands. The supported desktop baseline is
a fully updated rolling release with Plasma Wayland, Plasma Login Manager, and
Shelly; older SDDM, X11, or Octopi assumptions are outside this profile.
Containers and isolated project environments remain project-owned, not part
of workstation setup.

## Install and refresh tools

Use the existing desktop account. Preview an explicit selection, then apply it:

```fish
basaltw setup agent_cachyos localhost --dry-run --godot --node
basaltw setup agent_cachyos localhost --godot --node
```

Add only the requested tool flags. Existing optional selections are preserved
unless an explicit full setup replaces the saved selection; omitted flags do
not uninstall packages. This profile uses its private local setup record and
does not populate the generic saved-host `patch` or `deploy` workflow. To update
Basaltwater on its selected channel and replay the last successful local setup,
use `basaltw refresh --dry-run` and then `basaltw refresh`. Refresh merges
supported selection changes; finish active work before an update that may
restart selected services.

SSH is opt-in. Use `--ssh` for the independent key-only port-22 service and
select `--lan-access` or private `--access-source` values when remote firewall
access is needed. Password authentication requires explicit private access;
prefer `--ssh-password-window MINUTES` for key enrollment, then verify normal
key-based SSH before expiry. Use
`basaltw refresh --ssh --ssh-password-window 15 --lan-access` to preserve the
saved tool selection while opening a temporary window. See the
[SSH setup guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos.md#ssh-access)
for service ownership, password modes, and refresh lifecycle details.

Packages use pacman. Leave full OS upgrades to the user's CachyOS workflow and
never repair a package failure with a partial `pacman -Sy` upgrade. Update
externally managed tools with their original package manager. Setup cleanup is
bounded and runs only during setup/refresh; there is no CachyOS cleanup timer.
See the [CachyOS maintenance guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-maintenance.md)
for cleanup and diagnostics.

Codex and GitHub CLI are defaults; other coding agents require explicit
selection. Keep existing credentials and provider configuration. Use normal
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

## Choose the installed skill

- Use `basaltwater-project-development` for repository instructions, project
  checks, current-status notes, and general coding workflow.
- Use `basaltwater-subagents` when bounded parallel analysis or isolated
  concurrent edits can help. Each editing agent needs a separate worktree.
- Use `basaltwater-cachyos-workspace` for this account's worktrees, notes, and
  task handoff. Do not reset or replace the human's existing checkout.
- Use `basaltwater-cachyos-desktop` for Godot/Electron work, Blender and media
  edits, native exports, and Wayland capture or input. Portal access is
  task-scoped and needs the owner's consent; do not assume XRDP or X11 control.
- Use `basaltwater-cachyos-t3code` for desktop/web mode, pairing, T3 Connect,
  and the managed CachyOS T3 service. Do not use Debian VM service commands here.
- For browser checks, use browser tools exposed to the current session and the
  project's declared browser workflow; this profile does not install managed
  Playwright.

Run `basaltw agent manifest --json` in the project to discover installed
applications, active executables, workflow instructions, and deployment
mappings. Presence in PATH is discovery only; it does not prove that a project
can build, render, capture, or publish.

## Diagnose and report

Start with the unprivileged, read-only workstation diagnosis:

```fish
basaltw local cachyos-doctor --json
```

It does not activate services, launch applications, capture content, or prove
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
See the [privileged-check procedure](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-maintenance.md#privileged-checks-from-an-agent).

Optional webhook targets are stored in the private local setup selection and
receipt. Previews send nothing; delivery failure is reported separately from
the setup result. Treat URLs as credentials and do not copy them into shared
logs or task notes.

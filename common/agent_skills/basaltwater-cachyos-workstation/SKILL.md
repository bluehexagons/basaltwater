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
policy. Local setup detects current SSH service state; use `--ssh` or `--no-ssh`
to override it. Remote-controller setup defaults to SSH and needs an already
reachable, existing non-root account with sudo. SSH-enabled setup selects
private IPv4 firewall access automatically unless explicit policy flags disable
it; LAN discovery runs again on refresh. A one-shot password bootstrap window
on port 2222 can enroll a key for 1–60 minutes using the existing account
password; `basaltw refresh --ssh --ssh-password-window 15 --lan-access` opens
a fresh 15-minute window while preserving the selected tools. The window is
never saved or replayed, and an ordinary refresh closes an active window. A
stock SSH service can be adopted into key-only mode only when the account has
usable `authorized_keys`, unless a password mode or enrollment window is
explicitly selected.
Use the [CachyOS guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos.md#ssh-access)
for adoption checks, password modes, and refresh behavior.

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

Start with `basaltw agent manifest . --capability workstation` for compact
installed-tool/backend/GPU metadata, or `--application blender --json` for one
application. Use `--capability development --json` for project recipes and
`--capability skills --json` to compare installed guidance with the invoking
source. A source update alone does not refresh installed skill files. See the
[workflow routes](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/workflows.md).
Presence in PATH is discovery only; it does not prove that a project can build,
render, capture, or publish.

## Choose a topic

<a id="install-and-refresh-tools"></a>
<a id="diagnose-and-report"></a>

- [CachyOS workstation setup and diagnosis](references/setup-and-diagnosis.md) Preview or refresh managed tools and check workstation readiness.

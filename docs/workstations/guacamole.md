# Guacamole browser desktop and console

`--guacamole` installs an embedded browser client for the configured account's
shared XRDP desktop. This is optional on non-root Debian server and workstation
profiles, including `agent_vm`, on Debian 13 (Trixie), amd64. It requires
an HTTPS web panel. Proxmox hosts, CachyOS, WSL and custom-step profiles are
unsupported.

## Choose a topic

<a id="install-and-open"></a>
<a id="mobile-display-and-shell-access"></a>
<a id="observation-and-human-control"></a>

- [Open and use a Guacamole desktop](guacamole-session.md) Install or open Guacamole, use mobile and shell access, and share control.

<a id="access-and-lifetime"></a>
<a id="installation-and-service-isolation"></a>
<a id="diagnose-and-remove"></a>

- [Guacamole administration and troubleshooting](guacamole-administration.md) Review access, service isolation, lifecycle, and removal.
- [Qualification status](#qualification-status) Check which session behavior has been tested.

## Qualification status

A clean Debian 13 amd64 container has passed package build and installation,
private RDP/SSH relay, XRDP framebuffer transport, certificate pin rejection,
Nginx HTTPS/Basic Auth/WebSocket access, cooperative desktop control, and
reconnect to the same desktop. A systemd-booted Debian container has also passed
private SSH socket activation, PAM login, shell permissions, bounded lifetime,
and grouped removal.

Synthetic browser checks cover view-only behavior, takeover, reconnect,
external permission changes, Unicode input, touch/trackpad controls, zoom, panning,
terminal input and desktop/mobile layouts. These checks do not qualify a live
XRDP desktop, a native phone keyboard or fullscreen transition, keyboard-layout
compatibility, native RDP-client coexistence, GPU applications, or every
supported desktop environment. Keep those as live qualification requirements
before relying on the affected workflow.

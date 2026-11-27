# Workstation desktop and RDP setup

[Topic index](workstations.md)

## Desktop and RDP choices

`--desktop` accepts `xfce`, `i3`, `cinnamon`, or `lxqt`. All desktop setups use
one XRDP session, reachable on loopback without `--rdp`. An explicit `--desktop`
also enables it on a headless profile. `--rdp` enables remote ingress;
the detailed session, TLS, and dynamic-resolution behavior is in
[`xrdp.md`](./xrdp.md). `--dark` configures XFCE, LXQt, or Cinnamon themes;
i3 receives an informational message because its theme is normally configured
in the user's i3 setup.

When a workstation is provisioned as a hosted Proxmox VM, it receives a
VirtIO-GPU recovery/noVNC console plus a serial socket. XRDP uses its own
xorgxrdp display; setup enables glamor only when the guest exposes a supported,
accessible DRM render node and otherwise uses the software fallback. Changing
the recovery console device alone does not accelerate the remote session. The
complete host-side settings and CPU/migration tradeoffs are in
[`proxmox.md`](../operations/proxmox.md).

RDP logins use the setup user's Unix password. Remote setups and setups that
create a user require a non-root `--password`; prefer a secret-sourced
environment variable such as `--password "$RDP_PASSWORD"`. For local setup of
an existing desktop account, `--rdp-existing-password` reuses the password
already configured for that account without exposing it in process arguments.
It cannot be combined with `--password`, cannot be used for a remote target,
and does not set or change the account password. Passwords are not written to
saved setup state. State containing this field is sanitized when it is loaded.

Use repeatable `--rdp-source IP_OR_CIDR` flags to restrict UFW ingress to the
trusted LAN, management network, or VPN clients that should connect. Without
one, RDP remains globally rate-limited. XRDP binds all IPv4 interfaces by
default; `--rdp-bind-address IP` narrows the listener to one target-side
address. Clipboard remains enabled for coding workflows, while
drive/device, printer, audio, RemoteApp, and video redirection are disabled.
Use `--no-rdp-clipboard`, `--rdp-drive-redirection`, or `--rdp-audio` to change
the explicitly managed channel policy.

XRDP permits one session owned by the setup account. Human RDP login or
`basaltw desktop start` creates it; disconnect retains it until logout.
`--rdp-idle-timeout` disconnects an idle client without ending applications.
Session-count and disconnect-cleanup flags have been removed. Use
`basaltw desktop exec -- APPLICATION` to launch into the same session.
For browser testing, prefer T3 Code or managed Playwright over desktop input.

The `pc_dev` profile includes Remmina with RDP and VNC plugins. Other profiles
can install it through the explicit custom step `install_remmina` when using
`--steps`.

## Verification

After setup, inspect installed applications as the target user:

```bash
command -v firefox librewolf vim geany code remmina blender
blender --version
flatpak list --app
xdg-mime query default x-scheme-handler/https
xdg-settings get default-web-browser
```

For remote sessions, test XRDP separately and use the log checks in
[`xrdp.md`](./xrdp.md). Machine capability differences, including Flatpak
fallbacks and software rendering in containers, are documented in
[`machine-types.md`](../reference/machine-types.md).

For agent-operated native controls, see [Desktop automation](desktop-automation.md).
It covers bounded AT-SPI inspection, named actions, semantic waits, and a small
Geany edit/save check using the default `agent_code_vm` editor.
Use `basaltw agent manifest --json` to discover installed desktop application
workflows. For a Blender editing check in a running shared desktop, use
`basaltw desktop exec -- blender /absolute/project/scene.blend`; use the
[background-render recipe](desktop-development.md#blender) on a headless host.

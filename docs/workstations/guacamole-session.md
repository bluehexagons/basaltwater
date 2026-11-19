# Open and use a Guacamole desktop

[Topic index](guacamole.md)

## Install and open

```bash
basaltw setup agent_vm 192.168.1.50 agent --codex \
  --web-panel --ssl --web-panel-password "$PANEL_PASSWORD" \
  --guacamole --password "$RDP_PASSWORD"
```

Provide both values from a protected secret source. Variables keep literal
passwords out of shell history, but expanded arguments may be visible to local
process inspectors; run setup on a trusted controller and do not store the
values in scripts. An existing account needs a valid Unix password for browser
RDP login. Guacamole does not reuse the panel's Basic Auth password and cannot
use local Unix peer authentication for its RDP connection. `--harden-user`,
which locks password login, is rejected.

For an existing HTTPS panel, saved panel port and TLS settings are retained:

```bash
basaltw patch 192.168.1.50 agent --guacamole
```

| Flag | Effect |
| --- | --- |
| `--guacamole` | Install the browser desktop, console and shared desktop prerequisites; requires `--web-panel` and `--ssl` on a new setup |
| `--no-guacamole` | Remove browser desktop/console access and their managed units; retain the desktop, applications and pinned release |
| `--no-web-panel` | Remove the panel and Guacamole access together |

Without `--rdp`, XRDP remains bound to loopback and no RDP firewall ingress is
added. Explicit `--rdp` retains its normal native-client access policy; the
listener must accept local IPv4 connections. Choosing a different environment
with `--desktop` follows the existing shared-desktop setup and logout policy.
Adding desktop support can require an existing managed desktop to log out;
save work before setup. See [XRDP](xrdp.md).

Open **Desktop** in the sidebar, or **Desktop (Guacamole)** under the panel's
**Services**. Desktop and Console retain the panel's sidebar links for installed
features. JavaScript is required on these access pages.
Enter the Unix password and choose **Connect to desktop**. Connecting explicitly
starts or reuses the managed desktop. Merely opening the page does not start it.
The Unix password is used in memory for the RDP handshake and is not saved,
returned in responses, or written to logs.

**Check desktop status** reads the supervisor's state, geometry and saved agent permission
state without starting a desktop or creating a browser session. Opening the
page itself remains read-only. If the supervisor is stopped, saved permission
is unavailable to the confined gateway until you connect; the owner's native
control window and CLI can still inspect it.

## Mobile display and shell access

The desktop toolbar wraps on narrow screens and remains available in
fullscreen. Expand **Display & touch settings** to choose **Fit screen** or
100%, 150% or 200% local scaling. Fit uses the available width and, in
fullscreen, the available height. Zoom changes only the browser view, not the
shared desktop's resolution. **Pan display** lets you scroll a zoomed display
without sending clicks or scroll-wheel input to the desktop.

**Trackpad** is the default touch mode: move with one finger, tap to click,
tap with two fingers for a right click, and swipe with two fingers to scroll.
**Direct touch** clicks at the tapped location. After taking control, use
**Keyboard** to focus a text field and open a phone's keyboard. The accompanying
toolbar supplies Ctrl, Alt, Tab, Esc, Backspace, Enter and arrow keys. Ctrl and
Alt toggle until released; leaving the desktop controls, switching away from
the browser, observing or disconnecting releases held modifiers. The text field
supports Unicode and input composition. Fullscreen support depends on the
browser; rotation and zoom controls remain available when it is unsupported.

In both Desktop and Console, press **Ctrl + Alt + Shift** together to release
held keys and move focus from the remote display or text field to **Keyboard**.
Then use Tab or Shift+Tab to navigate the page controls. Without this shortcut,
Tab and Escape continue to reach the remote application.

**Console** in the sidebar, or **Open browser console** under Desktop's
**Command line access (SSH)**, opens an embedded Guacamole SSH terminal.
Enter the Unix account password and select **Connect to console**. Opening the
page is read-only; connecting starts a separate shell without starting,
connecting to, pausing or logging out the desktop. One console and one desktop
connection may coexist. The console has the account's normal shell, home,
network and sudo permissions; it is not the restricted gateway service's shell.
Agent tasks continue running independently.

The console resizes with the window, rotation and fullscreen view, including
when opening or closing its mobile keyboard controls. Choose
**Text size** before connecting. **Keyboard** exposes phone text input and
Ctrl, Alt, Tab, Esc and arrow keys; two-finger swipes scroll the terminal.
Moving the pointer outside the terminal or switching focus keeps the shell
open. Leaving the focused controls releases held keys. Pointer events are
clamped to the terminal bounds, including mouse-button releases and scaled
touch input.
Sessions end after one hour or ten minutes without keyboard/pointer input.
Polling, resizing and terminal output do not renew inactivity. Disconnecting,
closing the tab or expiring the session closes its shell. Use `tmux` or `screen`
when work should survive disconnects. Clipboard, SFTP, file transfer, recording
and arbitrary destination/command selection are disabled.

This first console integration ships with `--guacamole` and its shared desktop
prerequisites. It does not require the desktop to be running, but is not a
separate headless-only installation option.

The Desktop page also provides a copyable command for an existing SSH
client. It needs no desktop connection and uses the panel hostname with SSH's
default port 22; adjust the destination or add `-p PORT` before `--` when SSH
uses another endpoint. SSH retains its existing authentication policy and
requires your SSH credentials. The embedded console uses a separate
[private Unix login endpoint](guacamole-administration.md#installation-and-service-isolation).

## Observation and human control

See [access and session lifetime](guacamole-administration.md#access-and-lifetime)
for observation-only expiry and session closure.

New browser desktop connections are view-only. The gateway enforces this even if a
client modifies the JavaScript or sends keyboard/pointer instructions directly.

- **Take control** saves a human override, then enables browser keyboard and
  pointer input. On agent workstations, choose 15 minutes (the default), 5 minutes,
  1 hour, or **Until I enable agents**. The button changes to **You have control**
  while active. Click the display to focus keyboard input.
- **Observe only** disables browser input and keeps the saved permission.
- **Enable agents** ends the override or disabled state and allows authorized
  agent actions. **Disable agents** blocks them until an explicit human change.
  These choices appear on desktops with agent features. Ordinary desktops show
  **Release control** and use indefinite takeover.
- **Disconnect** revokes the current browser connection,
  scoped to this page's attachment. A disconnected page can revoke the active
  connection from another tab. A stale page cannot revoke a replacement
  connection with its old attachment. Disconnect releases this browser's control
  and keeps saved agent permission and any override deadline. The desktop and
  applications keep running. Closing the tab or losing the stream performs the
  same cleanup.
- **Log out desktop** requires you to take control first, asks for confirmation, and requests a
  normal desktop logout. This ends the desktop and its applications. The browser
  remains usable for unsaved-work dialogs; canceling logout retains control until
  you observe, enable agents, or disconnect. Successful logout clears the browser session
  automatically, so the next connection can start a new desktop directly.
- **Fullscreen** changes the browser view. The client scales the display locally;
  it does not request live XRDP resolution changes.

One browser desktop connection is supported per VM account, alongside one console.
XRDP also supports only one
human RDP connection in this configuration; a native RDP client can take over
the same display and disconnect the browser. Desktop restart or geometry changes
require reconnecting. Permission is cooperative same-account coordination;
see [persistent desktop control](desktop-control.md) for approval boundaries,
migration and storage. Keep sensitive administrator sign-ins and privilege approvals on your
own device, outside the shared desktop.

The gateway binds permission actions to the observed desktop generation and geometry;
the desktop supervisor checks these before changing state. Control is available
only after the native tunnel supplies its first display sync. Canceling a login
revokes its pending handshake immediately. The page serializes control requests
and refreshes authoritative status every three seconds. The gateway independently
checks permission, its revision and browser ownership before accepting keyboard
or pointer input. A human permission change or override expiry invalidates
browser input. Browser ownership expires after 45 seconds without a gateway
heartbeat. Heartbeats and cleanup keep the saved permission and its original
deadline. Timed overrides enable agents at the chosen deadline, including after
a disconnect or restart; indefinite overrides remain until a human changes them.
Cleanup is conditional on the original desktop generation and ownership token.

Clipboard, drive/file transfer, printing, audio, SFTP and recording are disabled.
Browser instructions cannot select hosts, ports, protocols, other users, existing
Guacamole connection IDs, or enable these channels.

# Use and share an XRDP desktop

[Topic index](xrdp.md)

## Start and share the desktop

As the configured account, without sudo:

```bash
basaltw desktop status
basaltw desktop start
basaltw desktop exec -- thunar
basaltw desktop screenshot --output /tmp/desktop-1.png
basaltw desktop logout
```

All commands return JSON. `status` observes without starting a session; `start`
reuses one already running. `exec` and screenshots require a running session.
`exec` launches argument vectors with the desktop's X authority and D-Bus
environment and the invoking terminal's working directory, so relative project
paths work as expected. It does not execute a shell or change unrelated user services.
The session declares its desktop identity before starting D-Bus, including the
XFCE menu prefix, so desktop-specific autostart entries and toolkit integration
can recognize it.

`start` waits for the window manager and display geometry to become usable,
including when a human login wins a simultaneous start. A startup request gets
30 seconds, followed by up to 30 seconds of readiness polling; concurrent agent
starts serialize and reuse the result. While initializing, `status` reports
`starting`; unavailable geometry is `null`, with a diagnostic `detail` when
the geometry query fails. Wait with `start` before launching applications or
sending input. A startup error never automatically logs out or retries login.

The local agent startup uses XRDP 0.10's `xrdp-sesrun` Unix peer authentication;
it needs no stored password. This is for local Unix accounts; it does not
unlock password-protected keyrings or provide AD/Kerberos login.
See the [upstream explanation](https://github.com/neutrinolabs/xrdp/discussions/3467).

A human can connect first using an RDP client and the same account's Unix
password, or connect after the agent starts the desktop. Both see the same
applications. Only one human viewer is supported; another connection takes
over that display. The active RDP client controls resolution. Agent startup
uses 1600×900 until a client resizes it, so native media editors' minimum
window sizes fit without human intervention. Existing sessions keep their
current resolution; this default takes effect on the next agent-created session
after updating Basaltwater. No VNC server or second GUI is started.

With remote ingress disabled, connect through an SSH tunnel from the client:

```bash
ssh -N -L 13389:127.0.0.1:3389 agent@VM
```

Point the RDP client at `localhost:13389`. RDP authentication and certificate
verification still apply. A passwordless account can start the desktop locally
but needs a Unix password set by its administrator for human RDP login.

Disconnecting either participant retains the session. Logout or VM shutdown
ends it. No desktop autostarts at boot or restarts on failure. A new authenticated
RDP login counts as a new start after logout, including clients that automatically
reconnect: disable client auto-reconnect when intentionally logging out.
`logout` requests the desktop's normal logout, so unsaved-work dialogs can cancel
it. Observe `status` afterward; the request alone is not proof of completion.

Screenshots return the session `generation` and full-display pixel `geometry`.
Use `desktop windows` to discover current window IDs and titles. Capture one
application's client area with `desktop screenshot --window ID --output PATH.png`,
or use `--active-window` for the active application. Neither option changes focus;
hidden or closed windows produce an error instead of a full-desktop fallback.
Partially off-screen windows also fail with bounds guidance: move or resize the
window fully onto the desktop, hide panels that impose a larger minimum size,
or use a larger resolution. Repeating the same capture cannot fix clipping.
Inspect captures for overlapping content before sharing. The `image_geometry`
field describes the PNG, while top-level `geometry` still describes the desktop.
Window metadata includes its desktop `origin`; recapture the desktop before
coordinate-based input because a window can move between commands.

Agents can include inspected PNGs through their client's image/attachment support
or local Markdown image links. Keep response artifacts at stable paths outside
Git, retain linked images, and avoid exposing unrelated windows or credentials.
The desktop skill documents capture selection and response attachment guidance.

Use those exact values for bounded input, then inspect the result:

```bash
basaltw desktop input --generation GENERATION --geometry 1280 720 click --x 100 --y 200
basaltw desktop input --generation GENERATION --geometry 1280 720 key --key ctrl+s
basaltw desktop input --generation GENERATION --geometry 1280 720 text --text 'Example'
```

`move` takes coordinates; click buttons 4–7 scroll. Agent mutations follow the
persistent desktop control state. Enabled allows authorized desktop work
without an unpause request or lease. Human override blocks agent mutations
while allowing observation; disabled also blocks mutations until a human
explicitly changes state. RDP input remains available. Recapture after any
resize, reconnect or stale-input error. Screenshots are private PNGs at new
paths, never overwritten; delete temporary evidence after use. See
[persistent desktop control](desktop-control.md) for state transitions and
approval rules.

Change state only after explicit human approval or a human UI choice. Use
`--human-approved` for an approved CLI state change; a task request alone is
not approval. For a timed or indefinite override:

```bash
basaltw desktop control enabled --human-approved
basaltw desktop control human-override --human-approved
basaltw desktop control human-override --seconds 900 --human-approved
basaltw desktop control human-override --indefinite --human-approved
basaltw desktop control disabled --human-approved
```

Overrides accept 60–86400 seconds or `--indefinite`. Timed expiry returns to
enabled because the human selected that duration; disabled and indefinite
override never auto-enable. The native CachyOS command adds `--native` before
`control`. Native helper lifetime is unlimited by default; optional
`--session-seconds` accepts 60–28800, with `0` unlimited, and renewal uses the
same range. See [persistent desktop control](desktop-control.md) for the full
contract.

For almost all browser testing, use available T3 Code collaborative preview or
managed Playwright. Desktop input is for native applications, desktop-specific
integration, or a justified fallback. These browsers have separate profiles
and authentication. The managed `basaltwater-desktop` skill provides routing
and command guidance for Codex/OpenCode.

## Native application productivity

Setup installs `wmctrl`, `xdg-utils` (for `xdg-open`) and `python3-tk` from the normal package repositories
and adds **Shared Desktop Control** to the application menu. Rerun setup to
install these additions; setup handles managed desktop logout. The control
window offers **Enable agents**, **Disable agents**, and **Take control** with a
15-minute default, duration choice, or indefinite override. Closing the window
does not change remembered state. See
[persistent desktop control](desktop-control.md) for state transitions and
approval rules.

```bash
basaltw desktop doctor
basaltw desktop open /home/agent/document.txt
basaltw desktop open /home/agent/document.txt --reveal
basaltw desktop exec --wait-window Mousepad --timeout 15 -- mousepad
basaltw desktop windows
basaltw desktop window focus --window WINDOW --identity IDENTITY --generation GENERATION
basaltw desktop window resize --window WINDOW --identity IDENTITY --generation GENERATION --width 800 --height 600
basaltw desktop wait --window WINDOW --condition active --generation GENERATION
basaltw desktop screenshot --active-window
```

Use the configured account's actual document paths. `open` accepts existing local
paths and uses `xdg-open`; application packages and file associations must exist.
`--reveal` opens the parent directory. `exec` flags precede `-- APPLICATION`.
Launch results include a PID and a `launch` token; inspect it using
`desktop launch-status LAUNCH --generation GENERATION`. Records are session-local
and limited to the latest 128 launches. Launchers can exit successfully after
delegating to an existing process. `--wait-window` uses a literal title substring,
reports matching windows and which IDs existed before launch, and never promises
that a match belongs to the new process or that a document has finished loading.
`window_baseline` records completeness and observed IDs. An unavailable baseline
does not block the requested launch; it has `observed_window_ids: null` and a
diagnostic detail. `existing_window_ids` is `null` when the baseline is incomplete
or unavailable, so matches cannot be classified as new. Launch still checks the
same session and control revision. A failed wait retains the launch PID/token;
an interrupted launch reply may leave no receipt even if the app started.
Inspect PID/class and the document; do not retry a timed-out launch blindly.

Window inventory adds PID/class where available, an identity fingerprint, and
the active window ID. It uses the window manager's stacking list, falling back
to its standard client list when stacking metadata is unavailable. If neither
list is readable, inventory reports `coverage: unavailable`, `truncated: true`
and diagnostic detail. This permits bounded polling while the first app starts,
and cannot establish absence. An explicitly empty client list remains valid.
Window-targeted mutations still fail when metadata is unavailable. Title inspection preserves
Unicode filenames and ellipses in an English UTF-8 locale.
Mutations require current generation and identity;
title changes invalidate the fingerprint. It reduces stale targeting but cannot prove
an X window ID was never reused. Available operations are focus, move, resize,
maximize, minimize, restore, and close. Desktop/panel windows are rejected.
`close` requests normal window-manager closure, allowing unsaved-work prompts;
it never destroys the X client. Verify completion with a fresh observation.

`wait` supports present, visible, active, and absent conditions. Window ID, title,
and PID selectors combine with AND. Absence requires a complete inventory.
Polling takes place outside the supervisor; human override remains available.
Timeout defaults to 15 seconds (maximum 120),
plus any in-flight bounded request. A timeout does not kill or relaunch the app.

Screenshots without `--output` receive unique persistent paths under private
`~/Pictures/basaltwater/` and return a capture timestamp. These files are never
automatically removed; retain linked response artifacts and clean disposable ones.

`sequence PATH --generation GENERATION` accepts a JSON list of 1–20 `input`,
`window`, `screenshot`, or `windows` requests with their normal payload fields.
Do not embed control revisions or generations. Sequences remain limited to 20
seconds and 1–20 actions, and stop when the control revision changes or input
becomes stale. Operators coordinate manually; there is no exclusive lease.
Steps are not transactional: a failure reports partial results without rollback
or retries. Long waits belong outside a sequence.
For example, after observing current desktop geometry, a short save-and-capture
file can contain:

```json
[
  {"action": "input", "geometry": [1280, 720], "kind": "key", "key": "ctrl+s"},
  {"action": "screenshot", "active_window": true, "output": "/tmp/save-check-1.png"}
]
```

`doctor` reports session state, executable availability, handoff dependencies,
and XRDP service activity. It returns nonzero for detected problems and suggests
next steps without starting/restarting services or capturing content. Human RDP
reconnect/resize, clipboard, and application responsiveness remain explicitly
unverified by this check.

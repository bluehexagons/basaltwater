# Lifecycle and application launch

Run commands as the setup account, without sudo:

```bash
basaltw desktop status
basaltw desktop start
basaltw desktop exec -- thunar /home/agent
```

Use the actual account's paths. `status` only observes; `start` reuses a running
desktop and waits for readiness when initialization is in progress. During
`starting`, geometry may be `null`; inspect any `detail` if startup fails.
`exec` accepts an argument vector, keeps the invoking shell's working directory,
and supplies the session's display, X authority and D-Bus environment.
Do not guess `DISPLAY`, copy authority cookies,
start a second X server, or import GUI variables into the global user manager.
Output is JSON; failures return a nonzero exit code and an `error` field.

Use `desktop open /absolute/document` to use its default native application,
or add `--reveal` to open the parent directory. For launch readiness:

```bash
basaltw desktop exec --wait-window Mousepad --timeout 15 -- mousepad
basaltw desktop wait --title Mousepad --condition visible --timeout 15
basaltw desktop launch-status LAUNCH --generation GENERATION
```

For known splash windows, add `--exclude-title Startup --stable-seconds 1`
to `exec`/`open` with `--wait-window`, or to `wait`. Exclusions are literal,
case-sensitive title substrings (up to 16, each at most 512 characters).
Stability accepts 0–5 seconds, no longer than the timeout, and defaults to zero.
It requires the same matching identities/titles/PIDs across observations;
changes or incomplete inventories restart the interval. Polling remains usable
during human override or disabled control. Inspect the
actual document afterward; a stable title does not prove that it loaded.

Launch results include a session-local `launch` token and PID. Only the latest
128 launches are tracked. A title match can be an existing application window;
inspect returned PID/class and `existing_window_ids` before acting. A running
process or visible window does not prove document readiness. A launcher may exit
successfully after asking an existing process to open a document. On timeout,
inspect before retrying; never automatically launch a duplicate application.
## Human handoff and logout

The human can open **Shared Desktop Control** from the application menu and
choose **Take control** (15 minutes by default, or indefinite), **Enable agents**,
or **Disable agents**. `desktop handoff` opens the same window while agents are
enabled. Closing it, disconnecting RDP, logout, and restarting the supervisor
preserve the choice. Ordinary RDP input does not detect human intervention.

Only explicit human approval or UI interaction permits changing the state.
An approved CLI transition uses `desktop control enabled|human-override|disabled
--human-approved`; override accepts `--seconds 900` or `--indefinite`. Never add
that flag merely because a desktop task is blocked. Enabled work requires no
lease or extra approval. See [persistent control](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-control.md)
for storage, migration, timers, and account-level limits.

The human connects with their RDP client using the setup account's Unix password.
They can disconnect and reconnect without ending applications. An SSH tunnel
can reach a loopback-only listener; do not widen network access merely for a test.
Do not save the user's password in agent scripts or command arguments.

Leave the session running after your task unless logout is requested.
`basaltw desktop logout` asks the desktop to log out; unsaved-work dialogs
may require human action. Confirm `status` becomes stopped. Do not force logout,
kill the user's systemd manager, or restart sesman to recover a failed app.
Explicit start or a new authenticated RDP login creates a fresh session after
logout. Disable RDP-client automatic reconnection when intentionally logging out.

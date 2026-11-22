# User-approved KDE Wayland control

Run locally as the existing KDE user. `basaltw desktop --native doctor` checks
session sockets and library imports without requesting permission. `start`
requests KDE permission; only the desktop owner can select one monitor and
allow keyboard/pointer access. Wait for `status` to report `running`. Denied,
cancelled or incomplete consent grants no usable automation. If dependencies
are missing, use the workstation setup skill rather than replacing system Python.

`initializing` reports the current `portal_stage`. `awaiting-consent` means KDE
accepted Start and a response is pending, not that a visible dialog is verified.
`consent_expires_in` shows the approximate seconds left in the two-minute wait;
it is separate from session lifetime and also appears in the handoff window.
After helper exit, `status.last_failure` retains the stage, timestamp and detail.
Stages/errors also remain in the private runtime `helper.log` across restarts.

The helper uses a private same-user Unix socket, no network listener,
privileged input daemon, systemd service or login autostart. It has no default
lifetime limit; pending consent remains subject to the portal response timeout.
It closes on stop, portal
revocation, bus loss or loss of the session socket.
It is an account-local control channel, not a sandbox against other programs
already running as this account.

For autonomous future sessions the owner may opt in with `start --remember`,
explicitly approving one monitor, keyboard, pointer
and KDE's persistence option. This requires RemoteDesktop version 2. Later
`start` calls use the saved token; KDE may decline restoration or require a new
prompt. `grant_saved` and `restore_attempted` do not verify prompt-free access.
The private grant record lives in `~/.local/state/basaltwater/native-desktop`
(0700 directory, 0600 file), outside projects and logs. Tokens are single-use;
the returned replacement is saved without exposing it in status or arguments.
An interrupted restore keeps its consumed identifier solely for revocation.
Plain `start` preserves the saved opt-in and agent control state even without a reusable
token, requesting fresh owner approval if needed. Use `revoke` to remove the
opt-in before starting temporary access.

An optional `start --session-seconds SECONDS` bounds backend lifetime to 60–28800
seconds. `renew --generation GEN --seconds SECONDS` changes that limit on a
running, enabled, unexpired backend; `0` (the default) removes it. Neither
changes human permission. After an explicit limit expires, use `start`, inspect
the new generation and recapture; initial or withdrawn grants need owner
approval. There is no login autostart. `stop` keeps a grant;
owner `revoke` closes control, deletes only its portal PermissionStore entries
and removes the saved record, including while stopped. Retry reported store
errors; never work around revocation with a different input service.

## Sleeping displays

Native `doctor` reports timestamped `checks.display_power` (on/off/mixed/unknown),
and the local CachyOS doctor includes `session.display-power`. These read-only
queries do not wake displays. An unlocked session/XWayland response does not
establish display power; unknown observations and partial output coverage remain
unverified.

A sleeping monitor can leave capture black, frozen or unavailable even when the
application and portal are running. While agent control is enabled, try
`basaltw desktop --native wake` from the existing KDE session. It requests
`kscreen-doctor --dpms on` (from `libkscreen`) with a ten-second timeout, without
typing keys, moving the pointer, changing permission, or starting a portal.
It also works before the native helper starts. Disabled and human override
still block this agent action; do not enable agents merely to wake the monitor.

Wait briefly, then take and inspect a fresh capture before input. A successful
wake request does not prove the physical display or capture is ready. If needed,
inspect `kscreen-doctor --dpms show` and `kscreen-doctor -o`; an unplugged,
physically switched-off, disabled or locked display needs separate diagnosis.
Do not unlock, enable outputs, change layout or disable idle power policy as a
substitute. If capture stays stuck after wake, inspect status before restarting
the helper and obtaining any KDE approval it requests.

For Moonlight connection failures while the monitor sleeps, see
[Sunshine display wake](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-streaming.md#sleeping-monitors-and-moonlight).

## Human control and shutdown

`basaltw desktop --native handoff` opens the human control window from the local
KDE session, even while the helper is stopped or agents are blocked. Keep
`--native` before `handoff`; the bare command addresses Debian XRDP. Opening the
window neither stops an agent's loop nor transfers ownership or changes permission.
Relinquish your own action loop separately; only the human's UI choice or an
explicitly authorized control request changes persistent permission. Spawn success
does not establish window visibility. See the
[handoff distinction](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-control.md#handoff-is-not-a-permission-change).

Check `status.agent_control`: `enabled` permits authorized task input without
asking to unpause or obtaining a lease. `human-override` and `disabled` block
launches, input and semantic mutations while observation remains available.
Only explicit human approval or the human UI permits a state change; asking to
use an application alone is insufficient. Never change the state to bypass a block.

The handoff window offers Take control (15 minutes by default, or indefinite),
Enable agents, and Disable agents, with backend health shown separately.
State survives helper restarts, logout and reboot. A timed override stores its
original deadline and returns to enabled as approved by that human choice.
Indefinite override and disabled never expire. For an explicitly approved CLI
change, use `control enabled|human-override|disabled --human-approved`;
override accepts `--seconds 900` or `--indefinite`. State controls work even while
the helper is stopped. See [persistent control](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-control.md)
for migration, storage, and the same-account authorization boundary.

In-flight operations can finish before a human request is processed (bounded
capture/accessibility timeouts); this is not emergency compositor isolation.

`stop` closes the portal and helper, preserving the desktop and applications.
Stop, revoke and closing the handoff window do not change agent permission.
Leave enabled control available between tasks. Close
only disposable instances created for the task, after confirming saved files.
Keep editable sources, exports and useful verification logs with the project.
See the [native desktop guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-desktop.md)
for implementation limits and live qualification evidence.

## Choose a topic

<a id="observe-act-verify"></a>
<a id="missing-or-stuck-physical-keys"></a>

- [KDE Wayland native control recipes](native-control-recipes.md) Observe, act, verify, and recover from lost or stuck keys.

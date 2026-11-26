# User-approved KDE Wayland control

Run locally as the existing KDE user. `basaltw desktop --native doctor` checks
session sockets and library imports without requesting permission. `start`
requests KDE permission; only the desktop owner can select one monitor and
allow keyboard/pointer access. Wait for `status` to report `running`. Denied,
cancelled or incomplete consent grants no usable automation. If dependencies
are missing, use the workstation setup skill rather than replacing system Python.

`initializing` reports the current `portal_stage`. `awaiting-consent` means KDE
accepted Start and a response is pending.
`consent_expires_in` shows the approximate seconds left in the two-minute wait;
it is separate from session lifetime and also appears in the handoff window.
A stopped helper reports a null `portal_stage` and retains the prior stage in
`last_portal_stage`. After a failure, `last_failure` retains its stage, timestamp
and detail. The private runtime `helper.log` keeps stage and error messages
across restarts.

`consent` records the fresh or restore attempt, its outcome, the last completed
portal stage, and the token disposition. `display_power` is a read-only DPMS
sample taken before the portal request. `prompt_visibility` is `unknown` because
the portal does not report it. `response_code` is available for a nonzero portal
response. Code `1` means cancellation, and code `2` covers other unsuccessful
responses. A `timed-out` outcome means the response deadline elapsed. Inspect
`status` after a timeout. The helper closes the pending request and leaves any
next consent attempt to an explicit `start`. Calling `start` on a running
approved helper reuses its current session.

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
`consent.token_disposition` reports `consumed` when the restore token is retained
solely for revocation, `saved` or `replacement-saved` when a new token is stored,
and `revoked` when the portal confirms removal. This describes the attempt at the
time of its receipt. Check the current `grant_saved` for whether a token is
available to try on the next start.
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
Relinquish your own action loop separately and leave permission changes to the
operator in the KDE control window. Spawn success
does not establish window visibility. See the
[handoff distinction](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-control.md#handoff-is-not-a-permission-change).

Check `status.agent_control`: `enabled` permits authorized task input without
asking to unpause or obtaining a lease. `human-override` and `disabled` block
launches, input and semantic mutations while observation remains available.
The operator can change permission in the control window when needed.

The handoff window offers Take control (15 minutes by default, or indefinite),
Enable agents, and Disable agents, with backend health shown separately.
State survives helper restarts, logout and reboot. A timed override stores its
original deadline and returns to enabled as approved by that human choice.
Indefinite override and disabled never expire. The native CLI and helper socket
refuse direct permission changes. Open the window with the KDE launcher or
`basaltw desktop --native handoff` even while the helper is stopped.
See [persistent control](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-control.md)
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

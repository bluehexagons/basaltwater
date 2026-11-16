# Experimental Linux QA gamepad

The optional source command `python3 -m desktop.gamepad_qa` creates one owned
uinput gamepad. It requires an existing enabled native desktop helper, permission
to use `/dev/uinput`, and the system Python `evdev` package. It installs nothing
and changes no global mappings, Steam settings or persistent control choice.
This is an experimental Linux test route, not physical-controller qualification
or a new portal input capability.

A native KDE Wayland check on 2026-11-16 used Godot 4.7.2's joypad inspector.
It recognized the Xbox mapping and observed face/shoulder/menu/stick buttons,
d-pad diagonal, both sticks and both triggers. Controls returned to neutral
(Godot's signed-stick center can be approximately 0.000015). Client disconnect
released held input and removed the device. SIGKILL during a held button/stick
removed the device and cleared the inspector's state; that action has no final
release receipt. A 60-second lifetime removed the idle device at expiry (task
exit 1 reports expiry). Raw captures, event logs and task receipts remain private.
Live human takeover, helper loss, rumble and physical devices remain unqualified.

Run from the Basaltwater checkout with the intended Python. Create a private
evidence parent outside Git, then launch the server as a supervised development
task with a finite lifetime:

```bash
python3 -m desktop.gamepad_qa serve --output /private/new-pad-run --lifetime 900
python3 -m desktop.gamepad_qa status
```

One server owns the pad and a same-user private Unix socket. The identity is
`Basaltwater QA Virtual Gamepad`, USB Xbox 360 mapping (045e:028e). Buttons use
physical-position names: south/east/north/west, left/right-shoulder, select,
start, guide and left/right-stick. Axes are left/right-x/y (-32768..32767),
left/right-trigger (0..255), and dpad-x/y (-1..1); neutral is zero throughout.
Force feedback is unavailable.

First qualify the device in a disposable input inspector. Inspect a fresh
native screenshot and save its complete JSON receipt. Each action consumes
that observation in this bridge; capture and inspect again after the action.
For example:

```bash
python3 -m desktop.gamepad_qa press --observation /private/capture.json --button south --hold-ms 120
python3 -m desktop.gamepad_qa press --observation /private/next-capture.json --axis dpad-y=1 --hold-ms 120
python3 -m desktop.gamepad_qa stop
```

Actions accept simultaneous buttons/axes and a 0–5000 ms hold. All owned buttons
and axes return to neutral afterward. Freshness, helper generation, geometry
and control revision must match the native helper. This bridge checks the shared
human control state during holds and while idle; disabled/override, changed
session, lost helper, client disconnect, lifetime expiry, SIGINT or SIGTERM end
the bridge and destroy its device. SIGKILL relies on kernel uinput descriptor
closure and cannot write a final release receipt. Checks are polling, with
20 ms hold checks, 200 ms helper checks and a 250 ms helper request deadline;
they are not emergency compositor isolation. A stopped or changed helper must
be reconciled before starting a new bridge.

Each dispatched action retains requested events, dispatch/release timestamps,
observation provenance and delivery/neutralization status in private JSON.
The native screenshot recorder does **not** record these pad events; retain both
inventories and link them in the playtest handoff. A completed receipt proves
injection and release, not visible game response. An uncertain/error result must
be inspected and reconciled, never automatically retried. A rejected press
conservatively ends this bridge; inspect live state before a new server/capture.
Cleanup/status accept
no screenshot because stopping must remain possible after control is blocked.

Before gameplay, verify button release, d-pad, both sticks, triggers, neutral,
disconnect, expiry and owned-device removal. Offline mocked tests cover blocked
control and control changes; report any live takeover, hardware, Steam Input,
rumble or platform gaps explicitly. Keep a single desktop/input owner. Use the
[game-testing workflow](game-testing.md) for fresh-player learning and evidence.

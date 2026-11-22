# Linux virtual controller for testing

`basaltw desktop [--native] gamepad` creates one owned uinput controller. Use
`--native` for CachyOS KDE Wayland; plain `desktop gamepad` uses the configured
Debian desktop supervisor. Both routes require running desktop observation and
enabled shared agent control. Events travel through Linux uinput, separately
from portal keyboard/pointer delivery. No Steam or global mapping is changed.

## Prerequisites

Run the adapter as the existing non-root desktop account. The read-only check
does not open uinput, create a controller, start a helper or change permission:

```sh
basaltw desktop --native gamepad doctor --json  # CachyOS
basaltw desktop gamepad doctor --json           # Debian
```

Install `python-evdev` on CachyOS or `python3-evdev` on Debian for system Python.
For an isolated Python installation, install `basaltwater[gamepad]` into that
environment instead; system packages may be invisible to its interpreter.
The [Debian package](https://packages.debian.org/trixie/python3-evdev) and
[evdev guide](https://python-evdev.readthedocs.io/en/latest/tutorial.html#injecting-input)
provide the uinput bindings. The optional extra leaves other platforms unchanged.

The kernel needs uinput and the account needs read/write access to `/dev/uinput`
and read access to the adapter's resulting event device. An administrator can
provision a dedicated testing group on either distribution. Replace `tester`
with the selected desktop account. Do this once at a maintenance checkpoint:

```sh
sudo groupadd --force basaltwater-gamepad
sudo usermod --append --groups basaltwater-gamepad tester
sudo tee /etc/modules-load.d/basaltwater-gamepad.conf <<'EOF'
uinput
EOF
sudo tee /etc/udev/rules.d/70-basaltwater-gamepad.rules <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="basaltwater-gamepad", MODE="0660"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="Basaltwater QA Virtual Gamepad", GROUP="basaltwater-gamepad", MODE="0660"
SUBSYSTEM=="input", KERNEL=="js*", ATTRS{name}=="Basaltwater QA Virtual Gamepad", GROUP="basaltwater-gamepad", MODE="0660"
EOF
sudo modprobe uinput
sudo udevadm control --reload-rules
sudo udevadm trigger --action=change --subsystem-match=misc --sysname-match=uinput
```

Start a new login with the new group membership, including the process launching
the game and any user service manager that launches it. Preserve active work;
do not forcibly restart a shared desktop to refresh groups. A dedicated group
also works for Debian XRDP sessions without an active local seat; `uaccess`
alone may not grant those sessions access. This grants trusted group members
kernel virtual-device creation, not permission to read arbitrary physical input
devices. Basaltwater does not add the account to the broad `input` group or make
devices world-writable. Existing administrator-managed access can be retained.
Run doctor again, then qualify actual device recognition in a disposable inspector.
VMs need their own uinput support; installing a Python package cannot supply it.

## Commands and ownership

Use a private evidence parent outside Git. `serve` runs in the foreground with
a finite lifetime, so supervise it using the existing development-task route:

```sh
basaltw desktop --native develop exec --project /absolute/basaltwater -- \
  basaltw desktop --native gamepad serve --output /private/new-pad-run --lifetime 900
basaltw desktop --native gamepad status --json
```

On Debian omit **both** occurrences of `--native`. The installed CLI must include
the adapter; use an explicit executable path when testing another installation.
Retain the returned development task handle and verify its completion at closeout.
An unacknowledged launch needs status/task inspection before another start.
The source entry point `python3 -m desktop.gamepad_qa` uses native by default;
its top-level `--debian` flag selects Debian. Prefer the public CLI for run briefs.

One same-user private Unix socket and lock own the controller across both
backends. Clients must select the server's original backend. The identity is
`Basaltwater QA Virtual Gamepad`, USB Xbox 360 mapping (045e:028e). Button names
describe physical positions: south=A, east=B, west=X, north=Y; plus
left/right-shoulder, select, start, guide and left/right-stick. Axes are
left/right-x/y (-32768..32767), left/right-trigger (0..255), and dpad-x/y (-1..1).
Negative d-pad Y is up, positive is down; neutral is zero throughout.
Force feedback is unavailable. Keyboard remapping is a separate test condition.
For relative mouse, side buttons and true multitouch testing through the native
portal, use [pointer and touch QA](pointer-touch-qa.md). Those events have a
different transport, permission and qualification boundary from this controller.

Inspect a fresh screenshot from the selected backend and save its JSON receipt.
Each action consumes that observation within this bridge. Capture and inspect
again after each action, a restart or a switch to keyboard/pointer input:

```sh
basaltw desktop --native screenshot --compact --json > /private/capture.json
# Open the PNG named by the receipt and inspect focus, scene and intended action.
basaltw desktop --native gamepad press --observation /private/capture.json --button south --hold-ms 120
# Take and inspect another capture before the next action.
basaltw desktop --native gamepad press --observation /private/next-capture.json --axis dpad-y=1 --hold-ms 120
basaltw desktop --native gamepad stop --json
```

Actions accept simultaneous buttons/axes and a 0–5000 ms hold. All owned buttons
and axes return to neutral afterward. Freshness, supervisor generation, geometry
and control revision must match. Disabled/override, browser-reserved control,
changed session, lost supervisor, client disconnect, lifetime expiry, SIGINT or
SIGTERM end the bridge and destroy its device. SIGKILL relies on kernel descriptor
closure and cannot write a final release receipt. Guards poll during holds
(20 ms), while idle, and against the supervisor (200 ms, request deadline 250 ms);
they do not provide emergency compositor isolation. Reconcile changed/lost
supervisors before starting again. See the [kernel uinput lifecycle](https://docs.kernel.org/input/uinput.html).

Private JSON receipts retain backend, requested events, dispatch/release times,
observation provenance and delivery/neutralization. The desktop screenshot
recorder does **not** record these pad events: link both inventories in the
handoff. `completed` proves injection/release, not a game's response. An uncertain
result requires inspection and reconciliation; never replay it automatically.
Pre-dispatch invalid/stale captures return `delivery: not-sent`, `rejected: true`
and a retained rejection receipt, leaving the neutral device connected for a
fresh-capture recovery. Device/persistence failures end the bridge. Stop/status
need no screenshot; the watchdog also closes the device after blocked control.
When no socket exists, status returns `running: false` and stop is idempotent;
unsafe sockets and failed connections still report errors. Idle lifetime expiry
is successful closeout with reason `lifetime-expired`. Expiry during a held
action retains its uncertain delivery/error and release evidence. `closed.json`
records the close reason and successful neutralization or a cleanup error.

## Qualification scope

CachyOS KDE Wayland on 2026-11-16, Godot 4.7.2: the source adapter recognized
face/shoulder/menu/stick buttons, d-pad diagonals, both sticks and triggers.
Neutral, client disconnect, lifetime expiry, SIGKILL device removal and normal
stop were observed. The first simultaneous face-button test missed an X/Y swap.
The corrected mapping uses west=307, north=308, following the
[xpad driver's historical aliases](https://github.com/torvalds/linux/blob/master/drivers/input/joystick/xpad.c).
Individual retests observed south=Godot 0, east=1, west=2, north=3, each with
release. Stale capture rejection produced no inspector event, and a fresh
capture recovered on the same device. Original evidence and corrections remain
private. Signed-stick center may be approximately 0.000015 in Godot.

The public CLI at `e8a184a4` passed a CachyOS consuming-inspector check on
2026-11-17: doctor, supervised serve/status, individual A/B/X/Y, distinct stick/
trigger values and d-pad diagonal, neutral, stale rejection without inspector
events, fresh-capture recovery and owned-device removal on stop. Both tasks
exited zero; the finalized recorder verified eight of eight retained captures.
Event logs and receipts are private. This does not measure held-axis latency,
repeat behavior or physical-controller performance.

The refreshed `d82c81ea` CLI/helper passed another contained Godot 4.7.2 check
on 2026-11-17: individual A/B/X/Y, both sticks, triggers, d-pad diagonal and
neutral. Its supervised 60-second idle expiry returned zero, retained
`reason: lifetime-expired` and `neutralized: true`, and removed the controller
from the consumer. Absent status and idempotent stop also passed. The inspector
closed normally; this adds live coverage for the closeout change, without
qualifying expiry during a held action or the remaining platform boundaries.

Debian supervisor routing has offline contract coverage; live device/game
recognition needs its own host qualification. Live human
takeover, supervisor loss, physical controllers, Steam Input, browser consumers
and other platforms remain separate checks. Windows has no adapter here: it
requires a separately selected virtual-controller driver and lifecycle testing.
Do not install a driver or claim Windows parity from Linux results.

Before gameplay, qualify each face button **individually** against the consumer's
semantic index; simultaneous face presses cannot distinguish X from Y. Check
release, d-pad, sticks, triggers, neutral, interruption and owned-device removal.
Then verify target-game menu navigation and visible 3D actions under the
[game-testing procedure](game-testing.md). Preserve platform and failure-path gaps.

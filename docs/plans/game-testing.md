# Game-testing capability follow-up

Use the [game-testing procedure](../workstations/game-testing.md) for available
capture/input, controller qualification, observer packets, retesting and cleanup.
This plan separates implementation needs from game-owned acceptance. Keep filled
briefs, actual inventories and raw evidence private; no particular game or
external test repository is required.

## Current boundaries

| Capability | State and next acceptance |
| --- | --- |
| Native pointer/keyboard targeting | Coordinate aid exists for resized full/cropped images and drag endpoints; each new gameplay controller still qualifies scene recognition and actual action effects. |
| Retained capture integrity | PNG file hashes and read-only evidence verification exist; file equality requires a separate review of what the image actually supports. Legacy records without file hashes remain unverified. |
| Long sessions | Journals are bounded at 500 events and 50 tasks. Use linked recording segments at safe checkpoints; any larger or streaming journal needs bounded storage, recovery and truncation acceptance before changing this contract. |
| Specialist assistance | Immutable-packet and deadline procedures are documented. A concurrent controller/observer trial must demonstrate shared artifact access, stale-advice rejection, exclusive live control and measured overhead before claiming a reliable team condition. |
| Model/resource comparisons | Actual model/effort, assistance, comparable settings, clocks and outcomes remain project-owned. Unknown usage/latency stays unknown; no automated model ranking is provided. |
| Virtual gamepad | No native gamepad action or qualified adapter exists. Implement and qualify the bounded route below before gameplay use. |

## Bounded Linux virtual-pad adapter

Keep a prospective adapter in Basaltwater's desktop boundary, under the same
persistent human control policy and sole-controller discipline as native input.
Do not introduce a free-running input script, global remapping or additional
privileged daemon as a shortcut. Discover prerequisites read-only; any required
device/driver setup must have its own explicit selection and lifecycle.

The implementation must define an owned, identifiable device, explicit button,
d-pad, stick and trigger mappings, ranges, neutral values and bounded durations.
It must reject new actions while disabled or under human takeover. Completion,
error, timeout, disconnect, stop, permission change and worker death must release
buttons, center axes and destroy only the owned device as appropriate. A
watchdog independent of the requesting worker must cover worker death. Record
requested/completed event and neutralization timestamps privately without
inferring a game's response from device-event delivery.

Qualification proceeds in bounded stages:

1. A contained inspector verifies device identity, only the intended device,
   mapping/ranges, single buttons, d-pad, both sticks, triggers and neutral state.
2. Mocked failure tests and live inspector checks verify control-state rejection,
   cancellation, watchdog, neutralization and owned-device cleanup. Check drift,
   diagonals and repeat behavior; state which failure paths remain untested.
3. A game that actually exposes controller support verifies safe-menu navigation,
   confirm/cancel, prompt changes, then one short gameplay unit. Record native
   versus remapped input and mixed-input behavior while preserving settings.
4. Where safe and supported, test reconnect/hotplug and recovery. Retain event
   records and visual evidence. Mark features such as rumble untested when absent.

Promote support only for the platform/device/mapping/game combinations exercised.
Linux inspector success does not qualify another OS, browser gamepad behavior,
or an arbitrary game. This plan starts no device, installs no driver and schedules
no run; the adapter remains unimplemented.

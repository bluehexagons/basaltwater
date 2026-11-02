# Windows test environment evidence

`basaltw desktop environment --json` reads host graphics and display configuration
without screenshots, input, window enumeration, project execution or preference
changes. It uses an isolated Windows PowerShell worker with an eight-second
execution deadline and bounded response size. It can be used independently of
the current desktop control mode and does not read/write that control state or
refresh observed window/element references. Temporary worker transport files
are private and removed afterward.

```powershell
basaltw desktop environment --json
```

The command returns `ok` plus `result`, whose `State` is `available`, `partial`
or `unavailable`. `available` means both inventory queries completed, including
possibly empty inventories. `partial` retains a successful section alongside
errors from the other; missing sections are null. Exit 0 means available/partial
inventory, and exit 1 means the worker/report is unavailable or the request is
invalid. A partial report is not a full qualification pass. Worker timeout is
reported as unavailable, never as a guessed hardware configuration.

## Collected fields

The report includes schema version, UTC capture boundaries, monotonic probe
duration, Windows version, section errors and `CapturePerformed: false`.
Session evidence automatically retains this same report in `HostEnvironment`
inside `session-evidence.json`, with graphics/display aliases at the top level.
Collection occurs during session preparation, before the application launches.
Unavailable metadata does not prevent dispatch; dry runs perform no probe.
Old evidence files remain unchanged and may contain no hardware data.

| Section | Recorded values |
| --- | --- |
| `Graphics` | Controller name, driver version, vendor and text-truncation flag |
| `Displays` | Device name, physical-pixel desktop rectangle, primary flag, integer mode refresh rate and API scale percentage |
| Explicitly uncollected | Per-window DPI, HDR state, the application's selected renderer GPU and VRR/scanout timing |

The graphics query uses [Win32_VideoController](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-videocontroller)
and retains selected properties only. This is an inventory of controllers, not
proof of which adapter an application uses; Microsoft also notes inaccurate
values for hardware incompatible with WDDM. No hardware serials, PnP IDs,
hostnames, account paths or process environment variables are requested.
The inventory is limited to 32 controllers; strings are bounded to 256/128
characters, with `Truncated` marking any loss. Missing driver text can be empty.

Display queries use `EnumDisplayMonitors`, `GetMonitorInfo` and
[EnumDisplaySettings](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaysettingsw)
under a per-monitor DPI-aware thread context. They read current desktop mode,
not registry defaults. Refresh values 0/1 denote a driver default and are
reported as null; other values require the display-frequency field to be valid.
The integer API value does not establish fractional refresh, actual frame rate,
presentation latency or VRR behavior. At most 32 monitors are accepted.

`ScalePercent` comes from [GetScaleFactorForMonitor](https://learn.microsoft.com/en-us/windows/win32/api/shellscalingapi/nf-shellscalingapi-getscalefactorformonitor).
The API can supply a fallback even on failure; Basaltwater reports null instead.
This value is not converted into a per-window DPI measurement. HDR/DPI and
renderer-specific details remain explicit unknowns; use the project's own
diagnostics and [qualification procedure](windows-qualification.md) for them.
The display section requires an interactive account session; graphics inventory
can still succeed independently when display discovery fails.

## Evidence handling

Keep full reports private alongside the test's source/runtime receipts. Capture
them again after an intentional display/driver change; one snapshot is not an
event recorder or a pinned machine configuration. Review controller/monitor
inventory details before sharing, even though the report omits account and
device serial identifiers. This command neither qualifies GPU rendering nor
adds Windows Graphics Capture, pacing, crash-dump or audio recording support.

Initial native Windows qualification covered the public CLI report and automatic
collection in a disposable interactive Scheduled Task session, with profile
cleanup and retained evidence. The full native suite and PowerShell 5.1/7
contracts passed. Contracts cover section failures, bounds, unknown driver
defaults and unavailable probes without hardware calls. Multiple-monitor,
locked-session and physical display-change scenarios remain separate acceptance
work; no HDR/per-window DPI measurement is claimed.

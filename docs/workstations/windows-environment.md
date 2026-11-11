# Windows test environment evidence

`basaltw desktop environment --json` reads host graphics, display and power configuration
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
or `unavailable`. `available` means all three sections completed, including
possibly empty graphics/display inventories. `partial` retains successful data
alongside section errors; missing values are null. Exit 0 means available/partial
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
| `Power` | Active power-plan GUID, AC online status, battery presence/percentage, battery-saver state and independent query errors |
| Explicitly uncollected | Per-window DPI, HDR state, the application's selected renderer GPU, VRR/scanout timing, power-mode overlay, CPU/GPU clocks and temperatures |

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

Power queries use [PowerGetActiveScheme](https://learn.microsoft.com/en-us/windows/win32/api/powersetting/nf-powersetting-powergetactivescheme)
and [GetSystemPowerStatus](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getsystempowerstatus).
The allocated scheme GUID is released after reading. Each query can succeed
independently; `Power.State` distinguishes `available`, `partial` and `unavailable`.
Unknown AC/battery values stay null; an absent battery has no charge percentage.
Only the GUID is retained, without user-defined plan names or preference changes.
The active plan does not identify the effective power-mode overlay, processor
energy preferences, throttling or achieved frequency. A VM's report describes
the guest configuration, not its hypervisor's power policy or host contention.
Compare like-for-like power conditions and collect effective policy and workload
timing in the project's own benchmark when needed.

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
work; no HDR/per-window DPI measurement is claimed. The later power-evidence
extension has mocked PowerShell 7 contracts and C# compilation/conversion
coverage on Debian. Its real Windows APIs, Windows PowerShell 5.1 and retained
test-session evidence still need native qualification; the earlier graphics/display
qualification does not establish those new cases.

For that follow-up, run `windows/tests/Run-EnvironmentTests.ps1` in native
Windows PowerShell 5.1 and PowerShell 7, then inspect the public
`basaltw desktop environment --json` result and a disposable session's retained
`HostEnvironment.Power`. Check the observed plan against the account's current
configuration, and keep missing battery/status data unknown. No plan change or
shared-desktop interruption is needed for these read-only checks.

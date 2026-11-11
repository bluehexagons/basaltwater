# Window operations and short sequences

`windows` returns PID/class when available, active window, and an `identity`.
Use the current identity and generation to target an application:

```bash
basaltw desktop window focus --window WINDOW --identity IDENTITY --generation GENERATION
basaltw desktop window resize --window WINDOW --identity IDENTITY --generation GENERATION --width 800 --height 600
basaltw desktop wait --window WINDOW --generation GENERATION --condition active
```

Other operations are `move --x X --y Y`, `maximize`, `minimize`, `restore`, and
`close`. Title changes invalidate the identity; list again. Desktop and panel
windows are excluded from these mutations. This fingerprint reduces stale
targeting but cannot guarantee an X window ID has never been reused.
Operations report requests, not completion: verify their result. `close` asks
the window manager to close normally; an unsaved-work dialog can cancel it.
Use `wait --condition absent` afterward, and inspect dialogs on timeout.
Wait selectors combine window ID, literal title substring, and PID with AND.
Waits remain usable during human override or disabled control.

For a brief, already-observed interaction, `desktop sequence /absolute/steps.json
--generation GENERATION` accepts 1–20 JSON action objects (`input`, `window`,
`screenshot`, `windows`), using the same fields as the underlying operations.
Omit generation and control metadata inside steps. The CLI uses one observed
control revision; a human intervention invalidates the remaining actions.
Input requires current full-desktop geometry, window actions require identity,
and screenshots can omit `output` to generate unique private PNG paths. Explicit
paths must be new PNGs under existing writable parents; all are checked before
input. Errors retain the completed prefix and failed action; never replay input
to repair a later observation. Screenshot-only `after_ms` delays are 0–5000 ms,
total at most 5000 ms within the 20-second sequence budget. The result retains
actual timing. Use `--save-output` for a private result JSON and short preview.
See the [one-input/two-capture recipe](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/short-observation-sequences.md).
Keep slow application work outside sequences.

Run `desktop doctor` for session, dependency and XRDP service diagnostics. It
does not start or restart anything and does not capture user content. A healthy
report does not qualify RDP reconnect, clipboard, or application responsiveness.

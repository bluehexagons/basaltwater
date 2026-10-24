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
and screenshots require an explicit new output path. Errors report the completed
prefix; there is no rollback or automatic retry. Keep waits and slow application
work outside sequences.

Run `desktop doctor` for session, dependency and XRDP service diagnostics. It
does not start or restart anything and does not capture user content. A healthy
report does not qualify RDP reconnect, clipboard, or application responsiveness.

# Native Windows Sunshine streaming

Sunshine can provide the same Moonlight desktop-streaming role as Basaltwater's
CachyOS setup. Windows uses the vendor's `SunshineService` at boot rather than
a systemd user service at graphical login. It does not need WSL or a second
scheduled copy of `sunshine.exe`.

From a reviewed Basaltwater checkout, preview without installing packages or
changing services:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Setup-Sunshine.ps1 -Plan
```

To apply, open an administrator PowerShell on x86-64 Windows 11, return to the
checkout, and omit `-Plan`. The command installs or updates the exact
`LizardByte.Sunshine` WinGet package, enables automatic startup for its service,
starts it and waits up to 30 seconds for the running state. An already pending
start uses the same bounded wait; stopping or paused services require manual
resolution before retrying. A missing service or failed start stops setup.
The vendor installer owns its service files and
firewall rules. Basaltwater does not replace existing settings, credentials,
pairings, encoder choices or application lists, or configure router forwarding.
Review installer feature/driver choices. Switching an existing EXE installation
to MSI may require a manual uninstall first; follow the vendor's
[Windows installation instructions](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2getting__started.html).

This optional machine-wide installation is separate from `agent_windows` and
its saved selections. Rerun `Setup-Sunshine.ps1` explicitly for updates; ordinary
`basaltw refresh` does not select or update Sunshine.

Inspect service readiness without elevation or configuration changes:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Setup-Sunshine.ps1 -Status
```

Status returns JSON and exits nonzero if the service is absent, stopped or not
automatic. A running service establishes process/startup readiness only. Open
`https://localhost:47990` locally, create credentials if this is a first install,
and enter the Moonlight PIN through Sunshine's administration UI. Keep the web
interface private; inspect Windows firewall profiles/rules for the intended
network before connecting. Basaltwater never collects the password or PIN.

Verify a stream from a Moonlight client: select Desktop, check the intended
monitor and GPU, change resolution/scaling, play audio, and test keyboard,
mouse and any gamepad. Windows capture needs an active display on the capturing
GPU; a headless machine may need a suitable display or dummy plug. RDP sessions,
locked desktops, UAC prompts, suspend/resume and display changes require separate
qualification. Check driver and license requirements before selecting optional
virtual input drivers; Basaltwater does not install or license them separately.
See Sunshine's [setup and input guidance](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2getting__started.html).

To pause hosting, stop `SunshineService` in Services as an administrator. To keep
it stopped across boots, change its startup type to Manual. Applying this setup
again enables and starts it. This machine-wide service can outlive account
logoff, unlike CachyOS's graphical-session service. Successful startup does not
establish logged-out or headless streaming support.

The offline suite mocks package/service operations, including unelevated setup,
missing services, installer failure, stopped/manual startup and pending-start
timeouts. Fresh package installation, firewall behavior, boot recovery and actual Moonlight streaming
remain live qualification tasks; they were not exercised on the reference PC.

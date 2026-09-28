# Ubuntu WSL support

The implementation is in [the operator guide](../WINDOWS_WSL.md). Live Windows
qualification is still required before calling the release production ready.

## Release scope

Support fully updated x86-64 Windows 11 desktop editions, including unactivated
test installations, with the latest WSL 2 and an official stable Ubuntu image.
The owner logs in with administrator rights for bootstrap; builds and agents
run without elevation. Reboots, passwords, and provider logins can be
interactive. Windows Server, ARM, pre-login operation, GUI automation, and VM
provisioning remain out of scope.

The installer uses WinGet for selected native packages and the `server_wsl`
profile for Ubuntu tools. T3 Code is an optional native desktop app; no managed
T3 server runs in WSL. A login-owned task can keep the selected distribution
running. Native PowerShell jobs use the logged-in user's checkout, an exact
commit revision, bounded output, and authenticated HTTPS artifact upload with
receiver-confirmed SHA-256. Ubuntu builds use Ubuntu-native workspaces.

## Qualification required on Windows

1. On a fresh updated Windows 11 machine, test the pasted PowerShell and CMD
   commands, WSL/WinGet bootstrap, Ubuntu user creation, reboot/resume, and an
   idempotent rerun. Repeat with absent WinGet registration, a path with spaces,
   and an unactivated or evaluation image.
2. Run a native PowerShell job that produces and uploads an artifact to a
   digest-confirming HTTPS receiver. Also test command failure, missing
   artifact, timeout, oversized output, failed upload, and checksum mismatch.
3. Run an Ubuntu build, one selected agent, and the T3 Windows desktop using its
   Ubuntu connection. Verify no extra T3 service appears in WSL.
4. Exercise worker start/stop/status, refresh, logout/reboot recovery, and
   preservation of existing WSL distributions and credentials. Record exact
   Windows, WSL, Ubuntu, WinGet, and T3 versions with the results.

Automated Debian-side tests cover the profile, capability gates, preparation,
and source staging. The [manual Windows CI workflow](../WINDOWS_WSL.md#run-manual-windows-ci)
adds hosted native job tests and optional read-only WSL checks on an isolated
Windows 11 self-hosted runner. Fresh bootstrap, UAC, Store registration, a real
artifact receiver, and T3 desktop integration still require hands-on checks.

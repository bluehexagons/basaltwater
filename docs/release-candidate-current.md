# Current preview candidate evidence

[Documentation index](release-qualification.md)

### Candidate results

#### `v2.0.0-rc.6`

Qualification on 2026-11-19 UTC used commit
`da80bbb88e83f7c6d209512f559a4c93e939ef2a` (package `2.0.0rc6`). Release CI
passed on Python 3.11 and 3.15 in Debian Trixie and built and smoke-tested the
wheel on Python 3.13. The branch test workflow also passed.
[Release workflow](https://github.com/bluehexagons/basaltwater/actions/runs/44431091057)

- The published wheel matched the tested CI artifact byte for byte and passed
  a second isolated installation and launcher smoke test. Its SHA-256 is
  `944a132c66541e176ff67716e3c5cb99c2a17cefebd2360303604d3a86f16481`.
  The source archive matched the tagged installer, package metadata, VM
  provisioner and target setup code.
  [GitHub prerelease](https://github.com/bluehexagons/basaltwater/releases/tag/v2.0.0-rc.6)
- A Debian 13 VM installed the exact tag in a fresh controller account and
  upgraded a second controller account from RC 5, preserving private file
  bytes, ownership and mode. `stable` still selects historical `v0.2.0` and
  refuses to activate it. `dev` followed `main`, and switching back selected
  the exact RC 6 tag.
- RC 6 defers the cloud-init upload until Proxmox capacity checks and VMID
  allocation succeed. On the test server, the exact RC rejected a request
  whose floor exceeded the host target. No VM or cloud-init snippet was
  created, and the provider inventory matched the pre-request snapshot.
- A fresh Debian 13 `agent_vm` completed setup with the exact candidate.
  The finished VM retained no cloud-init snippet. The tagged source,
  managed account, SSH host identity, package state, installed skills, and
  working Codex and GitHub CLI launchers passed before and after reboot.
  Provider authorization was disabled for this fixture.
- A SIGKILL during the agent VM's APT upgrade retained the setup operation
  marker and blocked an ordinary rerun. The saved setup, private file,
  SSH host identity and package state matched the baseline. Explicit
  recovery retained the original operation receipt and a new setup passed.
- A fresh unprivileged Debian 13 `server_web` LXC verified tagged source,
  saved setup, SSH, package state and Nginx before and after reboot. An
  independent SSH administrator completed a user rename and resumed another
  after a SIGKILL at the login and home cutover. Numeric IDs, private data,
  saved setup, deployment cache, SSH and the timer survived the recovery
  and another container reboot.
- The Playwright suite passed all 100 Chromium and Firefox cases with the
  required Guacamole fixture. Native Firefox ESR passed certificate
  enrollment through the Certificate Manager and the panel, pairing and
  synthetic approval checks. On a disposable Debian 13 `server_web` VM,
  Firefox ESR rejected the installed gateway before importing its verified
  CA and accepted the gateway after profile-only enrollment.
- The same web VM verified a real protected-file event with all 31 managed
  audit rules. Missing tools, stopped auditd and a missing rule preserved
  the monitoring cursors and reported failures. Audit, Fail2ban and SSH
  retention gaps persisted after a later healthy scan. The certificate CLI
  and panel agreed for all five trust cases, including public trust, a
  missing CA and hostname mismatch.
- A clean Debian 13 `server_lite` VM mounted Proxmox-backed data and backup
  disks with a writethrough LVM cache. Samba accepted an authenticated write
  and read. An unavailable backup mount blocked sync, and parity failure
  preserved the healthy mirror until restore recovered the bytes and file
  metadata. A failing data mount blocked Samba, Gogs, Syncthing and HomeBox.
  All four recovered when the mount was restored.
- A SIGKILL during disk addition interrupted setup just after it mounted
  the extra disk. A normal rerun refused to proceed and kept the original
  operation marker. The operator verified the original state, stopped the
  partial mount and archived the failed operation before retrying. A fresh
  setup reused the added volume, its filesystem UUID and a file written
  before interruption. The original Proxmox bindings, mounts, LVM cache
  identity and data stayed intact, and the mounted services and their
  identities survived reboot.
- The mounted HomeBox deployment passed browser login, inventory, attachment
  and WebSocket checks. Its CLI archive recovered a deliberately damaged
  attachment with the original bytes, ownership and mode. Health passed
  after restore. A burst of fixture connections hit the guest SSH rate
  limit. The first attempt left the damaged attachment and backup available.
  A new restore succeeded once SSH admitted connections again.

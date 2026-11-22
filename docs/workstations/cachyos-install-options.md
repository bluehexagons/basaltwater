# CachyOS install options

[Topic index](cachyos-install.md)

## Pick the options you need

Append options to `--local-setup agent_cachyos` in the installer command, or to
`basaltw setup agent_cachyos localhost` after the launcher is installed. Select
providers explicitly with `--codex`, `--claude`, or `--opencode`.

| Need | Options |
| --- | --- |
| Coding providers | `--codex`, `--claude`, `--opencode`; repeatable. Select at least one; no provider is selected by default. GitHub CLI is included unless omitted with `--no-agent-tool gh`. |
| Node, Python, Go, or Git LFS | `--node`, `--python`, `--go`, `--git-lfs` |
| Native game and Animator development | `--game-dev`, `--node-versions` ([Antistatic workflow](cachyos-game-development.md)) |
| Godot, media, or graphics diagnostics | `--godot`, `--av-tools`, `--gl-tools` (drivers are never installed) |
| Gaming and streaming | `--gaming`, `--sunshine`, `--moonlight` |
| Creative applications | `--obs`, `--blender`, `--kdenlive`, `--krita`, `--gimp`, `--inkscape`, `--scribus`, `--shotcut` |
| Audio, CAD, or electronics | `--audacity`, `--lmms`, `--ardour`, `--freecad`, `--kicad` |
| Remote desktop or diagnostics | `--remmina`, `--sysadmin-tools` |
| Materials, removable media, publishing | `--material-maker`, `--etcher`, `--butler`, `--steamcmd` ([packages and post-install checks](cachyos-software.md)) |
| Repository workspace | `--repo HTTPS_URL` (repeatable), `--agent-workspace /absolute/path` (default `~/repos`) |
| T3 Code desktop | `--t3code-desktop`: install or update `t3code-bin` through your AUR helper and enable KDE login startup; mutually exclusive with `--web-interface` |
| T3 Code web service | `--web-interface t3code`, then follow [T3 Connect](cachyos-t3-code.md#t3-connect). Optionally add `--web-interface-host PRIVATE_IPV4` and `--web-interface-port PORT` for direct LAN pairing |
| Machine declaration | `--machine hardware` (the bare-metal check still runs) |
| Setup plan only | `--dry-run` (use installer `--plan` before `--local-setup` for a full preview) |
| Restrict inbound workstation access | `--lan-access` and/or `--access-source PRIVATE_IP_OR_CIDR`; `--no-lan-access --no-access-source` closes managed access |
| Remote shell access | SSH follows detected service state on local setup; explicitly use `--ssh` or `--no-ssh` to override; remote setup defaults to SSH; see [SSH access](cachyos-install-ssh.md#ssh-access) |
| Setup-result webhooks | `--notify webhook URL` (repeatable), `--notification-level LEVEL`, `--notification-ca-file PATH` |

Examples:

```fish
# Game and media workstation with an additional coding agent
basaltw setup agent_cachyos localhost --codex \
  --opencode --node --python --git-lfs --godot \
  --av-tools --gl-tools

# Gaming and game streaming on the trusted LAN
basaltw setup agent_cachyos localhost --gaming --sunshine --moonlight --lan-access --codex

# Preview a plan without installing packages or changing files
basaltw setup agent_cachyos localhost --node --python --dry-run --codex
```

Application options install native packages from the configured CachyOS
repositories, except the explicit AUR options `--t3code-desktop`,
`--material-maker`, `--butler`, and `--steamcmd`. No Flatpak packages or graphics
drivers are installed. Most application selections only install packages;
Sunshine's startup and conditional encoder configuration are described below. See the
[creative and publishing software guide](cachyos-software.md) for all issue #106
software, source policies, and post-install checks.
`--gaming` selects CachyOS's gaming meta-packages;
`--sunshine` installs the native host plus VA-API diagnostics, enables its user
service at KDE login, and starts it during setup. Quit Sunshine normally from
its tray menu when you want it stopped for the session. `--moonlight` installs
the client. Add `--lan-access` or explicit `--access-source` values for restricted
inbound access, then configure and pair Sunshine in its local web UI.
Sunshine supports Intel, AMD, and NVIDIA GPUs; installing its package does not
verify capture or hardware encoding. See the
[Sunshine checks](cachyos-streaming.md#sunshine-service-and-encoder-checks) before pairing.

The T3 web service selects Node automatically; both T3 modes require Codex,
Claude, or OpenCode. Python
is also installed when needed for native Node module builds. Existing
version-manager runtimes are retained when their commands are on PATH.
Readiness runs version checks for all selected language commands: Node, npm,
and pnpm for `--node`, and Python and uv for `--python`. A missing or broken
companion tool makes setup incomplete even if the main runtime works.
On CachyOS, `--av-tools` includes FFmpeg, ImageMagick, and ExifTool;
`--gl-tools` includes Mesa/Vulkan diagnostics and apitrace.

## Optional webhook notifications

Add webhook targets to your initial setup selection to receive its completion
or failure result. For example, after installing the launcher:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs --codex \
  --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https
```

CachyOS supports only `webhook` targets; `mailbox` is rejected before setup
actions. Repeat `--notify webhook URL` for multiple receivers. Delivery uses
Basaltwater's [schema-version-2 JSON payload](../services/notifications.md#webhook-api),
bounded retries, and optional HTTPS fragment-carried bearer token. The payload
identifies this workstation by its hostname. A Basaltwater web panel's full
sender link can be used as the URL; quote it in fish and treat it as a credential.

`normal` and `verbose` send successful and failed setup results. `warning` and
`error` send failures; `off` suppresses delivery while retaining targets. Dry
runs send nothing. Invalid options are rejected locally without sending an
event. Validated setup preflight or step failures send the failing phase and
exception type; inspect local output for details. Delivery failure prints a
warning and preserves the setup outcome.

HTTPS verifies receiver certificates and hostnames by default. For a private
CA, add trust on the workstation or supply a sender-local PEM path with
`--notification-ca-file`. Notifications need outbound access
only; no listener, mail transport, or notification timer is installed. The
CachyOS integration sends setup results, including those from refresh; it does
not install the Debian maintenance/security notification jobs or monitor Sunshine.

Successful setup saves targets and policy in the private local selection and
receipt for later refresh. Displayed refresh commands redact webhook URLs.
Setup also writes a private `last-notification.json` beside the selection,
recording whether delivery succeeded, failed, or was suppressed by policy.
It contains no webhook URLs or tokens. `basaltw local cachyos-doctor` reads this
evidence without sending a test event. Receiver acceptance does not verify
downstream processing; older setups without this record remain unverified.
For an existing setup, see [notification maintenance](cachyos-refresh.md#webhook-notifications).

## Codex-only agent setup

No coding provider is installed by default. Select one or more with
`--codex --claude --opencode`. GitHub CLI (`gh`) remains selected for
GitHub operations; `--no-agent-tool gh` omits it.
For Codex with T3 desktop, use:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --codex
```

Add `--no-agent-tool gh` if you also want to omit GitHub CLI. Codex and T3
desktop do not require `--node`. Development projects may still need it.

Missing Codex installations use the [official standalone installer](https://learn.chatgpt.com/docs/codex/cli).
Reruns update recognized standalone installations at `~/.local/bin/codex`.
System packages, npm links, and version-manager paths remain with their existing
manager; setup reports the executable it retains. First installs and updates
use the same desktop-account environment, ignoring inherited installer target,
daemon-selection, and release-pin variables.

Existing `~/.codex/config.toml`, models, permissions, MCP servers, plugins, and
credentials are preserved. Setup does not create a replacement Codex config or
copy credentials from another account. A custom `CODEX_HOME` is rejected before
setup changes anything because this profile manages the standard `~/.codex`
location. An explicit choice of another provider with `--no-agent-tool codex`
leaves that custom Codex installation unmanaged.

Readiness runs `codex login status` without printing its output. A failed check
produces an actionable warning without prompting or replacing credentials.
Inspect `codex login status` locally and use `codex login` if needed. Codex
supports file and OS credential stores and refreshes ChatGPT tokens during
normal use; this profile installs no separate authentication refresher.
See [Codex authentication](https://learn.chatgpt.com/docs/auth).

Verify a real Codex task after login; local login status does not test provider
access or T3's GUI environment. Standalone update and rollback behavior is
documented in [maintenance](cachyos-refresh.md#codex-updates).

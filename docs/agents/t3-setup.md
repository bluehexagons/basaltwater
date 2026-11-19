# Install and pair the managed server

[Documentation index](t3-code.md)

This guide covers T3 Code's server-side web interface on managed Debian
profiles. The retired desktop AppImage installation is not managed.

For an existing CachyOS KDE workstation, use the limited local profile described
in the [CachyOS guide](../workstations/cachyos.md). Choose the native `t3code-bin` package with
`--t3code-desktop` using the [desktop installer example](../workstations/cachyos-install.md#install-with-t3-code-desktop),
or the [managed web service](../workstations/cachyos.md#t3-code-web-service-host-locally-or-on-a-trusted-lan)
for loopback access, direct private-LAN pairing, and optional T3 Connect.
The two setup flags are mutually exclusive and can be switched on later runs;
each environment retains its own data.
The VM/server options below do not apply to `agent_cachyos`.

Use either the focused profile:

```bash
basaltw setup server_dev vm.example agent \
  --t3code-ready --codex \
  --device-pairing-auth-file /run/secrets/t3-pairing.htpasswd \
  --access-source 192.168.1.0/24
```

or select the interface and providers explicitly:

```bash
basaltw setup server_dev vm.example agent \
  --agent-tool gh \
  --codex \
  --web-interface t3code \
  --device-pairing t3code \
  --device-pairing-auth-file /run/secrets/t3-pairing.htpasswd \
  --git-access read-write \
  --access-source 192.168.1.0/24
```

Prepare the first [pairing portal credential](../services/device-pairing.md#configure-it)
before setup, or use `--interactive` to enter it privately. Reruns can reuse
the credential installed on the target.

The usual client is the separately installed T3 Code desktop or mobile app.
Generate a one-time URL from the control system:

```bash
basaltw agent web pair vm.example agent
```

T3 Code sessions can expose collaborative preview tools. Server preview
tools automate tabs marked `runtime: "server"`. Listed tabs with desktop or
missing runtime belong to the user's local app. Report that those tabs
are inaccessible to server automation and open a server tab without passing
their IDs. Desktop automation requires tools that explicitly support it.
In those sessions, use preview first: status, then open if no capable tab is
attached. Fallback is allowed for absent preview tools, an explicit user request
for another browser, or an explicit unsupported/unavailable response from open.
Follow the active session's additional bounded retry and fallback rules for
navigation, timeout, and certificate failures, including two failures on the same
step when specified; quote the raw error when switching.
For independent SSH or terminal sessions, request VM-local Chromium explicitly:

```bash
basaltw setup agent_code_vm vm.example agent --codex \
  --browser-automation playwright
```

Stable 0.1.2 prefers its server browser, which uses the server's routes and
certificate trust and can keep working after a viewing client disconnects.
Desktop-hosted tabs use the desktop's network and trust instead. Use the
[browser workflow](browser-automation.md#collaborative-preview-and-private-networks)
for tab attachment, verified input, recordings, and bounded recovery. If only
client-origin testing fails, continue with healthy VM-local Playwright when
session policy permits fallback, or
non-browser checks. For a desktop-browser `ERR_CERT_AUTHORITY_INVALID`, optional
[client CA enrollment](../services/client-ca-trust.md) can restore private-origin access;
a timeout or unreachable address needs network diagnosis. Never bypass TLS.

For server-hosted preview, use `target: {kind: "environment-port", port: PORT}`
to reach a VM loopback development port directly when the live tool schema
exposes `target`; otherwise open a tab and navigate to the loopback URL.
When a desktop-hosted browser
needs a reachable VM URL, run
`basaltwater-web preview resolve --port PORT --json`. It returns a verified
owned gateway URL for `preview_navigate`, or an exact forward creation command
with actionable failure fields. See [Internal HTTPS previews](../services/internal-web.md#forward-an-existing-loopback-service).
The browser guide also covers navigation/component attribution, uncertain
input acknowledgments, and [offline-cache freshness](browser-automation.md#offline-cache-freshness).
For T3's own Chrome download, missing libraries/fonts, or AppArmor errors, use
[T3 browser host preparation](browser-automation.md#t3-browser-host-preparation).
The ordinary T3 doctor checks service readiness, not a browser launch.

Agent-enabled T3 setups install T3-only preview guidance, or the combined
Playwright/T3 skill when both capabilities are selected, plus focused T3 Code
and HTTPS-gateway guidance. See
[Managed agent workflow skills](agent-skills.md) for the installation matrix.
Selected Claude Code receives the catalog under `~/.claude/skills`; Codex and
OpenCode share `~/.agents/skills`. Bundled references ship with the entrypoint,
so host-side migration instructions remain available outside this repository.
Successful setup queues selected T3 startup/reload after provider configuration,
skills, saved state and notifications. Debian's completion worker waits until
the setup process exits, then starts/reloads the shared service and checks
stable HTTP readiness. Its upstream updater can also start a newer runtime
during installation; the final reload picks up providers installed later.
Internal non-refresh service checks reuse a healthy running service. CachyOS
stages its web runtime without stopping the terminal's service, then queues
activation/readiness after setup. Its desktop worker gracefully reloads a
running app or starts a stopped app when a graphical session is available;
otherwise KDE login startup remains enabled. Native Windows uses a same-session
scheduled task after successful setup receipts and reports when manual reopening
is needed. See [Windows setup](../workstations/windows.md).

Debian `sudo basaltw refresh` runs in an independent system service, including
when invoked from a local T3 terminal or over SSH from another environment.
The caller's exit 0 confirms dispatch. Inspect the printed private refresh log,
refresh unit result and final T3 completion unit result before assuming success.
CachyOS keeps package and sudo prompts interactive until the completion handoff.
Queue acceptance does not prove app or provider readiness: inspect the printed
unit/status, then verify an authenticated provider turn and terminal. See
[refresh behavior](../reference/command-line.md#refresh-this-machine).

When Codex is selected, root-owned Codex defaults make direct CLI sessions use
auto-reviewed workspace access. They do not restrict explicit user or client
choices, so T3 can retain its upstream full-access default and users can select
other Codex permission modes. `--harden-agent` adds system requirements that
enforce the workspace boundary without an approval escalation path. This is a
Codex policy and does not change another provider CLI's native permission model
or remove T3's collaborative preview. See
[Agentic coding security](agent-security.md).
The workspace skill uses the safe local lifecycle command:

```bash
basaltw agent workspace create ~/repos/PROJECT TASK --json
basaltw agent workspace remove WORKTREE --dry-run --json
```

The removal path rejects dirty, untracked, or unmerged work and cannot remove
the primary checkout. For a shareable diagnostic snapshot that omits log and
credential contents, use `basaltw agent support-bundle`.

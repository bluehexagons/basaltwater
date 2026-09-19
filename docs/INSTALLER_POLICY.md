# Third-party installer policy

Selecting a tool or setup profile that installs these tools accepts the
vendor-managed channel below, including additional payloads fetched by its
installer. These channels are maintained by the vendor and can change without
a Basaltwater release. Sites requiring independently pinned payloads should
preinstall their approved tool versions and omit the corresponding installer
and update options.

| Tool | First-stage source | Accepted channel |
| --- | --- | --- |
| Codex | `https://chatgpt.com/codex/install.sh` | Vendor rolling |
| Claude | `https://claude.ai/install.sh` | Vendor rolling |
| OpenCode | `https://opencode.ai/install` | Vendor rolling |
| uv | `https://astral.sh/uv/install.sh` | Vendor rolling |
| nvm / Node.js | `https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh` | Tagged nvm installer; vendor rolling Node LTS |

The policy applies to Debian and CachyOS agent setup, Python/Node setup, and
explicit agent updates. The shared installer entry point requires
`--accept-vendor-channel`; selecting these setup steps supplies that acceptance.
Claude and OpenCode updates use their native vendor update commands.

First-stage downloads require HTTPS, including redirects, and are limited to
4 MiB and 120 seconds. Shared installer execution has a one-hour deadline;
the Codex updater retains its shorter update deadline. Before execution,
Basaltwater records the source, effective URL when available, selected policy,
observed SHA-256, size, executing UID, and time. A provenance write failure
prevents execution. The digest identifies downloaded bytes; it is **not** an
independently pinned expected digest or verification of transitive payloads.

The latest installer record per tool is private (mode 0600) under
`/var/lib/infra_tools/installer-provenance` for root, or
`~/.local/state/infra-tools/installers` for an ordinary user. Shared installer
runs also record success, failure, or interruption. uv setup and Codex updates
retain the download record; agent update outcomes, channel policy and version
checks live separately in `~/.local/state/infra_tools/agent-tools.json`.
Downloaded scripts are temporary and removed on normal completion or failure.
Native updater provenance records the command and version outcome; it cannot
attest to the vendor updater's internal downloads.

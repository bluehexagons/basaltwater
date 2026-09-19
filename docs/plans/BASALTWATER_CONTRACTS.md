# Basaltwater cutover contracts

The v2.0.0 rename changes the distribution to `basaltwater` and the primary
command to `basaltw`. The implementation remains in `infra_tools.py` and the
existing packages: moving modules would add upgrade risk without a user benefit.
The maintainer is bluehexagons. No repository transfer or package publication is
part of the implementation PR.

## Inventory and decisions

Inventory command: `rg -n 'infra-tools|infra_tools|INFRA_TOOLS'`. Review the
matches by contract, not by spelling; an occurrence can have several consumers.

| Class / producer | Consumers | Decision and migration | Verification / transition end |
| --- | --- | --- | --- |
| Public package metadata (`pyproject.toml`) | pip, build, version inspection, wheel checks | Distribution becomes `basaltwater`; retain all import paths. Uninstall the old distribution **before** installing the new wheel in the same environment. | Isolated wheel upgrade/uninstall smoke; distribution rename is permanent. |
| Executables (bootstrap and package scripts) | Operators, shell completion, existing automation | Primary `basaltw`; also install `infra-tools` pointing to the same implementation. Do not restore `infra_tools`, or add `basalt`, `bw`, or `b6`. | Execute both launchers; retire `infra-tools` no earlier than v3.0, after generated automation and installed agent guidance have migrated. Owner: project maintainer. |
| Direct Python entry (`infra_tools.py`) | Remote setup, recall, channel discovery, imports, scripts | Retain filename, function names and module layout permanently. | CLI, remote setup and recall tests. |
| Installation locations and ownership (`install.sh`, setup staging) | `/opt/infra_tools`, user `share/infra_tools`, custom paths, `.infra_tools/managed-install`, channel state and snapshots | Retain paths, marker bytes and metadata formats. Upgrade in place using existing staged activation/backup recovery. | Installer fresh, rerun, failure and rollback tests; no state move or dual-location precedence. |
| Workspace, credentials, inventory, journals and backups (`lib/`) | Controller and target recovery, shell history, user rename, auth consumers | Retain every persisted path and serialized key, including `infra.json`, `.infra_toolsrc` and `INFRA_TOOLS_*` runtime settings. Explicit paths still win under their existing contracts. | Existing state/recovery tests; help and inspection never migrate state. Permanent retention until a separately versioned migration. |
| Installer environment (`install.sh`) | Install scripts and update automation | Accept `BASALTWATER_REPOSITORY_URL`, `BASALTWATER_CHANNEL`, `BASALTWATER_REF`; new spelling wins over its old spelling. A channel wins over a ref; CLI options win over environment. | Installer precedence and empty-value tests. Old spellings supported through v2.x, removal follows launcher retirement. |
| Managed resource names (`common/`, `security/`, `web/`, `desktop/`, `lib/`) | systemd units, timers, sudoers, sandbox paths, readiness routes, locks, firewall comments, log consumers | Retain names and paths as one namespace. Includes `infra-tools-locks-*`, `infra_tools` ownership labels, HomeBox readiness routes and web-panel API/auth identities. | Existing service/security/locking tests. No duplicate jobs or split locks; no service migration needed for this rename. |
| Agent integrations (`common/agent_skills`, agent setup) | Installed skills, reconciliation, readiness checks, MCP registration, generated instructions | Retain skill IDs and tool registration IDs. Bundled content uses `basaltw`; previously installed content continues via `infra-tools` until setup refreshes it. | Agent skill and readiness tests. Maintainer owns v3 retirement audit. |
| External references (`install.sh`, docs, CI) | GitHub raw/archive URLs, releases, updater remotes, badges | Keep `bluehexagons/infra_tools` until a separate hosting cutover verifies every URL. No guessed redirects. | Existing URLs remain authoritative; no external mutation in this PR. |
| Historical evidence (`docs/plans`, dated audits) | Project history and previous-release instructions | Preserve the original names except explicit status updates. | Review residual matches; historical names have no removal deadline. |

Old and new commands operate on exactly the same files, credentials, services,
and locks. There is no new default state directory to conflict with an old one,
no copy-on-read, and no migration-completion marker. Retaining these contracts
also leaves permissions, ownership, symlinks and user-modified resource files
under their existing handling. Mixed controllers and targets remain supported
to the extent they were before this rename; this does not promise cross-version
compatibility for unrelated configuration changes.

## Release boundary

The rename targets the already declared v2.0.0 release. `dev` users receive it
when this PR merges; pinned and stable users must deliberately select a ref
containing the rename. Refresh bootstrap after a Git-only channel update to
install the new launcher. Until v3.0, the old command and existing integration
identifiers are supported contracts, not unused aliases. Re-evaluate retirement
only after upgrade documentation, generated commands and integrations no longer
need them. Retained persisted identifiers have no automatic retirement date.

Availability checks, operator upgrade/rollback instructions and qualification
results are recorded with the implementation; a successful build does not
reserve a registry name or establish trademark clearance.

## Availability observations (2026-09-19)

- [PyPI metadata](https://pypi.org/pypi/basaltwater/json) returned HTTP 404:
  no published distribution was visible; this does not reserve the name.
- [GitHub repository lookup](https://api.github.com/repos/bluehexagons/basaltwater)
  returned HTTP 404: no public repository was visible at the proposed address.
- [Verisign domain lookup](https://rdap.verisign.com/com/v1/domain/basaltwater.com)
  returned HTTP 200: `basaltwater.com` is registered. Ownership/control has not
  been established, so no product links assume that domain.
- General web searches did not identify an obvious software naming collision.
  Trademark registry clearance remains an owner follow-up before public launch.

## Qualification

`make check` passed: 3,959 tests ran, with two skipped, plus syntax, CLI docs,
package metadata and fresh wheel installation checks. A wheel built from the
pre-rename source at `e966305ed92e47150d725dd5f184d7f1ce327d95` also passed the
optional package upgrade/rollback check. Both distributions use the existing
2.0.0 development version; this check does not simulate a published release.

Focused tests cover both executable wrappers, repeat installation, old source
rollback, installer setting precedence, old/new Git release selection at a
custom path containing spaces, and retained private file content/permissions.
Actual wrapper subprocesses read an old-format workspace without relocating it.
Installer tests use local repositories and mocked system commands. No live
controller/target upgrade or system-service mutation was performed; live VM
qualification and external publication remain release follow-ups.

Active documentation and all bundled skill commands now use `basaltw`, with
skill IDs, paths, historical references and resource ownership strings retained.
The shared web panel uses Basaltwater branding and semantic light/dark tokens.
Editable SVG assets and reproducible documentation/panel specimens are included;
all 15 skills validate, and palette tests enforce text/control contrast.
VM-local Chromium verified light/dark rendering at 375/1280 pixels, no horizontal
overflow or broken images, and keyboard skip-link focus. The sole console error
on the static review server was its absent `/favicon.ico`; the panel introduces
no external image, font or script requests. No-color CLI version output passed.

The follow-up PR review added bootstrap collision checks that preserve an
unrelated `basaltw` file, binary, directory or symlink before touching either
older launcher. It also restored empty `INFRA_TOOLS_CHANNEL` behavior and
tested installer selection for underscore-only older releases. The latest
tag, `v0.2.0`, declares `infra_tools` and lacks channel/upgrade commands;
its migration instructions therefore use the current installer and retain
the source backup. The old-launcher installer test remains a local fixture,
not a live installation of that tag or a qualification of its wheel.

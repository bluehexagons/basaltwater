# Project roadmap

Status: active planning guidance for the v2.0.0 preview and stable release.

Basaltwater should complete the safety and recovery loops around its existing
operations before expanding platform, desktop, or service-specific support.
The [planning index](README.md) owns the portfolio and compact issue
dispositions; GitHub issues own discussion and detailed acceptance.

## Current state

The package version is 2.0.0rc1. No v2.0.0-rc.1 tag or public release has been
published. The [release checklist](../release-qualification.md) defines the
preview gate and the live matrix for stable qualification.

Managed execution has bounded transaction markers, state validation, unit
replacement recovery, Nginx reconciliation, and deployment activation
rollback. The shared mechanism does not reverse package installation,
arbitrary scripts, application data, or database migrations. Host behavior
still requires live qualification.

The security monitor handles bounded event backlogs. Issue #108 remains open
for detecting when audit or journal retention has passed an old cursor.

## Product direction

Basaltwater is a small infrastructure reconciler for Debian and Proxmox.
Operations should validate requested state, apply changes, verify results, and
retain a documented recovery path. Priorities:

1. Failed operations stop and preserve or restore the working state.
2. Manual setup, redeploys, and webhook delivery share declarations.
3. Destructive and connectivity-sensitive actions have a preview and rollback
   boundary.
4. Saved configuration can support fleet audit and drift checks.
5. New providers reuse existing execution and recovery contracts.

Prefer safeguards that reduce operator effort. Defer controls whose recurring
administration is not justified by usage or incidents.

## P0: Transaction and state reliability

The bounded transaction system is implemented. Keep required commands
fail-fast, stage and verify managed files before activation, retain markers
when recovery is incomplete, and reject corrupt or incompatible persistent
state. Full setup records partial progress but does not promise reversal of
packages, user changes, arbitrary scripts, or application data.

Use [transaction recovery](../operations/transaction-recovery.md) when an
operation marker blocks retry. Keep database migration and automatic
cross-phase recovery as separately scoped follow-up work.

## P1: Manifest deployment and secret references

Keep the Basaltwater controller lightweight and independent of Docker or an
external application-management API. Deploy-secret references and manifest
reuse are queued after the transaction foundation. The intended result is one
declaration for manual setup, redeploy, secrets, health checks, routing, and
webhook CI/CD.

## P1: Read-only planning and drift

There is no general desired-versus-observed plan command. A future planning
layer should report proposed changes and unavailable facts using stable
categories and exit codes; applying a plan must verify against the same
observations.

Start with services, managed files, packages, and deployments. Do not promise
a universal package diff until setup steps expose structured state. The
Proxmox operator guide owns current node and guest observations. The
maintenance guides own current agent-host status. Issue #108 tracks missing
source-retention detection in security monitoring.

## P2: Recovery workflows

Backups are incomplete without a tested restore. Prioritize verified restore
and retention for managed assets, application/database backup and restore, a
recovery inventory, and clear destructive confirmations.

Proxmox backup and restore require the shared transaction and recovery
contracts. HomeBox has native backup/restore commands; the release matrix
still requires a disposable-VM restore check. Gogs recovery must include its
database, repositories, attachments, LFS objects, secrets, and backing storage
identity. Storage follow-up proposals are limited to interrupted-manifest
reconciliation, resumable scans, persistent incident notifications, and
reviewed recovery retention; no implementation is scheduled.

## P2: Safe network apply and rollback

Extend the existing verified host/guest address handoff to reviewed Proxmox
firewall artifacts. Validate management sources, save exact artifacts, install
a timed rollback before apply, verify connectivity, and require explicit
confirmation before cancelling rollback. Add other network providers only
after this contract is proven.

## P3: Extensibility and release quality

After reliability work, isolate optional plugin failures, document stable
provider contracts, retain packaging smoke checks, and consider focused lint,
type, and coverage gates. New operating systems need explicit CI and live-test
expectations.

The [Windows plan](windows-support.md) targets current Windows 11 for focused
debugging and project checks, with Antistatic as a required workload, the native
GitHub runner, and a small panel for monitoring, runner rules, and notifications.
Prefer native execution if the reduced features remove the need for WSL with
little compatibility code; owner-login startup is sufficient initially. General
workstation support and VM provisioning are excluded. Windows remains outside
the current v2.0.0 stable-release gate.

Game publishing is available through the VM-local panel and CLI, but provider
account behavior and rendering remain unqualified. Steam default release stays
manual. Automatic public posts, rich assets, and additional destinations
remain qualification-gated in the [communications plan](game-release-communications.md).

Godot bundle additions need compatibility, architecture, installation,
headless verification, and update contracts before becoming supported CLI
choices.

## Deferred

Until reliability and recovery work is substantially complete, defer broad
non-Debian support, Android setup, additional network providers, general
plugin APIs, and recurring trust administration without an operational need.
The [mobile proposal](mobile-agent-support.md) is not an implementation
commitment.

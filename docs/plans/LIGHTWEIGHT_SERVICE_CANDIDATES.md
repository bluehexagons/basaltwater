# Lightweight Service and Monitoring Candidates

Status: proposed unscheduled project brief. Gatus is the recommended first
monitoring integration, Beszel is the recommended second integration, and
Memos is the preferred first non-monitoring application. Implementation should
start only after its shared lifecycle and recovery dependencies receive an
explicit roadmap slot.

This project evaluates popular, lightweight open-source services that
Basaltwater could install on Debian VMs. The intended operating model is a
small business running several independently managed services on one VM, not
one appliance VM or container stack per application.

The selection boundary is deliberately narrow:

- PHP applications are excluded.
- A mandatory PostgreSQL dependency defers a candidate until Basaltwater has a
  supported PostgreSQL lifecycle, which may never become a project priority.
- Native binaries and SQLite-backed services are preferred over Docker-first
  or multi-process stacks.
- Popularity is evidence of adoption, not a substitute for an auditable
  install, update, backup, health, and recovery contract.

Popularity figures below are approximate GitHub star counts observed on
2026-08-30. They will change and should not be used as acceptance criteria.

## Decision summary

- Support both Gatus and Beszel, in that order. They cover different layers:
  Gatus performs synthetic service checks and status reporting, while Beszel
  records VM resource health and trends.
- Do not present either product as complete monitoring by itself. Gatus cannot
  explain host resource exhaustion, and Beszel does not prove that a customer
  can complete an external HTTP request through DNS, TLS, and the edge.
- Make Gatus the first implementation because it is one declarative service
  with no fleet enrollment protocol. It can immediately consume health
  endpoints that Basaltwater already manages.
- Add the Beszel hub and agent as a second slice. Default remote agents to an
  outbound WebSocket connection and a loopback listener so agent ports are not
  exposed across the fleet.
- Treat Memos as the first application to exercise the reusable application
  lifecycle after monitoring. It is useful to small teams and retains the
  preferred single-binary/SQLite shape.
- Evaluate ntfy, SFTPGo, and a tightly bounded SQLite Vikunja deployment only
  after the monitoring slices. They have plausible value but are not part of
  the initial project.
- Track inventory management requested in issue #99 through the
  [HomeBox implementation record](HOMEBOX_SUPPORT.md). Its native setup,
  recovery, and recurring-update slices were separately authorized and
  delivered; the remaining monitoring sequence is unchanged.
- Defer listmonk because PostgreSQL is mandatory. Defer Pocket ID and
  Vaultwarden because identity and password storage require a stronger
  recovery and urgent-update contract than this project's first slices.
- Do not create a general application marketplace before the Gatus and Beszel
  implementations reveal which lifecycle behavior is genuinely shared.

## Why monitoring should come first

Basaltwater can install and update services, inspect systemd, collect security
events, and emit notifications, but it does not provide a durable view of
service availability or VM resource history. Adding more applications before
closing that observability gap would increase the number of workloads an
operator must diagnose without improving the diagnosis loop.

The two recommended products divide the problem cleanly:

| Layer | Gatus | Beszel |
| --- | --- | --- |
| Primary question | Can this endpoint or protocol perform its expected behavior? | Is this VM healthy, and how have its resources changed? |
| Data source | HTTP, TCP, DNS, TLS, ICMP, and other active probes | A lightweight agent reporting host and optional container metrics |
| Useful output | Health dashboard, status page, response history, alerts | CPU, memory, disk, network, temperature, SMART, trends, and alerts |
| Deployment shape | One binary and declarative configuration | One hub plus an agent on each observed VM |
| Public exposure | Optional status UI behind Nginx | Administrative hub behind Nginx; agents should not be public |
| Basaltwater integration | Generate checks from managed service facts | Enroll saved hosts and report agent/hub health in audits |

Supporting both is justified because their overlap is limited to dashboards
and alerts. They should share notification destinations and operator
documentation, but one should not be configured as a substitute for the
other.

## Evaluation criteria

A candidate should score well on all of the following before receiving
first-class support:

1. **Small-business value:** it solves a recurring operational or team problem
   without requiring a specialist administrator.
2. **Composable runtime:** it can bind to loopback or a private socket, use a
   unique service account and state directory, and coexist behind the existing
   Nginx service.
3. **Modest dependencies:** a native binary and local state are preferred.
   Mandatory PostgreSQL, Redis, workers, object storage, or container
   orchestration count against initial support.
4. **Verifiable releases:** Basaltwater can select an architecture, obtain an
   immutable version, verify publisher-provided integrity data or another
   explicit trust source, and retain a known-good rollback artifact.
5. **Observable startup:** the service exposes a reliable readiness or health
   probe and fails setup when activation is not healthy.
6. **Recoverable state:** every mutable path is known, a consistent backup can
   be created, and restoration can be tested. Copying a live SQLite file is
   not an acceptable backup strategy.
7. **Safe updates:** schema migration, binary replacement, restart, health
   verification, and rollback behavior can be stated before automatic updates
   are enabled.
8. **Bounded network surface:** listeners, proxy trust, TLS ownership, and
   firewall intent are explicit. An upstream Docker example is not permission
   to expose its default port publicly.
9. **Configuration ownership:** Basaltwater can reconcile the settings it
   owns without erasing supported operator configuration or silently accepting
   drift.
10. **Sustainable upstream:** releases and security fixes are active enough to
    justify a long-lived installer, and the license permits the intended use.

## Candidate portfolio

| Candidate | Use | Runtime/dependencies | Decision | Reason |
| --- | --- | --- | --- | --- |
| [Gatus](https://github.com/TwiN/gatus) (~11.1k) | Synthetic monitoring and status pages | Go binary; memory, SQLite, or PostgreSQL storage; YAML configuration | **Recommend: phase 1** | Best match for existing service health endpoints and declarative setup; no fleet enrollment dependency |
| [Beszel](https://github.com/henrygd/beszel) (~24.5k) | VM resource monitoring and alerts | Go hub and agent binaries; embedded hub state | **Recommend: phase 2** | Complements Gatus with resource history; native agent and outbound connection fit managed VMs |
| [Memos](https://github.com/usememos/memos) (~60.2k) | Team notes and lightweight knowledge capture | Single Go binary; SQLite by default; attachments in local state | **Recommend: phase 3 candidate** | Strong adoption, small runtime shape, and useful to small teams; proves the reusable app lifecycle beyond monitoring |
| [HomeBox](https://github.com/sysadminsmedia/homebox) | Inventory, item photos, and document tracking | Native Go binary; SQLite and local attachments | Native support delivered; [#99](https://github.com/bluehexagons/basaltwater/issues/99) | [Implementation record](HOMEBOX_SUPPORT.md) covers setup, recovery, recurring updates, and outstanding VM qualification |
| [ntfy](https://github.com/binwiederhier/ntfy) (~32.7k) | Mobile and desktop push notifications | Statically linked binary or Debian package; SQLite cache/state | Evaluate later | Natural destination for `--notify`, but public topic authorization and client delivery semantics need a dedicated design |
| [SFTPGo](https://github.com/drakkan/sftpgo) (~12.4k) | Business-partner file exchange over SFTP, WebDAV, and HTTPS | Go binary; SQLite by default; local or object storage | Evaluate later | Useful complement to Samba/Syncthing, but adds protocol, account, storage, quota, and firewall complexity; review AGPL/UI terms |
| [Vikunja](https://github.com/go-vikunja/vikunja) (~4.4k) | Task and project management | Bundled Go service and frontend; SQLite, MySQL, or PostgreSQL | Conditional/deferred | SQLite is suitable only for personal use or a handful of users according to upstream guidance; do not market that baseline for a growing team |
| [Pocket ID](https://github.com/pocket-id/pocket-id) (~8.8k) | Passkey-focused OIDC identity provider | Standalone binary; SQLite or PostgreSQL | Defer | Attractive shared identity layer, but credential recovery, issuer stability, key backup, lockout prevention, and dependent-app rollback need a separate security project |
| [Uptime Kuma](https://github.com/louislam/uptime-kuma) (~87.3k) | User-friendly uptime monitoring | Node.js plus PM2 or Docker; SQLite data | Do not prioritize | Much broader adoption than Gatus, but its runtime and update lifecycle fit Basaltwater less well; Gatus covers the required synthetic-monitoring role more cleanly |
| [listmonk](https://github.com/knadh/listmonk) (~22.5k) | Newsletters and mailing lists | Go application binary with mandatory PostgreSQL | PostgreSQL-gated | High small-business value, but unsupported until PostgreSQL provisioning, backup, upgrade, health, and restore are first-class |
| [Vaultwarden](https://github.com/dani-garcia/vaultwarden) | Bitwarden-compatible password service | Rust service, web vault, SQLite/MySQL/PostgreSQL; Docker is the common distribution | Defer indefinitely unless separately justified | A compromise or failed recovery would affect every managed credential; popularity does not lower the security and recovery bar |
| [linkding](https://github.com/sissbruecker/linkding) | Shared bookmarks | Python/Django; Docker-oriented; SQLite or PostgreSQL | Do not prioritize | Lightweight state, but a narrower business case and less suitable native lifecycle than the selected candidates |

### Excluded PHP candidates

These are credible open-source applications but are outside the selected
runtime boundary:

| Candidate | Use | Reason excluded |
| --- | --- | --- |
| [FreshRSS](https://github.com/FreshRSS/FreshRSS) (~15.7k) | Multi-user feed reader | Requires a supported PHP/web runtime even when SQLite is selected |
| [Kanboard](https://github.com/kanboard/kanboard) | Small-team Kanban | Requires PHP and is in maintenance mode; SQLite is intended only for small teams with minimal concurrency |
| [FreeScout](https://github.com/freescout-help-desk/freescout) (~4.3k) | Shared mailbox and help desk | Requires PHP plus MySQL, MariaDB, or PostgreSQL |

The exclusion is architectural rather than a claim about application quality.
Basaltwater should not add PHP-FPM, extension selection, application pools,
and a second web-runtime update policy solely to support one candidate.

## Recommended Gatus scope

[Gatus](https://gatus.io/docs) should be the first delivered slice.

### Deployment model

- Install a pinned, verified native binary rather than adding Docker to the
  target.
- Run it as a dedicated `gatus` system user with a read-only configuration
  tree and writable state only under `/var/lib/gatus`.
- Bind the dashboard to a stable loopback port. Nginx owns public TLS,
  hostname routing, proxy headers, and optional access restrictions.
- Use SQLite when history is enabled. Keep the live database on local storage
  and use the existing online SQLite backup technique before replacement or
  migration.
- Treat the dashboard as optional. A private monitoring instance may be
  reachable only over an SSH tunnel or an explicitly restricted source list.
- Record the running version, artifact digest, configuration digest, database
  path, listener, and last successful health result in managed state.

### Configuration ownership

Gatus can merge YAML files from a configuration directory. Use that behavior
to separate ownership:

- one root-managed file owns application-level storage, web, and common alert
  defaults;
- one generated file owns endpoints derived from Basaltwater-managed
  services; and
- an operator endpoint directory may append explicitly unmanaged checks.

Primitive configuration keys cannot be safely duplicated across merged Gatus
files. The initial implementation must document which global keys Basaltwater
owns and reject ambiguous managed/operator overlap rather than relying on file
ordering.

Only services with a stable observation contract should be generated. Initial
sources may include manifest components with declared health paths, Gogs,
Antistatic services, the CI/CD receiver, and other explicitly supported web
interfaces. Do not infer checks for arbitrary systemd units or guess health
paths from open ports.

Each generated check must record its observation perspective:

- a loopback readiness check proves that the local process can respond;
- a private-network check proves reachability from the monitoring VM; and
- a public URL check exercises more of DNS, TLS, proxy, and edge routing but
  still does not prove reachability from every customer network.

The UI and notifications must not describe a local-origin check as global
external availability.

### Gatus operations

The delivered operator surface should include:

- idempotent installation, reconfiguration, update, disable, and removal;
- `basaltw gatus health HOST` with stable text and JSON output;
- configuration validation before replacing the live file;
- service, listener, SQLite integrity, free-space, update-timer, and dashboard
  probes;
- release rollback after failed startup or health checks;
- backup inventory and a documented restore operation before scheduled
  updates are enabled; and
- notification delivery through existing Basaltwater webhook targets where a
  stable Gatus custom/webhook integration can preserve useful event fields.

## Recommended Beszel scope

[Beszel](https://beszel.dev/) should follow Gatus as a distinct monitoring
capability.

### Hub deployment

- Install the pinned, verified hub binary under a dedicated account.
- Keep hub state under `/var/lib/beszel` and bind the web service to loopback
  behind Nginx.
- Treat the hub as an administrative interface by default. Public exposure
  requires TLS and an explicit access policy; a status page should come from
  Gatus, not an anonymously exposed Beszel dashboard.
- Back up all embedded hub state consistently and test restoration before
  enabling automatic updates.
- Support one authoritative hub per saved environment in the first version.
  Multi-hub replication and high availability are out of scope.

### Agent deployment and enrollment

Beszel supports an outgoing WebSocket connection when `HUB_URL` is set. Make
that the Basaltwater default for remote VMs:

- bind any agent listener to loopback unless the operator explicitly selects
  the upstream inbound/SSH connection mode;
- do not open the default agent port across the fleet for outbound mode;
- store the hub public key and enrollment token in protected files, using
  upstream `KEY_FILE`/`TOKEN_FILE` support where available;
- never place an enrollment token in saved commands, process arguments,
  service unit text, logs, or support bundles;
- install the agent as its own restricted service account and grant only the
  host facts required by selected metrics; and
- do not mount or expose a Docker/Podman socket on Basaltwater's native-service
  VMs merely to populate an otherwise empty container view.

The first enrollment slice may require the operator to create a universal
token in the hub and save it through the workspace credential flow. Automated
token creation or rotation should wait for a documented, stable upstream API
and the deploy-secrets project. A failed agent enrollment must leave the VM's
other setup work and firewall unchanged.

### Beszel operations

The delivered operator surface should include:

- separate hub and agent installation choices;
- explicit association between a saved host and its hub;
- read-only hub/agent health in `basaltw audit` once the shared audit
  contract is ready;
- agent version, connection age, last telemetry time, and hub identity without
  returning enrollment material;
- coordinated but independently rollback-safe hub and agent updates;
- clean revocation and agent removal when a VM leaves management; and
- recovery guidance for replacing a hub without silently trusting a new hub
  key.

## Memos as the first team application

[Memos](https://github.com/usememos/memos) is the preferred application after
the monitoring work because it offers a useful small-team feature without a
mandatory external database or language runtime.

The initial scope should remain conservative:

- one native binary, one dedicated account, and one loopback HTTP listener;
- SQLite on local storage plus all attachment and configuration paths in the
  recovery set;
- Nginx-owned HTTPS and explicit registration policy;
- generated or workspace-supplied initial administrator credentials without
  exposing them in command output;
- application health, database integrity, capacity, update status, and backup
  age checks; and
- no promise of OIDC integration until an identity-provider project is
  separately accepted.

Memos should not be the reason to create a generic `--app` schema before the
two monitoring implementations exist. It should instead be the third example
used to decide whether repeated lifecycle code is stable enough to extract.

## Shared implementation direction

The services should compose through the existing server plugin pipeline, but
application support must not become a second deployment engine. Reuse these
existing contracts where they match:

- release architecture detection, verified artifact staging, immutable
  release directories, saved digests, and rollback from the Gogs lifecycle;
- hardened systemd unit generation and cleanup;
- Nginx, TLS, Cloudflare, source-policy, and firewall helpers;
- stable loopback port allocation and health-gated activation from manifest
  deployments;
- online SQLite backups and integrity checks; and
- saved setup state, notifications, dry-run behavior, and capability-aware
  machine handling.

The first two implementations should keep focused configuration models such
as `gatus`, `beszel_hub`, and `beszel_agent`. After both are complete, compare
their repeated code with Gogs and Memos. Extract a catalog definition only for
facts that are actually common, likely including:

- application name and upstream release source;
- supported architectures and integrity metadata;
- service account, immutable release root, and writable state roots;
- listener, health, and reverse-proxy policy;
- backup and migration hooks;
- update cadence and rollback check; and
- health/audit providers.

Application-specific authentication, initialization, configuration rendering,
multi-process topology, and database migration must remain code with focused
validation. A data-only registry must not become a way to execute arbitrary
publisher install scripts as root.

## Delivery sequence

### Phase 0: monitoring contract

1. Define stable service observations: service identity, local readiness URL,
   public URL when one exists, probe perspective, expected result, and desired
   alert policy.
2. Decide how saved host state exports those observations without exposing
   credentials or internal-only routes.
3. Define shared monitoring health/audit JSON fields and redaction rules.
4. Establish artifact-integrity and restore gates for both upstreams.

### Phase 1: Gatus

1. Add validated configuration and CLI state for one Gatus instance.
2. Implement native release installation, service isolation, SQLite state,
   Nginx routing, and health-gated rollback.
3. Generate checks only from explicit supported observations.
4. Add health, update, backup, restore, disable, and removal operations.
5. Test on a VM containing multiple Basaltwater-managed services and from a
   separate monitoring VM so probe perspectives remain honest.

### Phase 2: Beszel

1. Add the hub lifecycle and recovery contract.
2. Add protected manual-token enrollment for outbound agents.
3. Integrate agent state with saved hosts and read-only fleet audit.
4. Add token revocation/removal and hub-key replacement guidance.
5. Validate hub restore and agent reconnection on disposable VMs.

### Phase 3: Memos and lifecycle review

1. Implement the bounded Memos service and complete recovery set.
2. Compare Gatus, Beszel hub, Memos, and Gogs lifecycle code.
3. Extract shared application definitions only where doing so reduces code
   without weakening application-specific safety.
4. Re-evaluate ntfy and SFTPGo against the resulting contract.

## Acceptance criteria

- Gatus and Beszel can coexist on one Debian VM with other supported services
  without conflicting users, state paths, ports, Nginx sites, firewall rules,
  timers, or cleanup logic.
- Gatus reports the origin and perspective of every Basaltwater-generated
  check and never labels a loopback check as external availability.
- Beszel agents use outbound enrollment by default and require no public agent
  firewall rule.
- Setup validates all configuration and release artifacts before stopping an
  existing healthy service.
- Failed activation restores the previous verified binary, unit, proxy
  configuration, and application configuration where rollback is supported.
- Live SQLite files remain on local filesystems and receive consistent online
  backups; all non-database mutable paths are included in each recovery set.
- A documented, tested restore path exists before an automatic application
  update timer is enabled.
- Enrollment tokens, application secrets, webhook URLs, and initial
  credentials are absent from argv, saved commands, normal output, logs,
  generated JSON, and support bundles.
- Health and audit commands distinguish unavailable facts from healthy facts
  and support stable JSON output.
- Disable and removal operations preserve user data by default and report
  exactly which retained paths require a separate destructive action.
- Unit tests mock system calls and use temporary directories; live validation
  uses disposable VMs and never targets a production service.

## Non-goals

- Building a Docker Compose or general container-application marketplace.
- Installing or supporting PHP.
- Adding PostgreSQL solely to unblock one candidate.
- Automatically remediating a VM or restarting applications in response to a
  Gatus or Beszel alert.
- Claiming high availability, multi-region monitoring, or external customer
  perspective from a single monitoring VM.
- Replacing Prometheus, Grafana, a log aggregation platform, or a full
  observability pipeline.
- Exposing Beszel agents publicly by default.
- Automatically enrolling every saved host without an explicit hub and
  credential relationship.
- Treating a successful backup command as recovery without a tested restore.

## Upstream references

- [Gatus repository and configuration](https://github.com/TwiN/gatus)
- [Gatus documentation](https://gatus.io/docs)
- [Beszel documentation](https://beszel.dev/guide/getting-started)
- [Beszel agent installation and enrollment](https://beszel.dev/guide/agent-installation)
- [Memos repository and deployment overview](https://github.com/usememos/memos)
- [ntfy installation](https://docs.ntfy.sh/install/)
- [Vikunja installation](https://vikunja.io/docs/installing/)
- [SFTPGo repository](https://github.com/drakkan/sftpgo)
- [Pocket ID installation](https://pocket-id.org/docs/setup/installation)
- [listmonk installation](https://listmonk.app/docs/installation/)
- [Uptime Kuma repository](https://github.com/louislam/uptime-kuma)
- [FreshRSS prerequisites](https://freshrss.github.io/FreshRSS/en/admins/02_Prerequisites.html)
- [Kanboard requirements](https://docs.kanboard.org/v1/admin/requirements/)
- [FreeScout repository and requirements](https://github.com/freescout-help-desk/freescout)

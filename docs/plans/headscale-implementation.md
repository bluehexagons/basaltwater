# Headscale implementation handoff

Status: hosting, client enrollment, managed Unbound, onboarding and portal
handlers are integrated. Native qualification and final review remain in
progress; the [qualification record](../testing/headscale.md) separates passing
checks from outstanding acceptance. This document retains the full contract.

## Read first

Read root [AGENTS.md](../../AGENTS.md), the
[contributor guide](../agents/contributing/README.md) and these contracts:

- [Scope, selected defaults and six milestones](headscale.md).
- [Native enrollment, public instructions and private administration](headscale-enrollment.md).
- [All-to-all access, owned relays and managed DNS](headscale-networking.md).

The [SSO assessment](headscale-sso.md) is deferred background, not implementation
scope. This tracked handoff contains reusable design, not deployment inventory.
Keep current checkout/revision, raw evidence, live job handles and credentials in
a private checkpoint under the [handoff convention](../agents/handoffs.md).

## Fixed scope

- Independent optional Headscale hosting and client enrollment; clients can use
  a saved managed or external server by name, or an explicit HTTPS URL.
- Start managed installation on Debian server/client profiles with usable TUN
  and the needed package/service/firewall capabilities. Qualify supported CPU
  architectures explicitly. Official end-user instructions cover Windows,
  macOS, Linux, iOS and Android without requiring Basaltwater on those devices.
- New managed networks permit all enrolled nodes to communicate. Native approval
  is the shared trust boundary; service authentication remains in force.
- Host Headscale, verified-client DERP/STUN and Unbound ourselves. Remove the
  public Tailscale relay map and recursive DNS provider defaults. One relay and
  one DNS node are the initial availability tradeoffs.
- Native approval per person; tagged machines use single-use, one-hour enrollment
  keys. Honor native device expiry and administrator revocation. No scheduled
  reapproval, custom invitations, OIDC or identity provider.
- MagicDNS answers node names; managed Unbound on a separate enrolled Debian
  node resolves all remaining queries. Unbound installation and management are
  deliverables in this project. Validate DNS before adopting it.
- Static HTTPS join instructions; Headscale administration in the Web Portal
  when both services are installed; independent native CLI-over-SSH recovery.
- Preserve adopted policies and DNS settings, unrelated configuration and SSH
  recovery. External endpoint discovery never conveys management authority or
  guarantees that an external server meets the owned-relay/DNS baseline.

Subnet routing, exit nodes, split DNS, LAN-wide node-record synchronization,
additional relay/resolver nodes, advanced policy editing and broader managed
client platforms are later work. Do not turn this into a general DNS or identity
management framework.

## Repository entrypoints

Read current implementations and callers before extending these patterns; names
below are entrypoints, not a requirement to put all code in existing large files.

| Concern | Start here |
| --- | --- |
| Configuration, validation, serialization | `lib/config.py`, `lib/runtime_config.py`, `lib/validation.py`, `lib/validators.py`, `lib/config_cleanup.py` |
| CLI and command reconstruction | `lib/arg_parser.py`, `lib/command_display.py`, `lib/completions.py`, root command dispatch |
| Saved selection and private state | `lib/build_selection.py`, `lib/cache.py`, `lib/workspace.py`, `lib/atomic_io.py` |
| Target-side setup composition | `remote_setup.py`, `plugins/server.py`, `plugins/web.py`, `plugins/post_setup.py` |
| Web/TLS and host access | `web/ssl_steps.py`, `lib/nginx_config.py`, `security/steps.py`, `lib/machine_state.py` |
| Service/configuration transactions | `lib/unit_transaction.py`, `lib/operation_state.py`, contributor transaction-safety guidance |
| Portal installation and installed-state discovery | `common/web_panel_steps.py`, `common/service_tools/web_panel_features.py` |
| Portal routes, templates and administration | `common/service_tools/web_panel_service.py`, `web_panel_templates.py` and `web_panel_admin.py` in the same directory |
| Privileged administration boundary | `lib/privilege_client.py`, `lib/privilege_policy.py`, existing bounded panel action adapters |
| Verification | `run_tests.py`, `tests/test_build_selection.py`, configuration/plugin tests, `tests/test_web_panel_installed_state.py`, relevant panel security tests |

Implement feature-specific helpers in owning modules/packages. Keep remote setup
mutations behind the plugin plan; local bootstrap must not become a second
target-side installer. Use existing validators, atomic writes and service
transactions. Mock system calls and use temporary directories in offline tests.

## Configuration and saved references

Milestone 1 is integrated. Use `--headscale` for
hosting, mutually exclusive `--tailscale NAME` / `--tailscale-login-server URL`
for joining, and `--unbound` for the separate managed resolver. The
[reference](../services/headscale-configuration.md) documents supported flags and commands;
help, completion and reconstruction have focused offline coverage.

Named-server operations must cover add, list/show, rename, removal of a reference
and printing onboarding instructions. Removing a reference never deletes remote
users, nodes or server data. Keep endpoint discovery independent of native
administration and any separately configured SSH management binding.

Keep records in the existing private workspace, with a schema version, stable
record ID, unique case-insensitive name, validated HTTPS endpoint, managed/external
origin and optional onboarding URL. Reuse existing private-state safety and
locking conventions. Reject malformed/corrupt/unknown-version state without
resetting it. Do not put credentials in these records. Reject URL userinfo,
untrusted command text and ambiguous references before mutation.

Saved client selections retain stable record ID plus resolved endpoint. A record
rename preserves selection. An endpoint change cannot silently move an enrolled
client: require explicit migration, preserve recovery and reconcile actual client
identity before a new join. Pass only necessary resolved target configuration to
remote setup, not the controller's whole server or management inventory.

Make omitted, enabled and explicitly disabled states survive cache merge, command
reconstruction and remote argument transport. Dry runs perform no enrollment,
package/service changes, key issuance or feature-state mutation. Tests cover
new/external records, conflicts, rename/migration, unsupported profiles and safe
secret-free round trips. Commit this coherent slice before service work.

## Dependency order and verification boundaries

1. **Headscale and owned relay.** Select a stable release and compatible client
   baseline, record verified artifact sources/checksums and use configuration/API
   documentation from that release tag. Validate with `headscale configtest`.
   Implement private state, SQLite, direct HTTPS/Nginx protocol upgrades, local
   administration, verified-client embedded DERP and UDP STUN. Keep public relay
   maps disabled. Preserve working configuration on validation/activation failure.
2. **Native clients and administration.** Implement interactive registration,
   exact Auth-ID approval, observed node status, tagged one-use machine keys and
   native expiry/removal. Verify supported machine-readable CLI/API operations
   against the pin; do not parse human tables or assume a pending-request list
   exists. Use bounded typed adapters and no browser-supplied shell commands.
   Keys travel through private transient input, never argv, saved setup payloads,
   URLs, logs or artifacts. Reconcile interrupted enrollment before retrying.
3. **DNS node, then DNS adoption.** Bootstrap and enroll the separate DNS node
   using existing DNS first. Install distro-managed Unbound and its owned fragment;
   validate configuration and private UDP/TCP 53, recursion and DNSSEC. Verify the
   node belongs to the intended network and record its observed overlay address.
   Test from another client before publishing the resolver through Headscale.
   Then apply MagicDNS and Unbound for general resolution. Preserve existing
   platform DNS management and qualify bootstrap, boot ordering and outages.
4. **Join page and portal.** Generate static, escaped, platform-specific
   instructions from trusted configuration, with a QR containing only the page
   URL. Implement installed-state-aware portal navigation, native user creation/
   inspection, exact-request approval and node inspection/expiry/revocation.
   Bound destructive user operations when enrolled nodes still exist. Retain
   existing authentication, authorization, CSRF and privileged review policies;
   add narrow backend actions rather than granting the portal broad socket/root
   authority. Native Headscale approval remains distinct from portal privilege
   approval. Public pages cannot access either administrative boundary.
5. **Recovery and qualification.** Implement consistent private backups of
   configuration, policy, SQLite and private keys, plus Unbound configuration and
   local records. Include DNS-node identity/state or a documented re-enrollment
   and resolver-rebinding recovery procedure. Follow sequential Headscale minor
   upgrades; retain matching pre-upgrade data. Explicit disable/leave operations
   preserve server/client data; data purge is a separately requested operation.
   Update operator guides with installation, diagnosis, offboarding and recovery.

## Acceptance and completion

Offline checks must verify behavior: validated configuration, composition/order,
safe persistent state, rerun preservation, failure/rollback propagation, policy
generation, bounded administrative inputs, denied actions and secret handling.
Register new tests in the appropriate `run_tests.py` suites. Run affected suites
during each slice and `make check-ci` for shared configuration/plugin changes.
Portal work also needs the project's separate browser qualification.

Live qualification uses disposable systems and records the exact candidate and
platforms tested, including:

- Fresh control server, separate DNS node and peer client; valid HTTPS, native
  enrollment, bidirectional access, restrictive-policy fixture and unchanged WAN
  service exposure. Repeat setup and reboot without changing node identities.
- Owned-only client relay map and forced relay with public Tailscale relays/map
  blocked; direct-path connectivity, unknown relay-client denial and relay outage.
- MagicDNS, private records, public recursion, valid/bogus DNSSEC, UDP/TCP DNS,
  public recursion denial and resolver outage. Control-host resolution works
  before enrollment and after reconnect/reboot; no public recursive fallback.
- Real end-user onboarding on each advertised OS, exact-request approval,
  expired/replayed requests, one-use/expired machine keys, native expiry and
  revocation of an already connected node, including after restart.
- Public join-site isolation, portal feature gating and authorization/CSRF denial;
  no enrollment keys in URLs and no keys or pending authentication IDs in logs or
  artifacts. Native registration URLs may contain an Auth ID; never put it in
  the shareable join URL or QR code.
- Replacement-host restore and upgrade recovery, including client connectivity,
  DNS binding, current revocations and independent SSH administration.

Only claim implemented or qualified behavior that has passed its checks. Missing
devices or disposable infrastructure leave named qualification gaps; they do not
block offline implementation or count as passes. Update the coverage map and
operator/reference documentation as each capability lands. Commit and push
coherent validated slices on the feature branch and open a pull request for
final checks and review, as authorized for this project.

## Inputs and agent-owned choices

Collect real hostnames, DNS suffixes, DNS-node selection, architecture, SSH
management identity, certificate inputs, onboarding visibility and backup
destination in private configuration before deployment. Public static onboarding
is the default; LAN/source restriction remains an explicit deployment option.
Do not ask for these values to begin the configuration slice.

The implementing agent owns module names, supporting flag spelling, package pins,
release-specific native API/CLI mappings and test organization within these
contracts. Recheck upstream releases rather than treating research examples as
pins. Escalate a demonstrated incompatibility or a required scope change; no
further product approval is needed for routine implementation choices.

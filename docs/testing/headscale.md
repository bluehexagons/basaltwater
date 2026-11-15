# Headscale and Unbound qualification

Updated 2026-11-15 UTC. Implementation is integrated on the feature branch;
this selected qualification is incomplete and does not replace the six
[project acceptance milestones](../plans/headscale.md#milestones).
Real endpoints, credentials, inventories and raw evidence remain private.

The disposable native topology has a separate Debian control server, DNS node
and enrolled peer. Versions are Debian 13.6 amd64, Headscale 0.29.7, Tailscale
1.104.7 and distribution Unbound 1.26.2. The deployed source began at
`3b2c9860`, with shared-root locking from `bbca7b83` and record management from
`28372b78` applied afterward. Frozen candidate `68b1ebdc` passed the combined
offline and application-browser checks below. Later candidate `145197b2` has
focused native client-resume evidence; subsequent backend fixes still need final
combined CI. These results do not qualify arbitrary combinations or architectures.

## Passing native checks

- Native control service, Unix-socket administration and Nginx HTTPS serve the
  control protocol and isolated static onboarding. TLS uses a private test CA;
  public certificate issuance has not been qualified.
- Two tagged clients enroll through the public controller workflow with private,
  one-use machine-key delivery. Actual identities and daemon privacy are
  observed. Bidirectional TCP access passes over direct paths and forced owned
  DERP. The client map contains only the owned relay, and an unknown native
  relay client is rejected after the HTTPS/DERP handshake.
- Stopped Headscale backup, snapshot validation and restart preserve native
  identities. This is not replacement-host restore or upgrade qualification.
- Control, DNS and client reboots retain identity, DNS and bootstrap settings.
  At `145197b2`, tagged-client disable/enable and stopped-profile resume change
  only `WantRunning`; 70 focused client tests also pass.
- Fresh Unbound installation, native configuration validation and health pass.
  Reviewed recovery of both legacy and current fresh-install failures passes;
  package effects are reconciled independently before guards are completed.
- Explicit DNS publication retains both reservations and its journal when peer
  propagation is unconfirmed. Exact after-side reconciliation succeeds once the
  independent peer verifies the effective configuration. Advertised-resolver
  expiry and client disable are blocked. Reviewed withdrawal succeeds, with
  teardown and expiry denied while the resolver remains advertised.
- The independent peer verifies identity, recursion and valid/bogus DNSSEC over
  UDP and TCP on each assigned resolver address. MagicDNS names and the actual
  systemd-resolved path through the overlay work. An unenrolled LAN client
  cannot recurse through the DNS node's LAN address.
- During a bounded resolver outage, device-local and OS-stub queries fail
  visibly without adding a public fallback. Independent control-host lookup
  survives; resolver restart restores full native health.
- The native administration adapter runs under the broker's systemd sandbox,
  including private temporary storage, a read-only system and AF_UNIX only.
  User creation/deletion and node inspection pass. A concurrent host-root DNS
  lease blocks the sandboxed caller through the shared root lock directory.
- The public records command adds A, AAAA and CNAME inventory while preserving
  the advertised binding, and refuses a stale inventory review. Independent peer
  and OS lookup, replacement and removal pass.
- The installed HTTPS portal enforces BasicAuth and completes separately reviewed
  native user creation/deletion and inventory. Forged CSRF receives 403; changed
  reviews, cross-origin submissions and replay receive 409.
- Narrow adoption of a compatible existing native installation byte-preserves
  configuration, policy, private key and database. It remains stopped until a
  separate normal setup activates it.
- Stopped Unbound backup, validation and restore pass with an explicitly reviewed
  same-identity rebind. Separate activation restores DNSSEC health. This does not
  qualify rebinding to a different native identity or independent republication.

## Offline and browser boundaries

Frozen candidate `68b1ebdc` passed `make check-ci`: 10,030 tests, 26 skipped,
82% coverage, and all static, documentation, brand, package and wheel checks.
Focused tests cover subsequent fixes; final combined CI for those later backend
changes remains required.

The frozen full application run passed 84/84 checks across Chromium and Firefox,
including all 18 Headscale checks. This resolves earlier unrelated Firefox form
failures caused by concurrent source edits restarting the development worker;
CSRF protection is retained. The browser fixture uses synthetic broker responses;
native portal evidence above separately covers the installed proxy and reviewed
execution. Neither establishes personal Auth-ID lifecycle acceptance. See the
[browser boundary](../agents/browser-testing.md#headscale-portal-and-static-onboarding-boundary).

## Outstanding acceptance

Finish replacement-control restore/cutover and controlled upgrade with matching
database recovery; different-native-identity Unbound rebind and independent
republication; restrictive policy, bounded transfer and relay-outage checks; exact
personal Auth-ID approval/denial, expiry/replay and connected-client revocation.
The narrow adoption and resume results do not establish general migration or all
setup-rerun combinations. Update this record as those checks complete; the six
project milestones remain subject to their complete acceptance criteria.

Public ACME, ARM64 and native Windows, macOS, iOS and Android onboarding remain
separate gaps. Missing devices do not count as passes. Native Firefox ESR is
separate from Playwright Firefox. Native client-identity portability, power loss
and broader interruption boundaries retain their documented limits.

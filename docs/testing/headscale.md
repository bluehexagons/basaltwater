# Headscale and Unbound qualification

Updated 2026-11-15 UTC. Implementation is integrated on the feature branch;
this selected qualification is incomplete and does not replace the six
[project acceptance milestones](../plans/headscale.md#milestones).
Real endpoints, credentials, inventories and raw evidence remain private.

The disposable native topology has a separate Debian control server, DNS node
and enrolled peer. Versions are Debian 13.6 amd64, Headscale 0.29.7, Tailscale
1.104.7 and distribution Unbound 1.26.2. The deployed source began at
`3b2c9860`, with shared-root locking from `bbca7b83` and record management from
`28372b78` applied afterward. Later checks must identify their exact candidate;
these results do not qualify arbitrary combinations or architectures.

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
- Fresh Unbound installation, native configuration validation and health pass.
  Reviewed recovery of both legacy and current fresh-install failures passes;
  package effects are reconciled independently before guards are completed.
- Explicit DNS publication retains both reservations and its journal when peer
  propagation is unconfirmed. Exact after-side reconciliation succeeds once the
  independent peer verifies the effective configuration. Advertised-resolver
  expiry and client disable are blocked.
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
  the advertised binding, and refuses a stale inventory review. Peer resolution,
  replacement and removal still require completion of the native checks.

## Offline and browser boundaries

An earlier broad candidate passed 9,981 tests with 25 skipped, 82% coverage and
the static, documentation, brand and wheel checks. Focused tests cover subsequent
fixes. A final combined `make check-ci` run remains required.

All 18 Headscale checks passed in both full Chromium/Firefox browser attempts.
Each complete application attempt had one other Firefox form failure while
source edits triggered a development-worker restart. Trace review confirmed
that the form and rejection came from different worker generations. Preserve
CSRF protection and rerun the full suite with source frozen. The fixture uses
synthetic broker responses; it does not qualify native portal authorization or
execution. See the [browser boundary](../agents/browser-testing.md#headscale-portal-and-static-onboarding-boundary).

## Outstanding acceptance

Finish native private-record resolution/replacement/removal, setup reruns and
reboot; restrictive policy and relay-outage/performance checks; exact personal
Auth-ID approval, expiry/replay and connected-client revocation; installed HTTPS
portal/proxy authentication and reviewed execution; replacement-host restore and
controlled upgrade with matching database recovery. Managed adoption is awaiting
integration and qualification. Update this record as those checks complete.

Public ACME, ARM64 and native Windows, macOS, iOS and Android onboarding remain
separate gaps. Missing devices do not count as passes. Native Firefox ESR is
separate from Playwright Firefox. Native client-identity portability, power loss
and broader interruption boundaries retain their documented limits.

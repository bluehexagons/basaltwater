# Headscale and Unbound qualification

Updated 2026-11-15 UTC. Implementation and selected Debian qualification are
integrated on the feature branch. This evidence does not replace the six
[project acceptance milestones](../plans/headscale.md#milestones).
Real endpoints, credentials, inventories and raw evidence remain private.

The disposable native topology has a separate Debian control server, DNS node
and enrolled peer. Versions are Debian 13.6 amd64, Headscale 0.29.7, Tailscale
1.104.7 and distribution Unbound 1.26.2. Native fixtures use the integrated
source with scoped backend updates: client resume through `140caa4b`, native
version recovery through `750dc50d`, resolver privacy through `37db790d`, and
peer DNS-map observation through `0d0e11c4`. The complete backend candidate
`0d0e11c4` passed combined offline CI; unchanged application UI from `68b1ebdc`
passed the browser checks below. These results do not qualify arbitrary
combinations or architectures.
Follow-up native checks use the scoped runtime changes from `1948f60a` for
resolver file permissions, recovery exclusion and endpoint-bound administration.

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
  identities. A replacement control host restores the endpoint, Noise/DERP keys,
  database and original client identities/connectivity. Only one copy runs
  during cutover; the replacement is stopped before the original resumes.
- A reviewed official 0.29.6-to-0.29.7 patch upgrade succeeds. Injected post-start
  failure after an actual newer native database write recovers the matching
  prior binary, database, policy and keys with native health. Its retained guard
  is independently reconciled before a subsequent successful upgrade.
- Control, DNS and client reboots retain identity, DNS and bootstrap settings.
  Tagged and personal-client disable/enable and stopped-profile resume change
  only `WantRunning`; native startup transitions retain exact identity checks.
- A restrictive native policy denies the test TCP service while permitting SSH;
  restoring the original policy restores access. Three exact-digest two-MiB
  transfers pass on each direct and forced owned-DERP path. With direct UDP
  blocked, relay outage denies fresh TCP connections; restart restores transfers.
  This small same-host virtual lab is not a WAN throughput or capacity benchmark.
- Fresh Unbound installation, native configuration validation and health pass.
  Reviewed recovery of both legacy and current fresh-install failures passes;
  package effects are reconciled independently before guards are completed.
- Explicit DNS publication retains both reservations and its journal when peer
  propagation is unconfirmed. Exact after-side reconciliation succeeds once the
  independent peer verifies the effective configuration. Advertised-resolver
  expiry and client disable are blocked. Reviewed withdrawal succeeds, with
  teardown and expiry denied while the resolver remains advertised. A fresh
  publication/withdrawal also succeeds with the bounded read-only peer-map wait,
  without manual reconciliation or repeated activation.
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
- Exact personal Auth-ID denial and replay refusal, five-minute review expiry,
  and fresh approved registration pass through the installed HTTPS proxy and
  real broker. The resulting untagged device belongs to the selected native user.
  Separately reviewed deletion revokes its already connected access; native
  inspection confirms removal, and restarting the client does not restore access
  or recreate enrollment. Independent root SSH remains available.
- Ordinary accounts cannot access the native management socket. A direct Unix
  backend caller with valid proxy-header names is rejected by peer identity.
  The public onboarding origin exposes no administrative/approval routes, and
  the installed native QR decodes to the canonical static join URL only.
- Narrow adoption of a compatible existing native installation byte-preserves
  configuration, policy, private key and database. It remains stopped until a
  separate normal setup activates it.
- Stopped Unbound backup, validation and restore pass with the same native identity
  and a separately reviewed different enrolled identity. Restore remains stopped
  and disabled until separate activation. Full DNSSEC health, independent peer
  republication and withdrawal pass. The temporary resolver is then disabled
  through normal setup, retaining recovery state.
- Follow-up resolver setup, restart and stopped backup/restore retain a root-owned
  `0640` fragment readable by the `unbound` group and denied to an ordinary
  account. Full native DNSSEC health passes after separate activation, and the
  original local records are restored. A retained recovery-snapshot fixture
  causes the real DNS helper to refuse reservation without changing the binding.
- Follow-up administration under private temporary storage, a read-only system
  and AF_UNIX-only transport records the actual endpoint in privilege reviews,
  completes reviewed native user creation/deletion, and refuses an explicit
  mismatched endpoint before creating a user. Original users are preserved.
- Listener preflight from `d05e519c` accepts a real temporary IPv6 TCP listener
  whose address contains `8080` while its actual listening port is unreserved.
  The existing managed control configuration and other services are preserved.
- An isolated native kernel-TUN daemon supplies logged-out `Self` host metadata.
  The identity check from `6bf41130` accepts that native status and refuses
  injected node IDs, overlay addresses, online state or network-map membership.
  This check performs no enrollment and preserves the managed client.
- Setup-led orchestration runs against an existing native control server and
  saves its verified management binding automatically. A matching enrolled peer
  rerun preserves its identity without issuing a key. A target with no native
  client or resolver completes client installation, tagged enrollment and Unbound
  installation in one public setup command; an unchanged rerun also succeeds.
  The separate inactive control identity already on that test target is preserved.
- The controller's single DNS hookup command generates its dedicated authority
  on the control host, installs forced-command authorization, derives native
  identities and passes publication. Peer OS MagicDNS lookup and ordinary
  recursive queries over UDP/TCP pass. An arbitrary shell command using the
  publication key is refused. After actual withdrawal, an injected lost reply
  retains the controller attempt and blocks replay. Exact reviewed
  `dns-reconcile` confirms settled native policy, identity, resolver and peer
  health and completes only the controller record. Normal setup then disables
  the withdrawn resolver and client while retaining the enrolled identity and
  native state. All owned lab guests finish stopped with autostart disabled.

## Offline and browser boundaries

Backend candidate `0d0e11c4` passed `make check-ci`: 10,045 tests, 26 skipped,
82% coverage, and all static, documentation, brand, package and wheel checks.
The final independent delta review found no actionable regression; its 86
focused mocked tests also pass. Later documentation-only updates pass the
documentation checks.
Review candidate `1948f60a` also passes all three hosted CI checks and local
`make check-ci`: 10,052 tests, 26 skipped and 82% coverage. That review fixes
root-CI fixture isolation, endpoint-bound approvals, private resolver fragments
and reservation exclusion during retained recovery.
Review candidate `6bf41130` passes `make check-ci`: 10,061 tests, 26 skipped,
82% coverage and all static/documentation/brand/package/wheel checks. It adds
durable logout intent, verified enrollment prestate, fresh-hosting failure
masking and precise listener checks. The subsequent setup guard in `ecd1aa9e`
passes 105 focused client, controller, selection and composition tests.

VM orchestration candidate `d324be49` passes `make check-ci`: 10,071 tests,
26 skipped, 82% coverage and all static/documentation/brand/package/wheel checks.
Independent review identified a direct-SSH delimiter regression, which is fixed
with full-parser and lazy-dispatch coverage; 121 focused CLI tests pass. The
[three-VM walkthrough](../services/headscale-vms.md) now uses setup-led automation
and passes offline execution with host operations replaced by fixtures: five
Bash blocks, eight public commands, four setup configurations and all three
managed DNS actions. The complete fresh-VM Proxmox/public-ACME walkthrough has
not been run as a single live deployment; the native checks above retain their
stated scope.

Setup-led integration candidate `d2747c42` passes `make check-ci`: 10,110 tests,
26 skipped, 82% coverage and all static/documentation/brand/package/wheel checks.
Independent review covers enrollment ordering, saved authority, active-firewall
preservation and guarded DNS completion. The final corrected-tag admission and
malformed-cache fixes in `2d120e08` pass 48 focused tests, retaining native
identity checks and private recovery evidence.

The frozen full application run passed 84/84 checks across Chromium and Firefox,
including all 18 Headscale checks. This resolves earlier unrelated Firefox form
failures caused by concurrent source edits restarting the development worker;
CSRF protection is retained. The browser fixture uses synthetic broker responses;
native portal evidence above separately covers the installed proxy and reviewed
execution and the personal Auth-ID lifecycle. See the
[browser boundary](../agents/browser-testing.md#headscale-portal-and-static-onboarding-boundary).

## Outstanding acceptance

The narrow adoption, selected patch upgrade/recovery and resume results do not
establish general migration, sequential-minor upgrades or all setup-rerun
combinations. Replacement recovery used another guest on the same physical host;
off-host disaster recovery requires separate qualification. The six project
milestones remain subject to their complete acceptance criteria.

Public ACME, ARM64 and native Windows, macOS, iOS and Android onboarding remain
separate gaps. Missing devices do not count as passes. Native Firefox ESR is
separate from Playwright Firefox. Native client-identity portability, power loss
and broader interruption boundaries retain their documented limits.
The retained logout-intent and pre-enrollment recovery paths have mocked fault
coverage; their lost-response scenarios have not been fault-injected natively.

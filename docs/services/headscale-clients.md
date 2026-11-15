# Native Debian Headscale clients

The client implementation is an integration API; parent setup/plugin and root
CLI wiring are separate. It installs native Tailscale using the official signed
stable APT repository on Debian 12/13, amd64/arm64. The reviewed baseline is
Tailscale 1.104.7 with Headscale 0.29.7. Headscale's protocol minimum is 1.80;
the enforced client security floor is 1.104.4. Security updates are not held.
Other architectures and platforms need separate qualification.

`common.tailscale_steps.configure_tailscale(config)` leaves omitted configuration
inert. Enable installs the daemon and preserves existing managed enrollment.
Disable stops/disables the daemon and preserves native identity and state; it
does not log out. Dry runs do not install, enroll or save client metadata.
Existing native installations require explicit reviewed adoption. Configuration
and observed control URL must agree before changes; changing a saved endpoint
cannot silently move a client. The actual local Headscale configuration is also
checked to prevent joining the control host to its own network.
If a retained native state file exists while the daemon is inactive, automatic
enable fails before service changes: review the retained profile and start the
daemon through independent administration, then rerun reconciliation/setup.
This conservative recovery gate also applies after an explicit disable.

The installer writes `TS_NO_LOGS_NO_SUPPORT=true` in a systemd drop-in and reloads
systemd before APT's postinst can first start the daemon. Headscale's separate
`logtail.enabled: false` setting remains the server installer's responsibility.
An adopted installation must already have the same privacy drop-in; inspect and
apply it through a reviewed restart before adoption. No WAN ports are opened.
Native `--accept-dns=true` delegates DNS changes to Tailscale's OS integration;
Basaltwater does not write `/etc/resolv.conf` or enable routing/exit nodes.

## Explicit enrollment

After setup prepares the client, an authorized adapter may call
`lib.headscale_client.enroll_client(url, interactive=True)` in a private terminal.
The native client prints registration instructions there, without launching or
requiring a local browser. Follow the device instructions and transmit the exact
pending Auth ID to the administrator over an authenticated channel. The
administrator verifies the person and approves that exact request for the
intended Headscale user. Hostname is insufficient. Native URLs can contain the
Auth ID: do not record this terminal in general setup logs or public artifacts.

For tagged services, an authorized controller must mint a single-use, one-hour
key remotely and deliver a transient private file. Call
`enroll_client(url, key_file=path, expected_tags=("tag:service",))`. No key belongs
in SetupConfig. The file and its containing directory must be private and owned
by the executing root account; symlinks, multiple links and unsafe modes are
rejected. The native command uses `--auth-key=file:PATH`; the value never enters
argv. Once submitted, the file is removed on success or interruption. Rejected
input files remain the controller's cleanup responsibility. Issuance authority,
one-use and lifetime enforcement belong to the remote controller, not this API.

An enrollment marker is saved before submission. Interrupted or lost responses
require `reconcile(url)` against the observed client before any new key issuance.
Setup never replays enrollment. If no enrolled identity can be observed, retain
the marker and investigate/revoke the issued request remotely; do not delete the
marker to retry. After remote reconciliation/revocation, explicitly confirmed
`abandon_enrollment(url, confirm=True)` can log out a still-pending attempt and
prepare a fresh explicit join. It refuses an enrolled identity and holds the
same DNS lifecycle guard. Personal expired requests require the same review.

## Identity and lifecycle APIs

`observe_client(expected_url, *, require_running=True)` reads bounded native
`status --json --peers=false` and `debug prefs`, validates client/daemon versions,
control URL and overlay IPs, and returns frozen `ClientIdentity` fields:
`node_id`, `addresses`, `login_server`, `tags`, `online`, `hostname`. AuthURL is
discarded. Unknown native states, userspace networking and mismatched versions
fail closed. This is the identity contract for managed Unbound.

`adopt_client(url, *, expected_node_id)` adopts an explicitly reviewed running
node. `leave_client(url, *, expected_node_id)` verifies the exact node before
native logout and retains state. `migrate_client(url, new_url, *, expected_node_id)`
verifies and logs out the old identity, then prepares the new selection; a new
explicit enrollment remains necessary. The caller must show the URLs and node
identity for review and confirm independent recovery access before these calls.
Leaving locally does not prove administrative node revocation; administrators
must revoke/remove the remote node and verify access loss separately.

Disable, leave and migration hold the shared resolver lifecycle guard throughout
mutation. An advertised or reserved DNS resolver blocks teardown until verified
withdrawal/replacement clears its publication binding. Client state lives under
`/var/lib/basaltwater/tailscale`; preserve it with `/var/lib/tailscale` in private
recovery backups. Never publish native state or debug preferences.

Offline mocked tests cover lifecycle and secret handling. Live package startup,
native enrollment/approval, reboot, OS DNS restoration, relay and peer access,
revocation and multi-platform device instructions remain unqualified.

Upstream contracts: [official APT packages](https://pkgs.tailscale.com/stable/),
[native up flags and secret-file input](https://github.com/tailscale/tailscale/blob/v1.104.7/cmd/tailscale/cli/up.go),
[typed status](https://github.com/tailscale/tailscale/blob/v1.104.7/ipn/ipnstate/ipnstate.go),
[native preferences](https://github.com/tailscale/tailscale/blob/v1.104.7/ipn/prefs.go),
and [Headscale topology/logging guidance](https://headscale.net/stable/about/faq/).

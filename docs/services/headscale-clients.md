# Native Debian Headscale clients

The client setup installs native Tailscale using the official signed
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
After an explicit disable, enable can resume an enrolled managed client using
its persisted endpoint and exact node binding. Before starting, it checks the
private native state and ownership record, reviewed privacy drop-in, and installed
client security floor. After starting it verifies effective daemon privacy,
client/daemon version agreement, endpoint and exact node, then online identity.
A stopped native profile may receive credential-free `tailscale up` only after
that identity matches. No key, logout or automatic enrollment is involved.
Unowned state, pending joins, missing identity and changed bindings require
explicit recovery; setup never adopts them.
An inactive prepared or explicitly left client can also be enabled: setup checks
private retained state, the privacy drop-in and client version before starting,
then verifies a private daemon with no issued node key in `NeedsLogin` state.
It preserves the prepared/left phase and waits for separate explicit enrollment.
Failed observation or a lost start response stops/disables the daemon again.

Resume writes a `resuming` marker before service changes. Failed startup,
lost observation, privacy failure or changed identity triggers guarded daemon
disable/stop and retains that marker and the expected endpoint/node. If stopping
cannot be verified, the operation reports uncertainty. Setup refuses automatic
retry. Review native state through independent recovery access, establish a
private daemon under operator supervision, and explicitly reconcile the exact
retained identity before enabling again. Do not delete the marker to retry or
substitute a new identity. An advertised/reserved DNS resolver blocks a resume
that could require failure teardown until its publication is withdrawn.

The installer writes `TS_NO_LOGS_NO_SUPPORT=true` in a systemd drop-in and reloads
systemd before APT's postinst can first start the daemon. Headscale's separate
`logtail.enabled: false` setting remains the server installer's responsibility.
An adopted installation must already have the privacy setting; inspect and
apply it through a reviewed restart before adoption. Adoption, local enrollment
and identity-dependent lifecycle mutations require both effective systemd
Environment and the running process environment to contain
`TS_NO_LOGS_NO_SUPPORT=true`. A drop-in alone is insufficient. Native state and
its directory must be private, owned and free of symlinks/multiple file links.
No WAN ports are opened.
Native `--accept-dns=true` delegates DNS changes to Tailscale's OS integration;
Basaltwater does not write `/etc/resolv.conf` or enable routing/exit nodes.

## Local recovery commands

Run these commands as root on the client through independent SSH recovery.
Setup must first prepare the managed client; an existing enrolled installation
instead requires reviewed adoption. Status prints safe observed identity JSON.

```bash
sudo basaltw headscale-client status --url https://vpn.example.com
sudo basaltw headscale-client enroll-personal --url https://vpn.example.com
sudo basaltw headscale-client enroll-machine --url https://vpn.example.com --key-file /run/private/join.key --approved-tag tag:service
sudo basaltw headscale-client reconcile --url https://vpn.example.com
sudo basaltw headscale-client adopt --url https://vpn.example.com --node-id 7
sudo basaltw headscale-client disable --url https://vpn.example.com
sudo basaltw headscale-client leave --url https://vpn.example.com --node-id 7
sudo basaltw headscale-client migrate --url https://vpn.example.com --new-url https://other.example.com --node-id 7
sudo basaltw headscale-client abandon --url https://vpn.example.com --confirm
```

Personal enrollment requires private stdin/stdout/stderr terminals. Never capture
its native instructions in setup logs. Machine enrollment accepts only an existing
private key file and repeated explicit `--approved-tag` values; it never issues a
key. Interrupted enrollment retains its marker and refuses replay. Abandon only
after reconciling/revoking the pending request or issued key remotely. Migration
prepares the new endpoint after exact-node logout; enroll there explicitly later.
It does not rewrite the controller's saved setup selection. After migration and
separate explicit enrollment have completed on the client, reconcile the private
controller selection with the command below. Ordinary setup/patch still reject
replacement endpoints and stable IDs, even following disable.

Every action accepts `--dry-run`, validating public inputs without native calls,
locks or state writes. Keep independent recovery access before disable/leave or
migration. Advertised DNS resolvers retain the native lifecycle teardown guards.

## Controller selection reconciliation

Run `reconcile-selection` on the controller after independently reviewing the
new enrolled node. Use the exact saved host, OLD stable registry ID and OLD
canonical endpoint, plus the NEW saved server reference, canonical endpoint and
native node ID. The old registry record may already have changed or disappeared.
The new reference must resolve to the supplied new endpoint in the selected
workspace. For example (all values below are fictional):

```bash
basaltw headscale-client reconcile-selection client.example.com \
  --workspace /private/example-workspace \
  --expected-server-id 092f1108-5af3-49f1-9c38-434b3d3b355c \
  --expected-url https://old.example.com \
  --server NewNetwork --new-url https://new.example.com --node-id node-new \
  --target-host client.example.com --target-key /private/ssh/client-key \
  --target-known-hosts /private/ssh/known_hosts
```

For an old explicit URL selection without a stable registry ID, omit
`--expected-server-id`. This requires the saved ID to be absent; it cannot
bypass a named selection's identity check. The new endpoint must have a saved
reference, which can be an external server added before reconciliation.

SSH always uses root, explicit descriptor-pinned credentials and strict pinned
host keys; `--target-port` defaults to 22. The target host must match the cached
host (case and a terminal DNS dot are normalized); aliases and alternate recovery
addresses are refused. No cached SSH credentials or management authority are
inferred. Omit `--tag` for a personal client with empty tags; supply repeated
`--tag tag:service` values for the exact expected tagged identity.

The target adapter only observes an owned, enrolled, running, online native
client at the new endpoint and exact node ID. It verifies effective systemd and
running-process privacy plus retained native identity permissions. It never
enrolls, adopts, logs out, issues keys or changes native state. The controller
holds the setup-target lock and rechecks the full cache and new registry record
under setup-cache/registry locks before publication. Concurrent changes, unsafe
or corrupt caches and unconfirmed observations leave the cache untouched.
Only the saved server name, stable ID and endpoint change; unrelated arguments,
run metadata, omitted client enablement and explicit opt-out are preserved.
An opted-out saved client remains opted out even when native enrollment has been
completed independently. `--dry-run` validates local schemas and bindings without
creating state or locks, reading SSH credential files or contacting the target.

The controller API is
`lib.headscale_selection.reconcile_saved_selection(host, *, expected_server_id,
expected_url, server, new_url, node_id, tags, target, workspace=None, dry_run=False)`;
`target` is an explicit `SSHBinding` and `tags=[]` selects personal observation.

## Enrollment APIs

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
node. `resume_client(url, *, expected_node_id)` resumes an enrolled managed
binding with the checks and uncertainty marker described above; setup uses the
same implementation with its persisted reviewed node. No new CLI command is
required. `leave_client(url, *, expected_node_id)` verifies the exact node before
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

Offline mocked tests cover lifecycle and secret handling. The
[qualification record](../testing/headscale.md) records verified Debian startup,
machine enrollment, DNS and direct/relay access, with personal approval, reboot,
revocation and other platform boundaries tracked separately.

Upstream contracts: [official APT packages](https://pkgs.tailscale.com/stable/),
[native up flags and secret-file input](https://github.com/tailscale/tailscale/blob/v1.104.7/cmd/tailscale/cli/up.go),
[typed status](https://github.com/tailscale/tailscale/blob/v1.104.7/ipn/ipnstate/ipnstate.go),
[native preferences](https://github.com/tailscale/tailscale/blob/v1.104.7/ipn/prefs.go),
and [Headscale topology/logging guidance](https://headscale.net/stable/about/faq/).

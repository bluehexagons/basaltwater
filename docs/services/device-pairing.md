# Protected device pairing

Basaltwater can run a small, password-protected enrollment portal for services
that use provider-native device sessions. T3 Code is the first supported
provider. The portal lets a new browser or app obtain its own short-lived,
one-time pairing link without an operator being present in an SSH terminal.

This is deliberately separate from the service being paired:

```text
browser or app
    -> managed HTTPS gateway and Nginx Basic Auth portal
    -> local Basaltwater pairing broker
    -> provider's supported pairing command
    -> one-time administrative link on the primary T3 HTTPS origin
    -> provider-native administrative device session
```

Basic Auth protects the ability to issue a link. T3 still authenticates the
resulting connection, owns the per-device session, and provides session
revocation. Basaltwater does not create a permanent shared T3 token.

This protected portal is for the managed Debian/VM profiles. The `agent_cachyos`
profile does not install the portal or its gateway. For desktop mode, use
the app's connection settings described in the
[CachyOS desktop guide](../workstations/cachyos.md#t3-code-desktop). For the managed web service,
use the native `t3 pair` and `t3 connect link` flows in the
[CachyOS web-service guide](../workstations/cachyos.md#t3-code-web-service-host-locally-or-on-a-trusted-lan).

## Configure it

Prepare an Nginx-compatible password file on the controller. This example
prompts without echo and uses the controller's OpenSSL command to create a
SHA-512 crypt hash:

```bash
read -rsp 'Pairing portal password: ' pairing_password
echo
pairing_hash="$(printf '%s\n' "$pairing_password" | openssl passwd -6 -stdin)"
unset pairing_password
umask 077
printf 'agent-admin:%s\n' "$pairing_hash" > /run/secrets/agent-1-pairing.htpasswd
unset pairing_hash
```

Then include device pairing with the T3 Code web service:

```bash
basaltw setup workstation_dev 192.168.0.41 agent \
  --agent-tool codex --agent-tool opencode \
  --web-interface t3code \
  --web-interface-source 192.168.0.0/24 \
  --device-pairing t3code \
  --device-pairing-auth-file /run/secrets/agent-1-pairing.htpasswd
```

The portal defaults to port `3774`; T3 defaults to `3773`. Override only the
portal with `--device-pairing-port PORT`. The two ports must differ. The portal
inherits the web-interface bind address and source CIDRs, and Basaltwater adds
matching managed UFW rules for both ports.

When T3 Code web setup is installed, Basaltwater also configures the shared
internal HTTPS gateway and publishes the T3 web service and pairing page through
managed HTTPS forwards. Setup prints their URLs. A VM-local CA is used when a
publicly trusted certificate is not available; install that CA once on client
devices before opening the HTTPS URL.

`--interactive` can generate the password file without a pre-existing source.
When T3 Code web setup is selected, choose device enrollment and enter the
portal username and password at the hidden prompts. The password is passed to
OpenSSL over standard input, never as a process argument.

For a one-off setup, the password can instead be supplied directly:

```bash
--device-pairing-password '<portal-password>'
```

This uses the setup username as the portal's Basic Auth username. The password
is hashed locally and is not placed in remote arguments or saved configuration,
but the command-line value may still be visible in shell history or process
inspection. Prefer a password file when that exposure matters.

## Enroll a device

From a permitted device, open the **T3 Code pairing HTTPS endpoint printed by
setup**. The direct listener below is HTTP-only compatibility; do not use
`https://` with it:

```text
http://192.168.0.41:3774/
```

Enter the configured Basic Auth username and password. The endpoint authorizes
this browser and opens T3 automatically, including when an earlier browser
session is no longer accepted. Without JavaScript, use **Open T3 Code**.
The transition uses a same-origin, single-use form nonce and then redirects to
the primary T3 origin with a short-lived credential in the URL fragment. T3
exchanges it for its own browser session; Basic Auth does not replace that
session. Once paired, bookmark the primary T3 HTTPS URL for normal use. Opening
the Basic Auth entry again creates another device session.

For enrollment controls and T3 Connect, open `/devices` on the Basic Auth
endpoint. It retains the manual portal actions:

- **Pair this browser** creates a one-time credential, then shows
  a button that opens T3's pairing page on the primary T3 HTTPS endpoint. The
  explicit second click avoids browser-dependent cross-port redirect behavior.
- **Pair another T3 Code client** displays the short-lived URL so it
  can be copied into a desktop or mobile T3 Code client.

The broker uses T3's supported administrative-session CLI and authenticated
pairing API to issue a five-minute link with the standard client capabilities
plus `access:read`, `access:write`, and `relay:write`. Its temporary two-minute
administrative bearer session is revoked immediately after the pairing link is
created. Pairing URLs are returned only to the authenticated request. They are
not written to setup output, Nginx access logs, the broker journal, provider
configuration, or saved Basaltwater commands.

The original SSH-based path remains available:

```bash
basaltw agent web pair 192.168.0.41 agent
```

It is useful for recovery, for a loopback-only service, or when the pairing
portal was not selected. It uses the same administrative flow. Sessions paired
before this support was installed remain standard sessions; remove and re-pair
the environment when T3 reports that `access:write` is unavailable.

## T3 Connect management

The authenticated `/devices` page hands off to **Open T3 Code** on the primary
T3 origin. Opening this link reuses native authentication; it does not issue a
pairing credential. If the browser needs enrollment, use **Pair this browser**.

T3 owns account linking, relay installation and preferences, and Connect device
approval. In the desktop app, use **Settings → Sharing & access**. A headless
server's initial linking is a host operation: open its integrated T3 terminal
or SSH, run the native command, and follow its prompts:

```bash
t3 connect link --headless
t3 service restart
t3 connect status
```

Wait for active turns to finish before restarting; a service restart interrupts
them. The native command handles relay-install confirmation and authorization.
Inspect the resulting relay status and verify a client connection. To turn
Connect off, run `t3 connect unlink`, then `t3 service restart`.
See upstream [remote access](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/remote-access.md)
and [Connect troubleshooting](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/t3-connect-troubleshooting.md).

### Migration from the portal console

Setup no longer installs the portal's authorization/input/startup forms or its
root restart bridge. Refreshing setup replaces the broker and removes the old
`basaltwater-t3code-connect.path`, `basaltwater-t3code-connect.service` and
`~/.t3/basaltwater-connect-restart` request marker. The retired
`/connect/t3code` GET/POST routes return 404, including with an older provider
configuration.

Finish or cancel any active legacy authorization before refreshing setup; its
process and output were temporary broker state. Use the native command above
to retry an incomplete authorization. Migration preserves T3's saved Connect
preferences, account credentials, device sessions and relay installation. It
does not enable, disable or unlink Connect. If the handoff cannot determine a
valid primary origin, it directs the user to their existing T3 connection
instead of generating a URL.

Direct/private-LAN enrollment, portal Basic Auth, gateway TLS and source
restrictions remain available. Older repository revisions can recreate the
legacy bridge on setup; prefer native controls for recovery and avoid running
both authorization flows at once.

## Credentials, reruns, and removal

`--device-pairing-auth-file` is a controller-local, transient secret source.
It must be a regular non-symlink file, no larger than 64 KiB, and not group- or
world-writable. Each non-empty record must use `username:hash` with a
crypt-style hash such as OpenSSL's `$6$...` output. Unsalted `{SHA}` entries
are rejected.

The target copy is root-owned, group-readable by `www-data`, and stored at:

```text
/etc/basaltwater/device-pairing/htpasswd
```

The uploaded source payload is removed after setup succeeds or fails. The
source path and interactive password are excluded from saved configurations.
A normal rerun reuses the installed target file. To rotate portal credentials,
rerun setup or patch with a new `--device-pairing-auth-file`; Basaltwater
replaces the file and reloads Nginx only after `nginx -t` succeeds.
Reconciliation also retains the last validated primary T3 HTTPS port until the
gateway confirms its current named endpoints, avoiding an HTTP-link window
while the managed routes are refreshed.
Setup also tracks the broker implementation revision and restarts that broker
when its code changes, even if provider and networking settings are unchanged.
A pending restart marker keeps broker configuration and HTTPS-port changes
retryable if service activation fails. Rerun setup after resolving the service
error; unchanged files do not suppress the pending restart. Nginx validation or
reload failures restore the previous site and credentials so the next setup
attempt validates and reloads the requested configuration again.

To remove enrollment from a saved host:

```bash
basaltw patch 192.168.0.41 agent --no-device-pairing
```

This disables and removes the broker service, Nginx site, pairing-port firewall
rule, provider definition, and target Basic Auth file. It does not revoke T3
sessions that devices already obtained. Inspect or revoke those with T3's
native access commands:

```bash
npx t3@latest auth pairing list
npx t3@latest auth session list
npx t3@latest auth --help
```

## Security boundary

The portal can mint access to an environment that owns repositories, terminals,
Git credentials, and coding-agent credentials. Treat the portal password as an
administrator credential and use a unique value per VM.

Basic Auth does not encrypt traffic. The direct HTTP compatibility mode is
intended only for a trusted private network and is always combined with a
private/non-global CIDR allowlist. Anyone who can observe that HTTP traffic can
recover the Basic Auth password and the returned pairing link. Setup publishes
the portal through the managed HTTPS gateway by default; use the printed T3
Code pairing HTTPS endpoint. For a loopback deployment, use the direct HTTP
compatibility listeners through an SSH tunnel, or forward the printed HTTPS
listener port instead:

```bash
ssh -N \
  -L 3773:127.0.0.1:3773 \
  -L 3774:127.0.0.1:3774 \
  agent@192.168.0.41
```

A separately managed trusted HTTPS endpoint is another option, but the portal
constructs T3 links from the request hostname and configured primary T3 port;
the proxy must preserve that externally reachable mapping. The managed pairing
listener trusts forwarded host, protocol, and client-address metadata only from
the loopback HTTPS gateway, then creates links on the primary T3 HTTPS origin.
Direct compatibility requests continue to produce direct HTTP T3 links.

The managed HTTPS forwards preserve the external host and WebSocket upgrade
headers, so the hosted T3 client can connect to the HTTPS T3 URL without a
mixed-content failure. They are limited to the configured private/LAN source
CIDRs by the internal-web policy.

The broker listens only on a local Unix socket shared with Nginx. Nginx limits
the entire portal, including failed Basic Auth checks, to five requests per
minute per source with a small burst. Five authentication failures within ten
minutes also trigger a one-hour Fail2ban ban. The failure-only log contains the
source address, timestamp, and a fixed marker; it never contains the Basic Auth
header or a pairing URL. After authentication, requests use a single-use
same-site form nonce, and the broker independently limits pairing issuance to
five requests per minute per source. Pairing forms accept only enrollment
actions; unknown intents cannot bypass the issuance limit. Provider commands
are fixed in a root-managed configuration, executed without a shell, restricted
to the configured local T3 endpoint, and required to return a link for the
expected T3 origin.

## Files and services

```text
~/.config/systemd/user/t3code.service
~/.config/systemd/user/t3code.service.d/basaltwater.conf
basaltwater-device-pairing.service
/etc/nginx/sites-available/basaltwater-device-pairing
/etc/basaltwater/internal-web/policy.json
/etc/basaltwater/internal-web/forwards.json
/etc/basaltwater/device-pairing/providers.json
/etc/basaltwater/device-pairing/htpasswd
/run/basaltwater-device-pairing/http.sock
```

Check the local components without revealing pairing credentials:

```bash
systemctl --user status t3code.service
sudo systemctl status basaltwater-device-pairing.service
sudo nginx -t
sudo fail2ban-client status basaltwater-device-pairing
sudo ss -lntp | grep -E ':(3773|3774)\b'
curl -I http://BIND_ADDRESS:3774/
```

Replace `BIND_ADDRESS` with the configured bind IP (or `127.0.0.1` for a
loopback/all-address bind). The final `curl` should return `401 Unauthorized`
because it omits Basic Auth.
Do not place a portal password or a one-time pairing URL in diagnostics shared
with other people.

If a previously paired browser shows T3's pairing-key screen, Basic Auth alone
does not establish that its T3 cookie is valid. Check that the browser is using
the same primary hostname and scheme as before; changing origins can hide an
existing cookie. Expired, revoked, or rejected sessions require fresh pairing.
An update is not established as the cause merely because it preceded the
redirect. Reopen the protected entry to recover access, or use the manual
**Pair this browser** action at `/devices`. If this still returns to pairing,
check the browser's authentication request status and whether a session cookie
was accepted; share statuses and origins, never cookie or credential values.

## Provider extension contract

The broker is provider-neutral: it loads root-managed provider records with a
fixed command, a public-base-URL flag, JSON result field names, and the public
service port. Adding another provider requires a setup adapter that validates
its supported CLI, writes a safe provider record, and documents its native
session/revocation behavior. Arbitrary operator-supplied commands are not
accepted.

See [T3 Code interfaces](../agents/t3-code.md) for the complete T3 workflow,
[Agent authentication](../agents/agent-authentication.md) for provider credentials, and
[Git access](../agents/git-access.md) for repository authentication.

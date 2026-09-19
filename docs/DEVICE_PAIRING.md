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
profile intentionally does not install the portal or its gateway; use the
native `t3 pair` and `t3 connect link` flows in the
[CachyOS guide](CACHYOS.md#t3-code-host-locally-or-on-a-trusted-lan).

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

Enter the configured Basic Auth username and password, then choose one of the
portal actions:

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

The authenticated portal includes a **T3 Connect** section. Choose **Start
authorization** to run T3's supported `t3 connect link --headless` flow. The
page cleans up terminal control sequences and displays readable installation
progress and authorization instructions. The known relay-install confirmation
is accepted automatically; use the input only for an authorization code or
other response requested by T3. T3 installs its pinned relay client;
Basaltwater does not download or manage a second relay service.

After authorization, the portal shows a completion message and requests a
restart of the per-user `t3code.service`. T3 then reconciles the saved Connect
preference and starts the managed tunnel. The checkbox represents that desired
startup state; choose **Apply setting** after changing it. Enabling it starts
the headless authorization flow only when Connect is not already enabled,
while clearing it runs `t3 connect unlink`. The relay binary may remain
installed when Connect is disabled, but no tunnel is started.

The active Connect operation is held only in the pairing broker's memory and
expires after fifteen minutes. While it is running, use **Refresh status** to
see new CLI output; the page does not reload automatically while you are
entering a response. Its CLI output is never written to the journal or a
configuration file. A root-owned systemd path trigger accepts only a fixed
request file from the target user's broker; it cannot run arbitrary commands.

## Credentials, reruns, and removal

`--device-pairing-auth-file` is a controller-local, transient secret source.
It must be a regular non-symlink file, no larger than 64 KiB, and not group- or
world-writable. Each non-empty record must use `username:hash` with a
crypt-style hash such as OpenSSL's `$6$...` output. Unsalted `{SHA}` entries
are rejected.

The target copy is root-owned, group-readable by `www-data`, and stored at:

```text
/etc/infra-tools/device-pairing/htpasswd
```

The uploaded source payload is removed after setup succeeds or fails. The
source path and interactive password are excluded from saved configurations.
A normal rerun reuses the installed target file. To rotate portal credentials,
rerun setup or patch with a new `--device-pairing-auth-file`; Basaltwater
replaces the file and reloads Nginx only after `nginx -t` succeeds.
Reconciliation also retains the last validated primary T3 HTTPS port until the
gateway confirms its current named endpoints, avoiding an HTTP-link window
while the managed routes are refreshed.

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
five requests per minute per source. Pairing and Connect forms accept only
their own action types, so an interactive Connect response cannot bypass the
pairing issuance limit. Provider commands are fixed in a root-managed
configuration, executed without a shell, restricted to the configured local
T3 endpoint, and required to return a link for the expected T3 origin.

## Files and services

```text
~/.config/systemd/user/t3code.service
~/.config/systemd/user/t3code.service.d/infra-tools.conf
infra-tools-device-pairing.service
infra-tools-t3code-connect.path
infra-tools-t3code-connect.service
/etc/nginx/sites-available/infra-tools-device-pairing
/etc/infra-tools/internal-web/policy.json
/etc/infra-tools/internal-web/forwards.json
/etc/infra-tools/device-pairing/providers.json
/etc/infra-tools/device-pairing/htpasswd
/run/infra-tools-device-pairing/http.sock
```

Check the local components without revealing pairing credentials:

```bash
systemctl --user status t3code.service
sudo systemctl status infra-tools-device-pairing.service
sudo nginx -t
sudo fail2ban-client status infra-tools-device-pairing
sudo ss -lntp | grep -E ':(3773|3774)\b'
curl -I http://BIND_ADDRESS:3774/
```

Replace `BIND_ADDRESS` with the configured bind IP (or `127.0.0.1` for a
loopback/all-address bind). The final `curl` should return `401 Unauthorized`
because it omits Basic Auth.
Do not place a portal password or a one-time pairing URL in diagnostics shared
with other people.

## Provider extension contract

The broker is provider-neutral: it loads root-managed provider records with a
fixed command, a public-base-URL flag, JSON result field names, and the public
service port. Adding another provider requires a setup adapter that validates
its supported CLI, writes a safe provider record, and documents its native
session/revocation behavior. Arbitrary operator-supplied commands are not
accepted.

See [T3 Code interfaces](T3_CODE.md) for the complete T3 workflow,
[Agent authentication](AGENT_AUTHENTICATION.md) for provider credentials, and
[Git access](GIT_ACCESS.md) for repository authentication.

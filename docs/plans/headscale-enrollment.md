# Headscale enrollment and onboarding security

Status: proposed implementation contract for the [Headscale plan](headscale.md).
These enrollment commands and onboarding services are not implemented yet.

For provider choices and the proposed Pocket ID qualification, see the
[self-hosted SSO assessment](headscale-sso.md). Existing OIDC providers remain
supported by the design; hosting a provider is optional follow-up scope.

## Public instructions and private administration

Default to a static HTTPS onboarding site at `join.example.com`, served by Nginx
on the Headscale host with read-only content. Use a separate origin from
`vpn.example.com`, the Headscale control endpoint. Allow LAN/source-restricted
hosting or a separate Basaltwater web host, including for externally managed
Headscale servers. Print a page URL reachable by the intended new devices before
enrollment; explain that LAN-only instructions cannot serve remote users.

The site provides official app links and platform-specific instructions for
Windows, macOS, Linux, iOS and Android. Generate commands from validated, trusted
configuration and escape displayed values. Its QR code contains only the page
URL. Public content contains no enrollment tokens, internal service inventory,
device lists or administrative actions. Personal devices need no Basaltwater
installation. An optional page login may restrict instruction visibility but
does not authorize network enrollment.

Serve local assets without third-party scripts, renew certificates automatically,
redirect the onboarding site's HTTP traffic to HTTPS and scope HSTS to that
hostname. Apply a restrictive CSP, prohibit framing, set `nosniff` and a
restrictive referrer policy. Give the static site no Headscale socket, database,
credential or root access. Keep its request limits separate from Headscale's
long-lived control connections. Sanitize proxy headers and keep authentication
codes, tokens and pending approval identifiers out of proxy/application logs.
Do not place a browser login gate in front of native client protocol endpoints.

Initially administer enrollment through authenticated SSH and Headscale's local
Unix socket. Registering a named external endpoint conveys no administrative
authority; configure management access separately or let its administrator
approve devices. A future web approval service needs its own authenticated,
authorized backend, CSRF defenses and audit records. The public static site
cannot issue keys or approve devices, and T3 pairing credentials grant no access.

## Enrollment setup

1. **Choose the network and policy.** Resolve the saved server name to its
   stable record and HTTPS endpoint. Establish recovery access and a restrictive
   Headscale policy before admitting clients; grant only intended service access
   and explicitly authorize service-tag ownership. Select personal-device SSO
   or administrator approval explicitly. Never fall back to open enrollment
   after an authentication failure.
2. **Provision personal-device SSO when available.** Use an existing OIDC
   provider. Register a confidential client with the exact Headscale callback
   `https://vpn.example.com/oidc/callback`; configure issuer, client ID and a
   private service-readable secret through the supported secret-file mechanism
   for the pinned release. Enable PKCE S256 and enforce MFA at the provider.
   Require an explicit allowed-user list or allowed-group filter; keep verified
   email requirements where email is used. Validate needed claims with an
   allowed and a denied test account before publishing onboarding instructions.
3. **Keep identity and access separate.** Let native OIDC enrollment create or
   resolve the user's identity. Bind network policy to stable provider identities
   using policy groups where useful; do not infer trust from a device name or
   user-editable display name. OIDC group claims filter enrollment and are not
   directly usable as Headscale policy groups. Provider or authentication-mode
   changes require an explicit identity migration plan.
4. **Offer native administrator approval without SSO.** Create a Headscale user
   per person. The user installs the official client, selects the custom server
   and initiates browser registration. Through an authenticated channel, verify
   the intended person and exact pending authentication ID before approving that
   request for that user with Headscale's CLI. Hostname alone is insufficient.
   Expired or failed requests restart native registration; do not distribute a
   shared reusable key or invent a separate invitation-token system.
5. **Automate managed machines separately.** After target preflight, an explicitly
   authorized administrator/controller issues a short-lived, single-use
   preauthenticated key carrying only the approved service tags. Deliver it over
   authenticated private transport and use the client's supported secret-file or
   equivalent private input mechanism. Keep secret values out of argv, URLs,
   setup serialization, logs and artifacts; remove temporary secret files after
   use. Preserve the client's durable identity with appropriate permissions.
   On uncertain completion, inspect enrollment before issuing another key.
6. **Verify and manage the lifecycle.** Check the enrolled node's server,
   identity, tags and intended access, including a denied-service probe. Ordinary
   reruns preserve valid enrollment. Set and document device reauthentication
   policy separately from key expiry. Offboarding blocks future enrollment and
   explicitly revokes/removes existing nodes and affected access grants. Revoking
   a bootstrap key or changing an IdP group alone is not verified node removal;
   confirm loss of access and retain an independent administrative recovery path.

## Acceptance

- Public or source-restricted instructions are reachable before enrollment and
  expose only intended content; the static site cannot read administrative state.
- SSO permits only configured identities; denied/missing claims, failed MFA and
  expired/replayed callbacks do not register devices. Administrator approval
  binds the exact pending request to the verified intended user.
- Both personal-device modes preserve user ownership; machine keys cannot assign
  unauthorized tags. Single-use, expiry and uncertain-completion paths preserve
  identity and reveal no credentials in process arguments, logs or saved state.
- Allowed services work and denied services stay blocked. Revocation is verified
  against a connected client, including after restart; recovery access survives.
- Qualify each advertised desktop/mobile flow on its actual platform. Test native
  protocol connectivity with the site's security settings enabled. Record
  sanitized results and remaining gaps under the main plan's coverage contract.

## References

- [Headscale registration and personal/tagged identities](https://headscale.net/stable/ref/registration/)
- [OIDC configuration, identity policy and limitations](https://headscale.net/stable/ref/oidc/)
- [Native registration walkthrough](https://headscale.net/stable/usage/getting-started/)
- [Reverse-proxy protocol requirements](https://headscale.net/stable/ref/integration/reverse-proxy/)
- [OWASP HTTP headers](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
- [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)

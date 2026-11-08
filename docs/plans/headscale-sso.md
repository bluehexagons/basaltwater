# Self-hosted SSO for Headscale

Status: deferred research; the current decision is to use Headscale's native
administrator approval and machine keys. OIDC integration, managed provider
qualification and custom SSO are outside the initial delivery. No provider has
been deployed or qualified by this work. Supports the [Headscale plan](headscale.md) and
[enrollment contract](headscale-enrollment.md). Findings use upstream
documentation reviewed on 2026-11-08; recheck the chosen release before coding.

## Recommendation and Basaltwater fit

If SSO work is reopened, keep Headscale's OIDC interface provider-independent and
support existing identity providers first. Consider qualifying **Pocket ID** as
the first optional managed provider for small deployments using passkeys.
Prefer **Authelia** when operators
want file/LDAP-backed accounts and configuration-managed authentication; evaluate
**Authentik** for richer administration and application integration. Continue to
offer Headscale's native administrator-approved registration without an IdP.
Optional provider hosting remains separate from the initial Headscale milestone.

This is an architectural fit assessment, not a security ranking or measured
performance comparison. Basaltwater already manages native services with pinned,
verified releases, systemd, Nginx and retained private state. Favor a small
service footprint, usable first-account/recovery flows and documented automation.
Keep identity available before tailnet enrollment and preserve independent SSH
recovery. The project's small-machine development target does not establish an
SSO hosting capacity guarantee; measure the complete proposed service allocation.

## Candidates

| Provider | Deployment and administration | Fit and tradeoff |
| --- | --- | --- |
| Pocket ID | Upstream native binary or container; SQLite by default; user/group/client UI and REST API; passkey sign-in. | First qualification candidate for a focused OIDC service. Test first-passkey setup, cross-device sign-in and recovery on actual clients. API credentials are administrative, not narrowly scoped enrollment grants. |
| Authelia | Native packages/binaries; file or LDAP accounts; SQLite and in-memory sessions permit a single-instance deployment. | Strong configuration-managed option, with a documented Headscale integration. File/LDAP account provisioning is less convenient for routine user administration than Pocket ID's built-in account UI. Redis/external SQL are choices for other topologies, not assumed prerequisites. |
| Authentik | Standard Compose deployment includes server, worker and PostgreSQL; administrative UI; declarative blueprints. | Consider for broader identity/application requirements. Adds a container stack and database lifecycle to the service. Remove the default Docker-socket mount when outpost management is unnecessary. |
| Kanidm | Passkeys and OIDC plus broader identity management; upstream prefers containers, with community native builds. | Revisit for a wider directory/system-identity project. Its CPU requirements and native packaging need explicit target qualification; avoid adopting the broader identity scope solely for Headscale. |
| Keycloak | JVM service with production database/configuration requirements; extensive federation, protocols and administrative controls. | Useful with an existing deployment or a concrete federation requirement. More identity infrastructure than this initial enrollment feature needs. |

Deployment sources: [Pocket ID](https://pocket-id.org/docs/setup/installation),
[Authelia native deployment](https://www.authelia.com/integration/deployment/bare-metal/),
[Authelia accounts](https://www.authelia.com/configuration/first-factor/file/),
[Authelia sessions](https://www.authelia.com/configuration/session/introduction/),
[Authentik installation](https://docs.goauthentik.io/install-config/install/docker-compose/),
[Authentik blueprints](https://docs.goauthentik.io/customize/blueprints),
[Kanidm deployment](https://kanidm.github.io/kanidm/stable/preparing_for_your_deployment.html),
[Kanidm features](https://kanidm.github.io/), and
[Keycloak administration](https://www.keycloak.org/docs/latest/server_admin/).

Headscale documents Authelia, Authentik, Kanidm and Keycloak as known working
providers in its [OIDC reference](https://headscale.net/stable/ref/oidc/).
Pocket ID publishes its own [Headscale integration](https://pocket-id.org/docs/client-examples/headscale).
These are integration evidence, not Basaltwater qualification.

Upstream main-software licenses are
[BSD-2-Clause for Pocket ID](https://github.com/pocket-id/pocket-id/blob/main/LICENSE),
[Apache-2.0 for Authelia](https://github.com/authelia/authelia/blob/master/LICENSE),
[MPL-2.0 for Kanidm](https://github.com/kanidm/kanidm/blob/master/LICENSE.md), and
[Apache-2.0 for Keycloak](https://github.com/keycloak/keycloak/blob/main/LICENSE.txt).
[Authentik's license file](https://github.com/goauthentik/authentik/blob/main/LICENSE)
identifies MIT core code and separate enterprise/documentation terms. Review the
exact bundled artifacts and dependencies before any distribution integration.

## Deferred Pocket ID qualification design

1. **Install independently.** Pin and verify an upstream binary, run a dedicated
   systemd service behind Nginx and keep its database on local storage. Use a
   stable public HTTPS origin such as `id.example.com`; passkeys bind to its
   domain. A host replacement should retain that origin, issuer and identities.
   Source: [deployment](https://pocket-id.org/docs/setup/installation) and
   [configuration](https://pocket-id.org/docs/configuration/environment-variables).
2. **Protect first setup.** Restrict access to initial setup until the intended
   administrator creates an account and passkey. Keep open signup disabled.
   Require user verification for passkeys, establish a second administrator
   recovery authenticator and leave email login fallback off unless explicitly
   selected. Disable analytics and remote icon loading for the managed default;
   reject insecure remote callback URLs. Sources:
   [sign-in settings](https://pocket-id.org/docs/guides/sign-in-methods) and
   [configuration](https://pocket-id.org/docs/configuration/environment-variables).
3. **Configure Headscale access.** Use a confidential OIDC client with an exact
   HTTPS callback, PKCE S256 and a dedicated allowed-user group. Configure the
   provider's client restriction and Headscale's enrollment filter; keep actual
   network grants separate. Preserve stable issuer/subject identities. Source:
   [Headscale integration](https://pocket-id.org/docs/client-examples/headscale).
4. **Bootstrap users with native flows.** Create each intended account and assign
   its allowed group. Privately deliver a short-lived provider-generated login
   code so the person can register their first passkey, then use normal Headscale
   OIDC login. Encourage a recovery passkey; test phone-assisted sign-in for a
   device without its own passkey. Provider login codes authenticate an account;
   they are distinct from Headscale machine keys and the public instructions QR.
   Sources: [users](https://pocket-id.org/docs/setup/user-management) and
   [sign-in methods](https://pocket-id.org/docs/guides/sign-in-methods).
5. **Bound automation.** Start with the native administrative UI. An optional
   controller adapter may reconcile users, groups and the OIDC client through
   documented APIs, preserving externally managed accounts. API keys inherit
   administrator authority; keep them in private credential transport/state and
   out of the onboarding site, clients, saved commands and logs. Avoid a permanent
   static admin key by default. Source: [REST API](https://pocket-id.org/docs/api).
6. **Qualify recovery before adoption.** Back up a consistent database/data set,
   configuration and encryption key; retain the matching release and public
   origin. Verify restored passkeys, user subjects, OIDC clients and signing
   material. Upstream's [export/import](https://pocket-id.org/docs/setup/data-export-import)
   is marked experimental, so it must not be the only recovery path. Verify
   [lost-passkey recovery](https://pocket-id.org/docs/troubleshooting/account-recovery)
   through private administrative access. Review restored accounts and nodes
   against later revocations before restoring normal access.

Test fresh bootstrap, failed/replayed account codes, denied enrollment groups,
device reauthentication, offboarding, restart/reboot, upgrade and replacement-host
restore. Exercise the advertised desktop/mobile clients, including their login
browser and passkey handling. Measure idle and enrollment-load resource use on
the intended allocation; no RAM or throughput claim follows from documentation.
Do not switch a running network's provider merely to adopt the managed default.

## Custom implementation boundary

A small custom identity provider would still own OIDC discovery, authorization
and token endpoints, client/callback validation, PKCE, stable subjects, signing
keys and rotation, sessions, credentials, account recovery and security updates.
Libraries help with protocol pieces; Basaltwater would retain responsibility for
the assembled service and its authentication decisions. See
[OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html) and
[OAuth security BCP](https://www.rfc-editor.org/rfc/rfc9700.html).

Use custom Basaltwater code for installation, validated configuration, named
references, static instructions and bounded adapters to upstream administration.
Keep identity protocols and credential handling in a maintained provider. If a
deployment needs only occasional approved devices, native Headscale registration
is the minimal solution and requires no custom SSO service.

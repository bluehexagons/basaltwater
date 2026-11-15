# Static Headscale onboarding

`web.headscale_onboarding_steps.configure_headscale_onboarding(config)` serves
read-only device instructions at the separate HTTPS origin configured by
`headscale_onboarding_domain`. The control URL comes from the hosted
`headscale_domain` when hosting is selected (even when this host joins another
network), otherwise an explicitly resolved `tailscale_login_server`; an
external endpoint does not grant Basaltwater administrative authority.

The page links only to official Tailscale downloads and documents native client
selection plus administrator approval. Approval admits a device to the network's
shared trust boundary. Device expiry is independent of the one-hour lifetime of
an optional machine key. The QR contains only the page URL. No login, keys,
approval form, node list, or Headscale runtime access is provided. Personal
devices do not need Basaltwater installed.

The site uses local CSS, a restrictive CSP, frame denial, `nosniff`, no-referrer,
hostname-scoped HSTS, HTTP redirection and Certbot-managed TLS. Its Nginx access
and error logs are disabled for this vhost, including HTTP and error responses.
The dedicated request-limit zone never applies to the control vhost. Only the
page, local stylesheet and QR are served; all other HTTPS paths return 404.
Onboarding routing is public unless `effective_access_sources()` supplies
explicit validated IP/CIDR restrictions, enforced independently in this vhost;
printing a LAN URL does not make it reachable off that LAN. Request limiting
must remain independent of Headscale's long-lived control connections.

Current instruction references: [Headscale registration](https://headscale.net/stable/ref/registration/),
[Tailscale custom coordination server](https://tailscale.com/docs/how-to/set-up-custom-coordination-server),
and [Tailscale logging](https://tailscale.com/docs/features/logging). The
page includes separate Windows, macOS, Linux, iOS and Android instructions based
on the official [Windows](https://headscale.net/stable/usage/connect/windows/),
[Apple](https://headscale.net/stable/usage/connect/apple/) and
[Android](https://headscale.net/stable/usage/connect/android/) guides.
Windows uses `TS_NO_LOGS_NO_SUPPORT=true` in `tailscaled-env.txt`; Linux uses
the daemon's service environment. The macOS daemon flag applies only to the
open-source daemon variant, not the Standalone or App Store apps. Upstream
documents no equivalent startup daemon flag for mobile apps. The page makes no
promise of total third-party traffic isolation. Disabling diagnostic upload
reduces support capabilities.

The capability builder installs Nginx and Certbot before onboarding and keeps
the existing native-service order. Explicit onboarding with external resolved
endpoint metadata and omitted native capabilities composes only the static web
steps; it requires neither TUN nor network enrollment. The shared configuration
validator currently infers client selection from `tailscale_login_server`, so
callers supplying discovery-only metadata must retain omitted client intent at
the configuration boundary; this installer never selects or enrolls a client.
Both omitted onboarding and dry-run publication perform no package, QR, lock,
file or service operations.

Publication checks the actual `nginx -T` dump for exact/wildcard hostname and
rate-zone collisions (unrelated regex names conservatively require review).
Root-owned ancestors, content files and the exact enabled symlink are checked
before replacement. The private ownership manifest records content hashes;
unknown installations, operator edits, unsafe paths, extra assets and hostname
changes require explicit review/adoption. An old installation without this
manifest is deliberately refused. No automatic adoption is attempted.

Under `/etc/nginx/.basaltwater-nginx-operation.json` ownership, publication
persists a mode-0600 snapshot at
`/var/lib/basaltwater/headscale-onboarding-recovery.json` before changing the
site, assets or enabled link. It records original bytes, permissions, ownership,
link presence and Nginx activity. Each HTTP/TLS candidate passes `nginx -t`
before activation. Certbot uses the existing webroot certificate helper and
renewal hook; it never rewrites unrelated vhosts or global Nginx configuration.
ACME challenges remain public even when instructions are restricted.

Failure restores the complete managed site/content/link, previous Nginx
activity, renewal hook and Certbot timer activity/enablement, validating the
restored configuration before reload. Failed or
uncertain restoration retains both the blocking operation marker and snapshot.
Do not delete either to retry: inspect them privately, restore the recorded
files and service state, validate Nginx and verify the working site before
resolving the guard under the
[transaction recovery procedure](../operations/transaction-recovery.md).
Certificate issuance and system package installation are retained across a
failed publication; these external effects are not undone. Existing certificate
renewal infrastructure is shared with other sites.

Live qualification remains outstanding for Windows, macOS, Linux, iOS and
Android custom-server selection, startup logging opt-out, native protocol
connectivity through the site's headers and rate controls, LAN-restricted
routing, Certbot renewal, and rollback on each supported Debian profile. Mocked
tests and dry runs do not establish those platform results.

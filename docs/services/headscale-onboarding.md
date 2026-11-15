# Static Headscale onboarding

`web.headscale_onboarding_steps.configure_headscale_onboarding(config)` serves
read-only device instructions at the separate HTTPS origin configured by
`headscale_onboarding_domain`. The control URL comes from the hosted
`headscale_domain` or an explicitly resolved `tailscale_login_server`; an
external endpoint does not grant Basaltwater administrative authority.

The page links only to official Tailscale downloads and documents native client
selection plus administrator approval. Approval admits a device to the network's
shared trust boundary. Device expiry is independent of the one-hour lifetime of
an optional machine key. The QR contains only the page URL. No login, keys,
approval form, node list, or Headscale runtime access is provided. Personal
devices do not need Basaltwater installed.

The site uses local CSS, a restrictive CSP, frame denial, `nosniff`, no-referrer,
hostname-scoped HSTS, HTTP redirection and Certbot-managed TLS. Its Nginx access
log is disabled. Onboarding routing is public unless the operator applies an
explicit source restriction using the host's validated access-source policy;
printing a LAN URL does not make it reachable off that LAN. Request limiting
must remain independent of Headscale's long-lived control connections.

Current instruction references: [Headscale registration](https://headscale.net/stable/ref/registration/),
[Tailscale custom coordination server](https://tailscale.com/docs/how-to/set-up-custom-coordination-server),
and [Tailscale logging](https://tailscale.com/docs/features/logging). The
provided native logging opt-out guidance requires per-platform qualification;
the page calls out that app variants differ.

Live qualification remains outstanding for Windows, macOS, Linux, iOS and
Android custom-server selection, startup logging opt-out, native protocol
connectivity through the site's headers and rate controls, LAN-restricted
routing, Certbot renewal, and rollback on each supported Debian profile. Mocked
tests and dry runs do not establish those platform results.

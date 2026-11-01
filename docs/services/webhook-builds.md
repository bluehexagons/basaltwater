# Signed GitHub webhook builds

Enable `--build-server --webhook-builds` and repeat
`--webhook-repository OWNER/REPO` for the allowed repositories.
Repeat `--webhook-branch BRANCH` for accepted branches (default `main`).
Set `--webhook-domain builds.example.com --ssl`.
The default enrollment contains one controller principal, which webhook setup
uses for core policy checks. Custom enrollments must currently contain exactly
one principal when webhooks are enabled.
Optional `--webhook-secret SECRET` accepts 32–512 characters; when omitted,
setup generates and retains a root-protected secret.
Optional `--webhook-github-token TOKEN` is a private-fetch credential held
only by the source broker. Transient secrets are excluded from saved cache
and SSH argv transport.

Configure GitHub's webhook URL as
`https://builds.example.com/basaltwater-builds/github`, content type
`application/json`, HMAC SHA-256 signing with the retained secret, push events
only, and TLS verification enabled. Only signed pushes from the repository
and branch allowlists are accepted. The exact signed commit is pinned.

No Actions workflow is needed. A root `basaltwater.json` declares literal argv
commands in `ci.install`, `ci.build` and `ci.test`, or uses deployment
component build commands. Use `components: []` for CI-only projects and
`build_runtimes[]` to declare required runtimes. Unsupported build systems
are not inferred. Publishing metadata may still be parsed by the schema;
publishing execution is unimplemented.

Webhooks use the same core policy, input store, worker, scratch space, limits,
logs and artifacts as SSH builds. They produce build/test artifacts only:
automatic target deployment, status checks and release publishing are absent.

Direct HTTPS needs public DNS and reachable ports 80/443 without generic
access-source web restrictions. Alternatively add `--cloudflare`: setup uses
the existing shared tunnel provisioning and activation, with an HTTP origin
and no ACME request. A LAN builder can publish through the tunnel; first-time
Cloudflare account authentication and DNS configuration are still required.
See [Cloudflare](cloudflare.md) and [architecture, capacity and migration](build-systems.md).

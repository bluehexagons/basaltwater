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

Webhook nginx sites have separate `webhook_` ownership. Ordinary application
reconciliation preserves them, including when `--deploy` runs in the same setup.
They still use the shared nginx generator, certificate installation and tunnel
discovery. Disabling webhooks removes their owned site and services while
retaining receipts, input evidence and core jobs. Setup migrates the previous
unprefixed generated webhook proxy through the same nginx transaction.

The receiver retains at most 128 signed pushes. Admission independently retains
at most 128 receipts; settled receipts require explicit archival. Preparation
pins the signed commit and retains one bounded source snapshot with its exact
core request, runtimes and source size before submission. Confirmed input or
queue capacity refusals become `retry_pending`: admission retries those same
bytes and IDs after capacity returns, without fetching the branch again or
regenerating the request. Pending inbox entries remain until submission succeeds.
Prepared snapshots count against the receipt bound and the per-source snapshot
size limit; they are additional storage outside the core input reservation.

Uncertain preparation, input writes, filesystem sync or response loss require
inspection. Their inbox entries and receipts remain and the service does not
automatically retry them. After verifying cleanup and retained core evidence,
a root-local operator can call `lib.build_webhook_service.admit_event` with the
retained signed envelope, current settings/configuration and `recover=True`
under the ordinary `host_slot`. This permits one exact core replay only when
preparation completed; core admission verifies source/request identities and
returns an existing job after response loss. It cannot recreate uncertain
preparation or overwrite partial core inputs. Source cleanup uncertainty still
propagates through the capacity lease for explicit recovery. Preserve the
receipt, prepared source, inbox entry and core evidence together until resolved.

Malformed envelopes and abandoned atomic-write files are handled individually,
without quarantine accumulation. Receiver publication and intake cleanup share
a directory lock so cleanup cannot discard an active receiver write. Each intake
pass handles a bounded batch; invalid entries do not prevent valid pushes in
that batch from being admitted.

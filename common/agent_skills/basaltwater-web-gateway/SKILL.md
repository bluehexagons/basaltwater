---
name: basaltwater-web-gateway
description: Publish static sites or expose loopback web services through a Basaltwater-managed HTTPS gateway.
metadata:
  managed-by: basaltwater
---

# Managed HTTPS gateway

Use this workflow only when `command -v basaltwater-web` succeeds. If it is absent,
report that the managed gateway was not provisioned; do not replace it with
ad hoc Nginx or firewall changes. Never bind a development service to
`0.0.0.0` or disable TLS verification.

## Choose the hosting mode

- For a built static site, use `basaltwater-web publish site`. It builds common
  JavaScript projects, atomically publishes their output on the shared HTTPS
  origin, and does not require a long-running process or dedicated port.
- For a Godot web export, use `basaltwater-web publish godot` and the
  `basaltwater-godot-web` skill. Nginx serves all static games on the shared
  HTTPS port 8443; static games do not need dedicated ports.
- For an explicitly requested live project preview, use `sudo basaltwater-web
  preview start NAME --project PATH`. It supervises the process as the
  requesting user, allocates its loopback and HTTPS ports, waits for readiness,
  and rolls back on failure.
- For a live HTTP or WebSocket service already owned by another service
  manager, bind it to `127.0.0.1` or `::1` on an unprivileged port, then
  register a low-level managed forward.

## Static sites

Publication copies the detected output tree into a separate managed site
directory and atomically replaces the previous snapshot. The published site is
not a symlink to the checkout or its `dist` directory: editing, committing, or
running a build does not refresh an existing publication. When the requested
outcome includes the hosted site, publish again after the final source change
and build. A healthy pre-existing route can still be serving an older snapshot.

From a Vite or similar project with a build script:

```bash
basaltwater-web publish site
basaltwater-web site doctor NAME
basaltwater-web site url NAME
```

Use `--project`, `--output`, or `--no-build` when auto-detection is not enough.
Published sites are available beneath `/sites/USERNAME/NAME/` on shared HTTPS
port 8443. Static applications must therefore use relative assets or a build
base that includes that prefix; root-relative `/assets/...` URLs point outside
the site.

When `node_modules` is absent, publication installs dependencies before the
build. An npm project without a lockfile uses `npm install`, which can create a
`package-lock.json`; inspect Git status before and after publishing and do not
silently commit or discard that file. Use `--no-install` only when dependencies
are already usable.

`site doctor` proves that the managed directory exists and its root HTTPS URL
responds successfully. It does not compare the publication with the checkout,
inspect client-side routes, or prove that JavaScript rendered. After updating a
hosted site, verify at least one changed public artifact against the build:

```bash
set -o pipefail
published_url="$(basaltwater-web site url NAME)"
curl --fail --silent --show-error "${published_url}PATH" | sha256sum
sha256sum OUTPUT/PATH
```

Require the download pipeline to succeed before comparing hashes; `pipefail`
keeps a failed HTTP request from being hidden by a successful `sha256sum`.
Replace `PATH` with a non-sensitive changed file and `OUTPUT` with the detected
build directory. Never add `-k`. A URL fragment such as `#/README.md` is used
only by the browser and is not sent to Nginx; verify the underlying file URL or
the site root separately, then use the capability-specific browser skill
installed on the VM for rendered behavior. Client certificate enrollment is
optional; route to managed Playwright or skip the collaborative browser layer
when the connected T3 client does not trust the VM CA.

Remove a publication only when explicitly requested, using `basaltwater-web site
remove NAME --yes`.

## Managed live previews

For Vite, project and command detection is automatic:

```bash
sudo basaltwater-web preview start NAME --project .
```

For another server, pass an argv command after `--`; `{host}` and `{port}` are
replaced without shell evaluation:

```bash
sudo basaltwater-web preview start NAME --project . -- \
  ./server --host '{host}' --port '{port}'
```

Use `basaltwater-web preview list`, `basaltwater-web preview logs NAME`, and `basaltwater-web
doctor NAME` for inspection. Stop a preview only when explicitly requested:

```bash
sudo basaltwater-web preview stop NAME
```

## Live forwards

Create a forward after its loopback service is ready:

```bash
sudo basaltwater-web forward add NAME --listen auto --to 127.0.0.1:PORT
```

Add `--profile godot` for a Godot preview that needs secure-context and
cross-origin isolation headers. If the upstream has a documented request-body
limit above Nginx's 1 MiB default, add a bounded limit such as
`--max-body-size 50m`. The command chooses a permitted HTTPS port, applies the
VM's existing access-source policy, validates Nginx, reconciles UFW, and prints
the URL.

Treat the two ports as distinct: `--to` is the private loopback HTTP upstream;
`--listen` is the externally reachable Nginx HTTPS port. Only the Nginx
listener receives a managed UFW rule. Use the URL printed by `basaltwater-web`
instead of substituting the upstream port or scheme.

Use `basaltwater-web forward list`, `basaltwater-web doctor NAME`, and `basaltwater-web ca` for
inspection. Remove a route when the associated service is no longer intended
to be reachable:

```bash
sudo basaltwater-web forward remove NAME
```

Do not proxy databases, SSH, metadata endpoints, or another user's service.
Treat adding or removing a forward as an external exposure change and keep it
within the user's requested scope.

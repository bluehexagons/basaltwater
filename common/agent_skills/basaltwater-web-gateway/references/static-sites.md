# Static sites

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
basaltwater-web site verify NAME --project . --json
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

`site doctor` checks directory and root HTTP readiness. For freshness, use
`site verify NAME --project PATH --output dist --json`: it compares the private
receipt with the publication, local build inventory, and served HTTPS bytes.
Publication stages the receipt with the snapshot, recording the observed source
commit, dirty state, build mode, and artifact hashes. The mode-`0600` receipt is
not public site metadata. `--local-only` omits HTTPS coverage.

Verification changes no source, build, or publication. A missing legacy receipt
requires an authorized publication to create one; do not republish automatically
for a read-only check. Dirty or unavailable Git state leaves source provenance
unverified even when artifact bytes match. `--no-build` does not establish that
the output was built from the recorded source. Verification does not execute
JavaScript, follow client-side routes, or inspect service-worker caches.

For an older gateway without the verifier, compare a changed public artifact:

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
optional. Follow the active session's browser policy: use managed Playwright only
when the installed browser skill permits fallback, or report the collaborative
coverage gap when the connected T3 client does not trust the VM CA.

Remove a publication only when explicitly requested, using `basaltwater-web site
remove NAME --yes`.

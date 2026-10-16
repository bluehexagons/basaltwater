# Browser compatibility checks

The local application suite qualifies Basaltwater's panel, device pairing, and
privilege approval handlers in pinned Playwright Chromium and Firefox. Chromium
remains the primary automation baseline. A separate integration case exercises
the Guacamole client/session boundary with its reviewed JavaScript and a
disposable protocol service. Native Debian Firefox ESR has a separate release
check below. None of these results asserts compatibility for arbitrary project
sites, T3 Code, Gogs, HomeBox, or other upstream application UIs.

## Develop the web panel locally

Activate the repository's development environment and start the panel directly
from your checkout as a regular development account:

```bash
source .venv/bin/activate
make panel-dev
make panel-dev PANEL_ARGS="--scenario degraded --port 8766 --open"
```

The command prints the loopback URL (normally `http://127.0.0.1:8765`), a
disposable Basic Auth login, and its private temporary state directory. It needs
no panel installation, Nginx, systemd services, or installed agent tools. Use
`--port 0` to select an available port. It never binds to a network interface
other than loopback. Stop with Ctrl-C to shut down the worker and remove the
session's data and credentials.

The production page renderers, routes, form parsing, validation, CSRF checks,
task store and inbox are used. Host observations and mutations are replaced by
development adapters: no real commands, agents, updates, approvals or provider
requests run. Saved Git author fields and token-presence flags belong to the
temporary fixture; submitted tokens are discarded. Do not enter real secrets.
Publishing renders an empty fixture and rejects provider/file operations. Simulated task
runs show success in normal scenarios and failure in `degraded`. Their workspace
preparation and evidence are simulated too; this does not qualify real execution.

Choose a scenario through `--scenario` or the development controls above each
page:

- `full` (default): optional panel pages, an example task and an unread message.
- `minimal`: core inspection pages with optional tools absent.
- `empty`: optional pages and forms with no saved tasks, messages or job records.
- `degraded`: high resource use, collector notices and simulated operation failures.

Switching scenarios or selecting **Reset sample data** clears the session's
temporary account and seeds the selected scenario again. Ordinary source reloads
preserve saved drafts, inbox changes and simulated account settings. The listener,
URL and login stay unchanged. Python changes under `common/service_tools`, `lib`
and `scripts` restart the worker in a fresh interpreter. `--watch PATH` adds a
file or directory; additions and deletions are detected. After a reload, the
browser refreshes automatically unless an input has been edited; then it offers
a reload link and preserves the unsaved form. `--no-refresh` disables browser
refresh and `--no-reload` disables source watching. A worker syntax/startup error
is printed in the terminal; after a reload failure, editing the file again retries
startup.

HTTP keeps everyday layout/form work simple. HTTPS-only credential and publishing
forms retain their transport restrictions. For transport-sensitive checks:

```bash
make panel-dev PANEL_ARGS="--https"
```

This mode requires OpenSSL and the development extra. It prints a session CA path
and SHA-256 fingerprint. Verify that fingerprint locally and import the CA only
into a disposable browser profile using the procedure below. Never bypass TLS
verification or add this CA to the host trust store. The certificate and login
survive source reloads; all CA material is removed when the development session
ends. The same TLS option is available for pairing and approval development.

### Specialized panels

Select a first-party surface with the same command:

```bash
make panel-dev PANEL_ARGS="--surface connect"
make panel-dev PANEL_ARGS="--surface pairing --https"
make panel-dev PANEL_ARGS="--surface approval"
make panel-dev PANEL_ARGS="--surface desktop"
make panel-dev PANEL_ARGS="--surface console --port 8766"
```

The printed URL opens the selected page. Pairing and Connect share the production
portal: `/devices` shows enrollment and relay controls, `/connect/t3code` shows
Connect, and `/` exercises automatic browser enrollment. Links end at a synthetic
local destination. Start authorization, enter any sample response, refresh status,
and toggle startup through the real nonce-protected forms. No relay is installed,
account authorized, or service restarted. Sample responses are discarded.

Approval runs the production approval handler against a simulated broker. `full`
includes pending, approved, denied and expired requests; `minimal` includes one
pending request, `empty` none, and `degraded` shows broker unavailability. Decisions
exercise real origin, digest, CSRF and replay checks without executing commands.

Desktop and Console use the production pages, JavaScript, session routes and
WebSockets with a synthetic colored display. They never connect to guacd, XRDP,
SSH or a real desktop. Enter any sample password. Both surfaces are available in
either session. Use `reject-sample-password` to exercise a failed login and
reconnect without changing the healthy desktop scenario. They enable HTTPS
automatically to retain production transport
checks; import the printed CA into a disposable browser profile as described
above. They require the reviewed Guacamole `share/all.js`, discovered from the
installed pinned release, or supplied with `--guacamole-assets /absolute/share/all.js`.
The checksum must match the reviewed client; this command installs nothing.

All surfaces share scenario/reset controls, stable URLs and credentials, source
watching and browser refresh. `full` starts Connect linked and the synthetic desktop
running; `minimal` and `empty` start Connect disabled, and `empty` leaves the
desktop stopped until Connect to desktop is selected. `degraded` simulates provider,
relay, broker and desktop failures. Saved Connect state and approval decisions
survive source reloads and clear on reset/scenario changes. Nonces, approval CSRF
tokens, and desktop/console sessions are renewed on source reload; an active
display or unsaved form offers a reload link instead of an automatic refresh.
Watching includes desktop JavaScript and templates, plus any `--watch` paths.
After deliberately reloading a display, reconnect with a sample password.

Run the matching focused browser checks while editing, then the full suite:

```bash
make browser-test BROWSER_ARGS="--grep 'specialized development' --require-guacamole"
make browser-test BROWSER_ARGS="--grep 'specialized development connect'"
```

The local suite launches each specialized development command and checks real
form submissions, scenario changes, reset, reload and cleanup. Desktop/console
checks require the reviewed assets and validate a real WebSocket attachment and
synthetic canvas in each engine. They also check keyboard escape from the display
and mobile input, and password failure recovery followed by a successful desktop
reconnect.
These results cover Basaltwater's interfaces; upstream T3 Connect authorization
and real desktop login remain deployment checks.

Static visual specimens remain available through the
[brand review workflow](../brand/README.md#reproduction-and-review). The static
exporter, browser fixture and development scenarios share deterministic job
records from `scripts/panel_samples.py`; interactive changes never alter exports.

## Run the local suite

Use a development machine with the Python development extra installed, Node.js
20 or newer, OpenSSL, NSS `certutil` (Debian's `libnss3-tools`), and the selected browsers'
existing OS dependencies. The command never installs packages or downloads
browser payloads. On a managed development VM with the pinned runtime:

```bash
source .venv/bin/activate
basaltw agent browser doctor --engine chromium --context managed --json
basaltw agent browser doctor --engine firefox --context managed --json
make browser-test
```

If Firefox's matching payload is absent, explicitly provision it first with
`basaltw agent browser install --engine firefox --context managed --json`.
Use the host's explicit dependency setup for missing native libraries.

For a developer without the managed runtime, create a dedicated local runtime
directory and install the exact `PLAYWRIGHT_VERSION` from
[`common/browser_automation_steps.py`](../../common/browser_automation_steps.py)
with npm's `--save-exact --ignore-scripts` options. Keep its generated package
and lockfile together; use `npm ci --ignore-scripts` for subsequent installs.
Download the matching Chromium and Firefox payloads explicitly with that
directory's Playwright CLI. Then select that installed runtime:

```bash
make browser-test BROWSER_ARGS="--context project --project /absolute/browser-runtime"
make browser-test BROWSER_ARGS="--engine firefox"
```

For a short edit/test loop, filter by a Playwright test-title regular expression:

```bash
make browser-test BROWSER_ARGS="--engine chromium --grep 'panel development'"
make browser-test BROWSER_ARGS="--engine chromium --grep 'panel form' --headed --trace"
```

`--headed` requires a working graphical desktop. `--trace` retains successful
traces too; failed tests retain traces and screenshots by default. A filtered
run reports `scope: focused`, its selection, and `selected_checks_passed` for
each engine. It never sets `application_tested` or claims full qualification.
No matching tests, failed selected checks and skipped first-party checks fail
the command. Run without `--grep` for the full suite. The development-server
case exercises real forms, all four scenarios, source reload, preservation of
saved and unsaved edits, reset, and cleanup on shutdown.

The standalone runtime must include `playwright` or `@playwright/test` at the
repository's exact pin. `playwright-core` alone lacks the test runner. Selecting
a project never upgrades it or changes its dependencies or lockfile. Projects
with their own browser policy should run their own tests instead of adopting
Basaltwater's runtime pin.

The suite uses one browser worker, even on hosts with many CPUs. This bounds
concurrent browser memory and preserves ownership of the single disposable
desktop. It binds every service to loopback, uses temporary state, replaces
provider/broker/desktop operations, and refuses unexpected subprocesses in the
service fixture. No real device is enrolled and no real command is approved.
The front proxy simulates the authenticated headers from Nginx; production
Nginx, guacd, XRDP, and a real desktop are separate deployment checks.

Each browser gets a new private profile and an ephemeral fixture CA imported
into that profile's trust database. Host trust stores and existing profiles are
untouched. Certificate verification stays enabled; a certificate with the wrong
hostname must still fail. Profiles, CA keys, and synthetic credentials are
removed when the run ends.

Results, browser/Playwright versions, console/request diagnostics, and failed
test traces/screenshots are kept under the invoking user's private
`~/.local/state/basaltwater/browser-tests/run-*` directory. `summary.json`
records each engine's browser versions, first-party and Guacamole outcomes,
counts of passed/failed/skipped checks, and whether first-party application
coverage passed. Native ESR and project UI remain separate. Missing or invalid
reports, missing browser versions, skipped first-party checks, and incomplete
required integration checks return nonzero even if the test process returned
zero. `results.json` contains the individual checks and version attachments.
Review locally; traces can contain synthetic credentials and form data.
Remove old run directories after review. Interrupted runs may leave temporary
fixture/profile directories, which can be removed after their processes exit.

Coverage includes navigation and keyboard focus, native disclosures, panel
light/dark themes, desktop/narrow layout, enlarged text, meters, actual form
submissions and redirects, authentication challenges, Secure/HttpOnly/SameSite
pairing cookies, nonce replay, forged CSRF, approval origin and replay rejection,
denial and expiry of a previously rendered approval form, safe status recovery
after a rejected decision, long approval values at narrow widths, readable Connect
authorization links and keyboard focus,
and completed CA downloads checked against the displayed fingerprint. Unexpected
page errors, console errors, and failed required requests fail the run.
Aborted document navigations that complete as downloads and canceled optional
favicon loads are distinguished from required request failures. Negative cases
assert their expected rejections explicitly.

### Guacamole boundary

With an installed current Guacamole release, the runner finds its
`share/all.js` automatically. Otherwise provide an existing, independently
verified copy explicitly:

```bash
make browser-test BROWSER_ARGS="--guacamole-assets /absolute/release/share/all.js --require-guacamole"
```

The script verifies the reviewed client's SHA-256. It never downloads or
installs Guacamole. Without the asset, the integration test is skipped and the
summary records the missing coverage; `--require-guacamole` makes that a
preflight failure. Before release, require this case to pass in both engines.
It covers the production embed page and JavaScript, session creation, WebSocket
attachment, rendering a synthetic display, control/observe, disconnect, and
forged-token rejection. The fake backend cannot log into a real desktop.
An upstream client defect should be reproduced against a disposable upstream
service and reported to its owner. Other hosted application UIs are deferred.

## Fast CI boundary

`make check-fast`, `make check-ci`, and ordinary Python test discovery never
run this suite or install browser payloads. They retain the mocked unit,
service, render, and harness preflight checks. `make browser-test` is a separate
explicit command and refuses to run when `CI` is set. Keep intensive browser
qualification on developer machines; do not add it to ordinary CI by accident.

## Native Debian Firefox ESR release qualification

Use a disposable Debian development desktop with the release candidate's
Firefox ESR package. Record the Basaltwater commit, Debian version, architecture,
`firefox-esr --version`, date, and the resulting checks. A Playwright Firefox
pass does not qualify this browser.

Start the synthetic services from the checkout in a new temporary directory:

```bash
fixture_dir=$(mktemp -d)
.venv/bin/python -m tests.browser.fixtures "$fixture_dir"
```

Leave that terminal running. In another terminal, inspect its private
`fixture.json` for the loopback HTTPS URLs and disposable login credentials;
do not paste them into chat. The fixture accepts only an empty, private directory
owned by the invoking user and publishes this ready file atomically after
starting the services. Start Firefox ESR with a new temporary profile
using `--no-remote --profile /absolute/temporary-profile`. On a managed desktop,
use its documented session launcher and preserve unrelated applications.

1. Before importing the fixture CA, visit the panel URL and confirm Firefox
   rejects its unknown issuer. Do not proceed past the warning.
2. Obtain `ca.crt` from the local fixture directory. Compare its SHA-256 through
   the independent local terminal. For a disposable remote VM, transfer only
   its public CA through trusted SSH and follow [client CA trust](../services/client-ca-trust.md).
3. In this disposable Firefox profile only, import the verified CA under
   Settings → Privacy & Security → Certificates → View Certificates →
   Authorities, enabling website trust. Restart the profile if needed, then
   reload the valid HTTPS URLs. Never disable certificate checks or enroll the
   fixture CA globally.
4. Check panel navigation, keyboard focus, disclosures, meters, light/dark
   appearance, 100%/200% browser zoom, and desktop/narrow widths. Download the
   public CA and compare its SHA-256 with the independent local value.
5. Check authentication, pairing's current-browser and one-time-link flows,
   and approval of a **synthetic fixture request only**. Confirm forged/expired
   submissions are rejected and a decision cannot be reused. Check that a
   fresh profile has no inherited authentication.
6. Visit the `invalid` URL after CA import and confirm its wrong-hostname
   certificate is still rejected. Do not add an exception.
7. Record pass/fail and evidence separately from the Playwright results. Stop
   the fixture process, close the disposable browser, and remove its profile
   and fixture directory, including the test CA's private key.

For the expiry check, load a fresh pending synthetic approval form. In that
page's developer console, send `fetch('/__fixture__/expire/REQUEST_ID',
{method: 'POST'})` with its request ID, then submit the original form and
confirm rejection. This authenticated control exists only in the disposable
fixture. Separate synthetic requests keep approval, denial, expiry, and pending
layout checks independent; fixture expiry does not exercise the real broker's
clock or command execution.

Repeat the documented internal-HTTPS enrollment on a disposable managed
deployment before release to qualify its actual proxy and service paths.
This native ESR/deployment record remains a manual release requirement; the
automated local suite never marks it complete.

For projects created with Basaltwater, follow the
[project browser policy](project-tooling.md#project-browser-tests). Agent runtime
selection and opt-in Firefox MCP registration are described in
[browser automation](browser-automation.md#verification).

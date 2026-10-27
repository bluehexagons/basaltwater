# Choose and operate a build system

Basaltwater has several build paths. Choose the one that matches who starts
the work and where the build should run.

| Path | Starts work through | Build location and guide |
| --- | --- | --- |
| Ordinary deployment | `setup --deploy`, saved `patch` or `deploy` | The deployment target; [manifests and deployment](deployments.md) |
| Selected builder | `setup --build-on SAVED-BUILDER --deploy …` | A managed builder selected by the controller; verified bundles go to the target over typed SSH |
| Webhook CI/CD | Signed repository webhooks | The webhook executor; [CI/CD topology and connection](cicd.md) |
| GitHub Actions runner | GitHub Actions workflows | A dedicated job host; [Debian runner](github-runner.md) or [Windows runner](../workstations/windows-github-runner.md) |

The selected-builder path has its own enrollment, admission and artifact
contracts. It does not use `cicd connect`, a webhook receiver or an Actions
runner. Bare `--build-server` selects the webhook role; adding
`--build-host-policy FILE` selects the managed on-demand builder. Combining
managed enrollment with `--cicd` is refused.

A single host combining all three build/CI services is not available yet.
The runner still has a dedicated profile and separate capacity control;
[role composition and existing-runner conversion](../plans/cicd-manifest-reuse.md)
are the next deliverable. `--build-on` selects a saved builder for another
deployment; it does not enable build services on that builder.

## Start with a selected builder

The bounded path supports ready static sites, Python static builds and
self-contained Go services on Debian 13 x86-64 VMs or physical hosts. It
requires public HTTPS source with a committed root `basaltwater.json`.
[Complete sample projects and enrollment templates](../examples/builds/README.md)
cover all three cases, including a ready SPA and Go API in one application.
Node/package-manager builds, interpreter services and runtime environment or
database declarations are outside this path. Ordinary target builds support a
broader manifest; see [the exact restrictions](deployments.md#build-on-a-saved-builder).

Before setup:

1. Prepare the Debian builder and target, controller-local Ed25519 identity
   files and their public companions, and verified host pins in the controller
   workspace. Use existing administrative access to establish trust; never
   disable host-key verification to pass preflight. See [identity and trust
   requirements](../agents/contributing/build-selection.md).
2. Copy the [enrollment templates](../examples/builds/README.md#enrollment-templates)
   outside the repository. Replace their public-key placeholders and domains,
   then review projects, routes, target roots, resource ceilings and storage
   limits. These are enrollment envelopes, not just their nested policies.
3. Provision root setup access independently on both hosts. On the target,
   the selected identity must authorize root as well as its typed transport
   principal. Enrollment installs only a forced-command transport key; it
   does not grant root login. Keep private keys on the controller.

For the SPA/Go example, enroll and save a builder named `builds`:

```bash
basaltw setup server_lite build.example.com root --machine vm --name builds \
  --key /home/controller/.ssh/build_ed25519 \
  --build-server --build-host-policy /home/controller/build-host.json --go
```

Use `--machine hardware` for physical hosts. Go is a builder tool; the
selected-builder target command refuses Go, Node and Godot installation flags.
Ready static output needs no compiler; Python static builds use the builder's
OS Python 3 and need no target `--python` flag. That flag remains available as
a profile option for installing target Python tooling and uv. Inspect the
builder's observed capabilities before assuming a runtime is ready.

Publish a sample as its own repository, with its manifest at the root, and
replace the URL below. With the target's root access and existing host pin
already provisioned, preflight without applying:

```bash
basaltw setup server_web web.example.com root --machine vm \
  --key /home/controller/.ssh/web_ed25519 \
  --build-on builds --build-target-policy /home/controller/build-target.json \
  --deploy app.example.com 'https://github.com/OWNER/spa-api.git#tag=v1' \
  --dry-run
```

Review the result, then repeat without `--dry-run`. Use the same controller
workspace for both commands. Dry runs clone pinned source and make read-only
SSH probes; they do not enroll, reserve, submit, stage or activate. Apply
bootstraps target authority, reserves every application before submitting
builds, stages every artifact before activation, and finally applies the full
profile against the exact installed release identities. A chosen builder's
failure does not fall back to building on the target.

## Update and reconcile

After publishing a new committed tag:

```bash
basaltw patch web.example.com --deploy app.example.com 'https://github.com/OWNER/spa-api.git#tag=v2'
basaltw deploy web.example.com --yes
```

The patch applies the revision update and saves it. The subsequent `deploy`
replays the saved selection; it is not required to finish the patch. Selected
builder patches replace the source for each named destination, retain omitted
siblings and preserve the builder. `--no-build-on` explicitly clears that
selection and artifact mode; it is not recovery for a failed delivery.

On an enrolled artifact-mode target, local `basaltw refresh` reconciles retained
release descriptors and bundles without a builder connection, source checkout
or compiler. Missing retained inputs refuse with controller guidance. See
[deployment and recovery details](deployments.md#build-on-a-saved-builder).

## Inspect capacity and retain evidence

On the builder, use its installed entrypoint from a local root administration
context. These maintenance actions reject SSH invocation environment markers;
they are not actions in the authenticated build protocol.

```bash
sudo /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py --inspect-capacity
sudo /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py --inspect-retention REQUEST_ID
```

Capacity inspection reports logical byte charges, record/history limits and
physical available bytes separately. It makes no readiness decision or space
reservation. Retention inspection can reconcile input durability; it is not
the strictly read-only capacity command. Use the returned exact revision for
one explicit action:

```bash
sudo /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --settle-terminal REQUEST_ID --expected-revision INSPECTED_REVISION
# Inspect again before choosing the next action and its current revision.
sudo /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --prune-terminal REQUEST_ID --expected-revision INSPECTED_REVISION
sudo /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py \
  --release-bundle REQUEST_ID --expected-revision INSPECTED_REVISION
```

Settlement recharges verified terminal work for retained bytes. Pruning requires
settlement and explicitly deletes source/log payloads by default. Bundle release
requires settlement and an exact successful job; it ends builder retrieval for
that bundle. Downloading or delivering a bundle never releases it automatically.
Each action is a separate operator decision; do not execute this list as an
unconditional cleanup script. They preserve job results, replay identities and
input/history slots. Finite slots and ledger limits can still refuse new work
after bytes are reclaimed. See [capacity inspection](../agents/contributing/build-host-entrypoint.md#read-only-local-capacity-inspection)
and [settlement, pruning and bundle release](../agents/contributing/build-retention.md)
for refusal conditions, revision retries and interruption evidence.

Controller and target recovery evidence is separate from builder retention.
Preserve unfinished guards, exact IDs, pinned inputs and receipts after a lost
reply or failure. The [offline setup inspector](../agents/contributing/build-setup.md#offline-unfinished-setup-inspection)
reports local acknowledgements and unresolved outcomes without contacting a
host; it does not resume or reset work. Current qualification and remaining
crash, power-loss and retention-policy limits are listed in the
[coverage map](../plans/testing-coverage.md).

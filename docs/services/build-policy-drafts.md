# Draft build enrollment policies from existing manifests

Run this operator utility from a repository checkout with Python 3.11 or newer:

```bash
python3 scripts/prepare_build_policies.py \
  --builder-key ~/.ssh/build_deploy.pub \
  --target-key ~/.ssh/target_deploy.pub \
  --output-dir ./private-build-drafts \
  --deploy example.com https://github.com/owner/site.git \
  --deploy food.example.com/foodguide https://github.com/owner/foodguide.git
```

Repeat `--deploy DOMAIN[/PATH] HTTPS_URL` for up to sixteen destinations. Explicit
`#branch=NAME` or `#tag=NAME` selectors use the existing source preparation rules;
each selection is pinned to its exact commit before planning. Sources must be
HTTPS repositories with a tracked `basaltwater.json` manifest. Fetching
uses the shared bounded source preparation flow in a temporary
private directory under the configured controller workspace, removed on completion
or failure. The utility runs no build
commands and makes no server mutations or enrollments.
Anonymous access is tried first; private sources use the controller's configured
noninteractive global Git credential helper. Credentials remain on the controller.

Both key arguments are paths to **public companion files**, never private key
paths. The same public key can serve both roles. One valid ssh-ed25519 public
line is required; its optional comment is removed, leaving only the canonical
key type and wire blob. Private key contents are never derived or copied.
`--principal` defaults to `deploy` and must be a lowercase enrollment slug.

The output is `builder-enrollment.json` and `target-enrollment.json`, each mode
0600 in a caller-owned mode-0700 output directory. Missing directory components
are created privately. Existing files are never overwritten, including when only
one draft already exists; choose a fresh output directory for another run.
Both complete documents and shared delivery plans are validated before files
are created. Runtime tool versions are not probed by this utility; actual
delivery still checks authenticated builder readiness.

Project and application names are readable domain/path slugs with a stable
digest suffix (maximum 64 characters), independent of deployment order and
repository name. Duplicate destinations, name collisions, overlapping roots,
service identity collisions and conflicting routes are refused. Runtime grants
honor explicit `build_runtimes`, including `[]`; otherwise they use the tools
inferred from the pinned source by shared preparation.

Target grants include the complete component route map and exact supported
service runtime settings. Manifest paths remain literal: a destination ending
in `/foodguide` does not prefix component paths. A manifest's `/foodguide` route
stays `/foodguide`. Release roots use ordinary deployment's `/var/www` helper:
`food.example.com/foodguide` becomes `/var/www/food_example_com__foodguide`.
This preserves generated service identities and the corresponding
`/var/www/.basaltwater_shared/food_example_com__foodguide` persistent data tree
when migrating ordinary deployments with that default base. Arbitrary deployment
roots and custom ordinary deployment bases are outside this utility's scope.

Default per-build ceilings are 1 GiB memory, 200% CPU, 4 GiB scratch,
128 MiB artifacts, 65,536 artifact entries, 900 seconds and 4 MiB logs.
Override them with integer `--memory-bytes`, `--cpu-percent`, `--scratch-bytes`,
`--artifact-bytes`, `--artifact-entries`, `--timeout-seconds` and `--log-bytes`.
Shared protocol parsers enforce supported bounds and alignment. These are grants;
actual delivery requests also remain capped by shared protocol defaults.
The planner requests the minimum of each protocol default, builder grant and
applicable target ceiling. In particular, the draft's 200% CPU grant produces
a 100% CPU request because that is the current protocol default. Increasing
an enrollment ceiling alone does not raise a request above its protocol default.

Both enrollments default to 128 records; retained storage defaults to 64 GiB
on the builder and 8 GiB on the target. Override with `--builder-max-records`,
`--target-max-records`, `--builder-max-bytes` and `--target-max-bytes`.
Retained reservations consume finite capacity even after jobs terminate.
There is no automatic cleanup. Use explicit operator retention/settlement
maintenance as described in the [build systems guide](build-systems.md).

Review both JSON files before applying them through the separate managed
enrollment workflow: verify keys/principal, complete routes and runtime grants,
release roots, tool grants, and resource/storage ceilings. The console summary
identifies applications, roots, tools and routes; the full files contain runtime
details. Review is an operator responsibility; the utility adds no approval
prompt. Keep existing deployment/data identities during migration and reconcile
existing enrollments explicitly rather than treating drafts as policy updates.
Saved setups retain decoded envelopes, not live references to these JSON files.
Use the [policy update procedure](build-operations.md#add-a-site-or-change-its-grants)
to add sites or apply reviewed replacements without dropping existing authority.

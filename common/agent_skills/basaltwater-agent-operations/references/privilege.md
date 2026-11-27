# One-time privileged actions

## Request tools

Prefer a saved setup change for tools the host should have after a rebuild.
Check `basaltw agent software plan --flag jupyterlab --json` to see the exact
packages. Then request the flag with `basaltw agent software request --flag
jupyterlab --reason "Run recurring notebook checks" --json`. Use `--apt PACKAGE`
for a tool outside the flag catalog. For a tool specific to a temporary task,
use the project's environment when practical, or add `--temporary` for an
approved host install. Temporary packages stay installed until removal or rebuild.

Keep the returned ID and send the review URL through the authorized user
channel. Continue independent work. Use the privilege wait command below when
dependent work is ready. A saved request can also be waiting for the owning
controller's `basaltw agent software sync HOST`. Check both `saved_for_rebuild`
and `applied` before reporting success.
If a submission response is lost, use `basaltw agent privilege list --json` to
recover the ID before requesting the same change again.

## Request a command

On a machine with web-panel approvals enabled, or a VM with `--approval-panel`, request a supported root action
through `basaltw agent privilege request` instead of trying `sudo`:

```bash
basaltw agent privilege request command.run \
  --reason "Refresh package metadata" --command /usr/bin/apt-get update
basaltw agent privilege wait REQUEST_ID --timeout 300 --json
```

The request returns a `review_url` in the main web panel, or the standalone
HTTPS approval panel (normally port 9444) on a minimal VM. Give that URL to the
operator. The coding account cannot decide through the request socket; never
obtain or use either panel's login to approve your own request. The main panel
uses its existing login; the standalone panel has an independent password.
Both display the exact arguments and allow one approved attempt. Do not
put a shell, `sudo`, environment assignments, or secrets in `--command`.
Managed `basaltwater-web` publication and owner-scoped live gateway actions
already work without this approval flow. See
[Privilege approvals](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/privilege-approvals.md)
for the supported actions and portal setup.

Waiting can time out while the original request remains pending or executing.
Request a review window with `--review-window 2d` and a command budget with
`--runtime 30m` before `--command`. Keep the ID and continue independent work.
A background `basaltw agent privilege wait REQUEST_ID --until decision --timeout 2d --json`
can notify your task runner when the operator decides. For dependent work wait
for completion and check the execution result. Exit 124 is a local wait timeout.
Inspect it with `basaltw agent privilege status REQUEST_ID --json` rather than
submitting another request. An uncertain execution result also needs inspection
before retrying. Cancelling a local prompt or reaching its runtime cap does not
cancel broker requests or undo an already approved host action.

For an existing Admin-screen refresh, use `basaltw agent privilege admin-status
--json` to read owned request IDs and safe maintenance diagnostics. Dispatch and
source activation do not establish setup completion. Missing old-broker fields
and unrelated setup markers are unknown; never use them to infer a failing step
or replay uncertain work. Keep recovery snapshots until disposable lifecycle
qualification passes; [PRV-01 in the release matrix](https://github.com/bluehexagons/basaltwater/blob/main/docs/release-qualification.md#stable-qualification-matrix)
owns that gate.

If the action becomes unnecessary, use
`basaltw agent privilege cancel REQUEST_ID --json`.
It withdraws your pending or approved request until the
worker claims execution. Already expired requests stay expired; claimed actions
cannot be stopped or undone by cancellation. A cancelled request needs no
operator approval and remains in the audit history.

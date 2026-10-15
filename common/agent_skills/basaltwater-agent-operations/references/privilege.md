# One-time privileged actions

On a VM configured with `--privilege-broker`, request a supported root action
through `basaltw agent privilege request` instead of trying `sudo`:

```bash
basaltw agent privilege request command.run \
  --reason "Refresh package metadata" --command /usr/bin/apt-get update
basaltw agent privilege wait REQUEST_ID --timeout 300 --json
```

The request returns a `review_url` for the separate HTTPS approval portal,
normally on the VM's port 9444. Give that URL to the operator; the coding
account cannot approve the request or receive the approval password. The
portal displays the exact arguments and allows one approved attempt. Do not
put a shell, `sudo`, environment assignments, or secrets in `--command`.
Managed `basaltwater-web` publication and owner-scoped live gateway actions
already work without this approval flow. See
[Privilege approvals](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/PRIVILEGE_APPROVALS.md)
for the supported actions and portal setup.

Waiting can time out while the original request remains pending or executing.
Inspect it with `basaltw agent privilege status REQUEST_ID --json` rather than
submitting another request. An uncertain execution result also needs inspection
before retrying. Cancelling a local prompt or reaching its runtime cap does not
cancel broker requests or undo an already approved host action.

For an existing Admin-screen refresh, use `basaltw agent privilege admin-status
--json` to read owned request IDs and safe maintenance diagnostics. Dispatch and
source activation do not establish setup completion. Missing old-broker fields
and unrelated setup markers are unknown; never use them to infer a failing step
or replay uncertain work. Keep recovery snapshots until disposable lifecycle
qualification passes; the [qualification plan](https://github.com/bluehexagons/basaltwater/blob/main/docs/plans/PRIVILEGE_EXECUTION_QUALIFICATION.md)
owns that gate.

If the action becomes unnecessary, use
`basaltw agent privilege cancel REQUEST_ID --json`.
It withdraws your pending or approved request until the
worker claims execution. Already expired requests stay expired; claimed actions
cannot be stopped or undone by cancellation. A cancelled request needs no
operator approval and remains in the audit history.

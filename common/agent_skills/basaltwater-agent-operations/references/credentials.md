# Credential rotation

Inspect credential status from the controller. Start a login only when the
user requested authentication or recovery:

```bash
basaltw agent auth status HOST USER --json
basaltw agent auth login HOST USER
```

Login defaults to a separate ChatGPT subscription session on the target.
Relay the device URL/code to the user; never authorize their account yourself.
`--open-browser` optionally opens the page on the controller. The controller
needs no Codex installation. API-key mode requires `--method api-key` and
warns that API usage is separately billed, outside the ChatGPT subscription.
Use a hidden prompt, protected `--api-key-file`, or `--api-key-stdin`; never
print secrets or put them in arguments or repositories.

Setup with `--agent-auth login` preserves or renews target credentials and
starts device authorization when recovery is needed from an interactive
terminal. Unattended setup reports the required login command. Daily and
boot maintenance continue to renew the target's own file-backed session.
Credential pulling is removed; do not share rotating subscription tokens
between VMs. `auth set` remains for explicit file imports for other tools,
including `--tool gh --active` or a protected `--file`.

## Private input in T3 sessions

When the active T3 tool supports it, use `request_secret` for needed private
input. Its default returns a one-use `secretRef` for a tool that accepts that
field, such as a webhook signature. For an authorized shell CLI, request
`delivery: "file"` and pass the returned owner-only file to its supported
credential-file/stdin interface. Read the value inside the consuming command
only if required; never print it, enable shell tracing, or place it in command
arguments. Do not copy it into chat, prompts, evidence, or source control.

Check `status`: declined, cancelled, or timed-out input is not a credential.
Use a new request ID for a later authorized retry. File delivery expires after
24 hours; keep its temporary path out of durable scheduled prompts and arrange
normal account-owned credential provisioning for unattended work. This does
not replace provider login or authorize credential rotation. Older T3 tools may
support only `secretRef`; follow the live schema. See the
[0.1.3 secret-input contract](https://github.com/pingdotgg/t3code/blob/v0.1.3/packages/contracts/src/orchestratorMcp.ts).

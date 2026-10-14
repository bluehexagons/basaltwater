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

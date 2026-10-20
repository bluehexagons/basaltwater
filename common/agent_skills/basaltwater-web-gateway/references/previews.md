# Managed live previews

For Vite, project and command detection is automatic:

```bash
basaltwater-web preview start NAME --project .
```

For a separate web workflow, keep the project's script name:

```bash
basaltwater-web preview start NAME --project . --script dev:web
```

The selected Vite script receives strict loopback, port, and `--strictPort`
arguments. The gateway honors npm, pnpm, and Yarn declarations and lockfiles;
conflicting managers fail before project commands run. `--script` cannot be
combined with an explicit command after `--`.

Automatic previews and dependency installation use the project's Node selection
(`.nvmrc`, `.node-version`, and package engines) as the requesting user. A missing
compatible runtime fails without installing Node or changing the account default;
inspect it with `basaltw node doctor --project .`. The service selects again on
restart. Custom commands keep control of their own runtime; wrap a Node command
with `basaltw node exec --project /absolute/project -- COMMAND` when appropriate.

For another server, pass an argv command after `--`; `{host}` and `{port}` are
replaced without shell evaluation:

```bash
basaltwater-web preview start NAME --project . -- \
  ./server --host '{host}' --port '{port}'
```

For documented uploads above Nginx's 1 MiB default, add `--max-body-size SIZE`
before `--`, for example `--max-body-size 129m` for a 128 MiB backup plus
multipart overhead. The positive limit is capped at 1 GiB. Keep the existing
limit when refreshing the service.

Restart an existing preview after edits when its command needs to rerun:

```bash
basaltwater-web preview restart NAME --wait 60 --json
```

This preserves its ports, URL, saved command, profile, and upload limit. Build a
precompiled executable first. See [refreshing a development site](refresh.md)
for user services and freshness verification.

Use `basaltwater-web preview list`, `basaltwater-web preview logs NAME`, and `basaltwater-web
doctor NAME` for inspection. Stop a preview only when explicitly requested:

```bash
basaltwater-web preview stop NAME
```

# Build examples

These small projects work with ordinary manifest deployment and the bounded
[selected-builder flow](../../services/build-systems.md). Each directory is a
separate repository root; deploying the Basaltwater repository itself does not
select a nested example.

For the six existing homelab repositories, use the
[LAN builder migration commands](homelab-local-builder.md).

| Directory | Manifest output | Builder requirements | Template application / destination |
| --- | --- | --- | --- |
| [ready-static](ready-static/basaltwater.json) | Committed `public` directory | No build runtime or command | `static-site` / `static.example.com` |
| [python-static](python-static/basaltwater.json) | `dist` generated with the standard library | Python 3, declared `python` runtime | `python-site` / `python.example.com` |
| [spa-go](spa-go/basaltwater.json) | Ready SPA plus `bin/api` in one bundle | Go, declared `go` runtime | `spa-api` / `app.example.com` |

Copy one directory into a new Git repository, commit every source file including
`basaltwater.json`, and publish it as a public HTTPS repository. Create a `v1`
tag if using the quickstart's `#tag=v1` selector. Build outputs such as `dist`
and `bin` need not be committed. The deployment command's domain must match the
administrator policy; `{{domain}}` renders that domain in the manifest.

The SPA's frontend is already built. This example does not install npm, build
React/Vite, declare runtime secrets or configure a database. The API honors
generated `HOST`/`PORT`, has a canonical `/health` endpoint and retains the
`/api` request prefix. Its deliberate `/api/error` endpoint returns JSON with
HTTP 418 to distinguish API errors from the SPA fallback.

## Check a project locally

Run from the copied project's root. For Python:

```bash
python3 build.py
python3 -m http.server 8080 --bind 127.0.0.1 --directory dist
```

For ready static output, serve `public` with the same HTTP server command.
For the SPA/Go application, build and start the API:

```bash
mkdir -p bin
CGO_ENABLED=0 go build -trimpath -o bin/api .
HOST=127.0.0.1 PORT=8081 ./bin/api
```

From another terminal:

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS 'http://127.0.0.1:8081/api/echo?message=hello'
curl -i http://127.0.0.1:8081/api/error
```

A standalone Python static server does not provide the deployed SPA fallback
or reverse proxy. Test the combined root, deep links and `/api` routes through
the deployed target. For a Linux x86-64 artifact, build on the supported Debian
builder; a local build on another OS does not prove target runtime compatibility.

## Enrollment templates

Copy [build-host.example.json](build-host.example.json) and
[build-target.example.json](build-target.example.json) to controller-local files.
Both are complete schema-1 enrollment envelopes: `schema`, `policy`, `keys`,
`max_records`, `max_bytes`. Passing just the nested policy is insufficient.

Replace `REPLACE_WITH_YOUR_PUBLIC_KEY_BASE64` before use. The checked-in text is
an intentionally invalid placeholder, never a usable credential. Each `keys`
value must be exactly `ssh-ed25519 BASE64` from your controller identity's public
companion, without a trailing comment or key options. Never paste a private key.
Use the builder identity's public key in the builder file and the target
identity's public key in the target file; they may be different keys. Principal
`deploy` is a policy label mapped to a generated forced-command account, not an
interactive user or root grant.

The templates grant all three examples. Remove unused projects and applications
before enrollment, retaining identical principal/key sets. Change domains and
target roots for your environment. The complete named component routes must
match: the `spa-api` application authorizes both `site` at `/` and `api` at
`/api`. Project IDs match the corresponding builder grants. Target deployment
parents must be root-controlled; choose distinct nonoverlapping application
roots outside Basaltwater's protected runtime/state trees.

The builder template uses explicit small ceilings: 1 GiB scratch, 64 MiB
artifacts, 4096 entries and 1 MiB logs per job, with an 8 GiB total logical quota
and 32 input records. The target staging quota is 1 GiB with 32 records.
Reservations also include source, descriptor and metadata allowances; these
ceilings do not reserve physical disk space or promise a particular concurrency.
Inspect real capacity and adjust ceilings for your projects. Omitting explicit
small limits would use the much larger request defaults, including 8 GiB
scratch and 2 GiB artifacts per build.

Use these files with the [enrollment and deployment walkthrough](../../services/build-systems.md#start-with-a-selected-builder).
The walkthrough also explains independently provisioned root setup access,
verified host pins, saved revision updates and explicit retention maintenance.

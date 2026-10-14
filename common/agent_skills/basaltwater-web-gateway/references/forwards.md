# Live forwards

For a collaborative preview of an already-running loopback server, resolve its
port before guessing an `environment-port` or client-loopback URL:

```bash
basaltwater-web preview resolve --port PORT --json
```

The read-only resolver returns an existing owned route as `navigation.url`, or
`forward_missing` with an exact creation `command`. Run that command when the
requested preview includes client-visible access, then resolve again. Failures
identify the upstream or gateway in `component`, distinguish transport, timeout,
HTTP and certificate issues, and include `remediation`. Use `--host ::1` for
IPv6 and `--profile godot` when isolation headers are required. VM-side success
does not establish client routing, source-policy access, trust, or rendering.

Create a forward after its loopback service is ready:

```bash
basaltwater-web forward add NAME --listen auto --to 127.0.0.1:PORT
```

Add `--profile godot` for a Godot preview that needs secure-context and
cross-origin isolation headers. If the upstream has a documented request-body
limit above Nginx's 1 MiB default, add a bounded limit such as
`--max-body-size 50m`. The command chooses a permitted HTTPS port, applies the
VM's existing access-source policy, validates Nginx, reconciles UFW, and prints
the URL.

Treat the two ports as distinct: `--to` is the private loopback HTTP upstream;
`--listen` is the externally reachable Nginx HTTPS port. Only the Nginx
listener receives a managed UFW rule. Use the URL printed by `basaltwater-web`
instead of substituting the upstream port or scheme.

Use `basaltwater-web forward list`, `basaltwater-web doctor NAME`, and `basaltwater-web ca` for
inspection. Remove a route when the associated service is no longer intended
to be reachable:

```bash
basaltwater-web forward remove NAME
```

Do not proxy databases, SSH, metadata endpoints, or another user's service.
Treat adding or removing a forward as an external exposure change and keep it
within the user's requested scope.

The configured owner runs these commands without `sudo`. The local control
service checks the caller's Unix socket UID against the saved gateway policy;
it cannot accept another user's claimed identity. If a live mutation reports
that the control service is unavailable, check
`systemctl status basaltwater-web-control.service` and ask the operator to rerun
saved VM setup. Do not work around this with a broad sudo grant. The
administrator-only `forward reconcile` command is not available through the
owner-scoped control socket.

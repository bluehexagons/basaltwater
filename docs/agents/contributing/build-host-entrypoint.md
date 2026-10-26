# Build-host process entrypoint

`common/service_tools/build_host.py` composes the internal builder behind a
root-controlled launcher. Setup and controller selection are not connected yet.
The initial host platform is Debian 13 x86-64. Standard deployment is unchanged.

Install the source beneath a root-controlled tree outside `/home` and invoke
the entrypoint with `/usr/bin/python3 -I`. Its configuration defaults to
`/etc/basaltwater/build-host/config.json`, inside a root-owned `0700` directory.
The no-follow, single-link file must be root-owned `0600`. It contains exactly:

- `schema: 1`, positive unprivileged `uid` and `gid`;
- `roots`: distinct non-overlapping private directories named `state`,
  `inputs`, `scratch`, `artifacts`, and `logs`;
- `policy`: the [project policy](build-host-policy.md);
- `max_records` and `max_bytes`: input admission's retained-record and reserved
  storage ceilings. Full capacity refuses new work and requires reconciliation.

The worker runs with `--worker`, independently of SSH. `--once` processes at
most one claim. It re-reads current policy at both authorization boundaries;
changes to identity, roots or storage settings require a drained restart.
Only installed tools allowed by the project grant reach the pinned manifest's
readiness check. An interrupted or uncertain claim needs explicit recovery;
the process never replays it automatically. Its service should stop on failure,
leaving the claim and evidence available to an administrator.

For SSH, a dedicated key's forced command fixes `--serve --principal LABEL`.
Use OpenSSH `restrict` and disable password login for this endpoint; do not
grant the build identity that key or an interactive shell. The entrypoint
requires the exact original command `basaltwater-build-v1`, accepts one typed
request on stdin, and limits the connection to 15 minutes. The client cannot
select a principal, path, shell command, drain operation or configuration file.
Root administrators can select `--config` when operating the helper locally;
that option must never be interpolated from SSH input.

The connection deadline bounds normal framing, upload and download waits. A
kernel syscall that cannot be interrupted can delay process exit. Accepted
jobs are durable and use an independent worker, so controller disconnects do
not cancel them. Status queries and repeated request IDs reconcile lost replies.
Runtime secret values and target credentials do not belong in build inputs.

This entrypoint still requires setup-owned account/key enrollment, bounded
source staging, typed transport integration and native qualification before
enablement. Persistent jobs sharing an identity must be mutually trusted.

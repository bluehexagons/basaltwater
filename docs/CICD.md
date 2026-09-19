# CI/CD Webhook System

Use `--cicd` during `setup` or `patch` to install the webhook receiver and
executor. Repository-specific scripts live in
`/etc/basaltwater/cicd/webhook_config.json`.

Example setup:

```bash
basaltw setup server_web ci.example.com deploy \
  --cicd --ssl --ssl-email admin@example.com
```

After setup, edit the generated configuration and add a GitHub webhook. The
secret is generated once and stored root-only at
`/etc/basaltwater/cicd/webhook_secret`; the systemd environment file is
`/etc/basaltwater/cicd/webhook.env`.

Setup reconciles both secret files to `root:root`/`0600` and regenerates the
environment from the canonical secret on every run. Empty, multiline, oversized,
or unsafe secret values fail before service replacement. Secrets must use
letters, digits, or `._~+/=-`; setup does not rotate an existing valid secret.

## Build and app server topology

The build server runs the webhook receiver as `webhook` and repository code
as the separate `cicd-build` account. An app server only needs nginx, rsync, and the restricted
`deploy` account. The build server pushes artifacts over SSH; it does not need
root access on the app server.

Set up the app server first:

```bash
basaltw setup server_web app.example.com deploy \
  --app-server --ssl --ssl-email admin@example.com
```

For a provisioned VM, keep the OS and language tooling on SSD-backed root
storage and mount the bulk disk directly at the CI state directory. A nominal
4 TB disk provides about 3.64 TiB before pool overhead, so this example requests
3500 GiB rather than assuming the full advertised capacity is allocatable:

```bash
basaltw setup server_web 192.168.1.60 deploy \
  --provision-on pve1 --hostname build \
  --memory 16G --cores 8 \
  --storage root local-lvm 96G \
  --disk-ssd root --disk-discard root --disk-backup root \
  --storage cicd-data ts1-storage 3500G \
  --storage-mount cicd-data /var/lib/basaltwater/cicd ext4 empty \
  --no-disk-ssd cicd-data --disk-discard cicd-data --disk-backup cicd-data \
  --build-server --node --python --go
```

Replace the address, Proxmox host, memory, and CPU values for the environment.
Run the same command with `--dry-run` first. The `ts1-storage` pool must support
Proxmox VM disk images and report at least the requested free capacity. The
mount is fail-closed: setup prepares only the newly attached blank disk and
will not fall back to putting builds on the root volume. `--build-server`
already includes the webhook receiver and executor, so adding `--cicd` is
supported but redundant. The explicit per-device flags let reruns retain SSD
emulation only for the SSD-backed root while reconciling discard and Proxmox
backup inclusion on both disks.

For an existing Debian server rather than a newly provisioned VM, omit the
`--provision-on`, capacity, and `--storage*` flags and mount the bulk disk at
`/var/lib/basaltwater/cicd` before setup.

Bootstrap the build server once without deployment targets so its managed
runtime and workspace exist. The storage-aware command above already performs
this phase. Then connect the two saved setups from the controller:

```bash
basaltw cicd connect 192.168.1.60 app.example.com
```

Saved host names, friendly `--name` values, and exact tags are accepted. If the
controller does not already trust the app server, the command displays its SSH
fingerprint for independent verification. Non-interactive automation can pin
that verified identity explicitly:

```bash
basaltw cicd connect 192.168.1.60 app.example.com \
  --target-name production \
  --fingerprint SHA256:REPLACE_WITH_VERIFIED_FINGERPRINT
```

The command validates both saved roles, transfers only the public half of the
build deploy key, deduplicates the app server's `authorized_keys`, installs the
verified host key and target definition atomically on the build server, and
tests SSH as the unprivileged `webhook` credential owner. It is safe to rerun. Inspect
or retest connections with:

```bash
basaltw cicd status 192.168.1.60
basaltw cicd test 192.168.1.60 app.example.com
```

The target entries default to the `deploy` user, SSH port 22, and `/var/www`.
Use `cicd connect --port` or `--base-dir` when a target differs. The target name
used by a repository must match its JSON key. A custom base directory must
already exist as a real directory and be writable by the `deploy` account.
For generated nginx sites using a custom base, also authorize that directory
on the app server in root-owned `/etc/basaltwater/cicd/deploy_policy.json`:

```json
{"allowed_base_dirs": ["/var/www", "/srv/sites"]}
```

The policy must not be writable by group or others. Without this file, only
sites below `/var/www` are accepted. The filesystem root is never a valid base.

## Repository configuration

Configuration is stored as `root:webhook`/`0640`; setup and `webhook-manager`
preserve that contract during atomic updates. Setup verifies schema and reads
the file as `webhook` before service replacement. `/health` returns 503 for
unreadable or invalid configuration, and the executor retains queued jobs until
configuration is repaired.
Configuration reads require a regular file, reject symlinks and FIFOs, and are
limited to 1 MiB. Writers enforce the same size limit before publication.

The shared version 1 schema accepts legacy files without a `version` field.
Repository URLs must use credential-free HTTPS; branches must be nonempty lists
of valid branch names. Script paths must be relative to the checkout, with no
parent traversal; resolved scripts cannot escape through symlinks. Existing
configurations using absolute scripts must move those scripts into the repository.

Edit `/etc/basaltwater/cicd/webhook_config.json` on the build server. Each
repository entry selects accepted branches and scripts. A remote deployment
uses `deploy_target` (a key from `deploy_targets.json`) and an optional
`deploy_spec` (`domain` or `domain/path`):

```json
{
  "repositories": [
    {
      "url": "https://github.com/example/site.git",
      "branches": ["main"],
      "deploy_target": "app.example.com",
      "deploy_spec": "www.example.com/",
      "scripts": {
        "install": "scripts/install.sh",
        "build": "scripts/build.sh",
        "test": "scripts/test.sh",
        "deploy": "scripts/deploy.sh"
      }
    }
  ]
}
```

`install`, `build`, and `test` run in a fresh, commit-pinned workspace as
`cicd-build`. When `deploy_target` is present, artifacts are pushed with rsync,
nginx configuration is refreshed, and the optional deploy script is streamed
to the target directory. Without `deploy_target`, the optional deploy script
runs locally on the build server. Use repository URLs without embedded
credentials; the executor rejects credential-bearing URLs.

Remote destinations must normalize to a strict child of the target's base
directory. A destination equal to the base (including `/.`) or outside it is
rejected before rsync can run with `--delete`. When a deploy script is
configured, it must be a readable regular file no larger than 1 MiB; the executor reads it before
transferring artifacts and fails the job if it is missing or unreadable.

After changing the JSON, the next signed push uses the new settings. A ping
event only verifies webhook connectivity and does not build a repository.

## Delivery receipts and queue limits

Accepted pushes are recorded in `/var/lib/basaltwater/cicd/deliveries.sqlite3`
before their job file is published. The receipt retains the GitHub delivery ID
when supplied, repository, commit, and job payload. Deduplication uses the
authenticated body digest: changing the unsigned delivery-ID header cannot
replay the same signed request. Duplicate requests receive 202 without creating
another attempt. Terminal receipts are retained for 30 days from acceptance;
replay protection does not extend beyond that retention window.

The receiver recovers unpublished pending jobs on restart. Jobs run in receipt
acceptance order, including recovered reservations. The executor records
its claim before build side effects, so a crash after claiming does not replay
a possibly completed deployment. Inspect the journal and build logs before
deliberately submitting a new push after an interrupted or failed attempt.
Do not delete the ledger to force retries. Missing, invalid, or unavailable
receipts retain their queued jobs and fail execution for operator repair.
Upgrade receiver and executor together for this receipt protocol; old queue
files without a receipt retain their previous one-attempt behavior.

Admission stops with HTTP 503 at 100 pending jobs, 10,000 retained receipts, or
less than 128 MiB free on the queue filesystem. The SQLite ledger is limited to
64 MiB. Reserved but unpublished jobs count toward the queue limit. Receipt-backed
jobs older than seven days expire before execution. Existing receipts can still
be acknowledged at the admission limit. Monitor admission failures in the
receiver journal and restore capacity before retrying rejected deliveries.
These limits bound admission, not disk usage by trusted build scripts.

Each attempt gets an exclusively created log named with repository, commit,
job, and a unique suffix under `/var/lib/basaltwater/cicd/logs/`. Its header and
the executor journal record the job-to-log mapping. Rebuilding the same commit
does not truncate an earlier log; the existing 30-day log cleanup still applies.

## Security and execution boundaries

The root executor is a credential broker; it never executes repository code
as root. Git, local scripts and artifact export run as `cicd-build`, with a
separate UID/group, no supplementary groups or capabilities, no new privileges,
and a clean environment. The managed home is `/var/lib/basaltwater/cicd/build`
(0700); workspaces live in its `workspaces` subdirectory. Deployment keys remain
private under the separate `webhook` home and are not passed to build commands.
The broker starts Python in isolated mode, so build-managed Python packages
cannot become broker startup code. Receipt transactions use the receiver's
identity so SQLite journals remain writable by the receiver.

Before remote deployment, an unprivileged exporter copies build-readable bytes
to a broker-private snapshot. The broker never reads deploy scripts or artifacts
directly from a concurrently writable build checkout. Transfers accept at most
1 GiB of file data and 100,000 file/directory entries, reject symlinks and special
files, and exclude `.git`, `node_modules`, `__pycache__`, and `*.log`. Export has
a five-minute command limit and shares the job deadline. Snapshots preserve
executable bits with safe 0644/0755 modes, ignore supplied ownership, and are
removed after deployment or failure. A hard kill can leave `snapshot-*` under
the CI state directory; remove these only after confirming the executor stopped.

Repository scripts remain trusted for the configured application: they control
the artifacts and optional script executed by the remote `deploy` account.
Protect configured branches, review script changes, and scope each target key
and remote sudo policy to its intended destinations. Build accounts share a
host and network; this is credential separation, not isolation for mutually
hostile repositories. Do not give `cicd-build` sudo rights, host credentials,
or privileged group membership. Private Git access needs a separately scoped,
read-only credential helper in the build home; never reuse a deployment key.
Approved scripts can read those build credentials and access the network.

Upgrade build servers with `patch` before accepting more jobs. Setup creates
the separate account/home and reinstalls selected Node/uv toolchains there;
old workspaces and toolchains under the `webhook` home are left untouched for
operator cleanup. Move only reviewed read-only Git credentials to the new
home, not the old home wholesale. Patch app servers for the restricted remote
sudo policy. Existing bulk mounts at `/var/lib/basaltwater/cicd` still contain
the new build home, snapshots and logs.

- the receiver is localhost-only behind Nginx; expose it through Cloudflare
  Tunnel when that option is configured
- webhook signatures are verified with the stored secret
- webhook bodies are capped at 1 MiB and push fields are validated before queueing
- the executor uses a fresh clone with Git hooks disabled, then checks out the
  signed commit SHA only after verifying it is reachable from the configured
  branch
- repository workspaces include a URL digest, preventing same-name repositories
  from sharing a checkout
- HTTP repository URLs with embedded credentials are rejected so secrets cannot
  leak through queue files or build logs; use the configured Git credential helper
- app-server privilege is exposed only through the validating
  `basaltwater-deploy-admin` helper; the deploy account has no wildcarded root
  `rm`, `mkdir`, or `touch` access
- nginx requests contain only a domain, route, artifact directory, and project
  type. The app server validates them and renders configuration using its own
  installed template and TLS certificates. Raw nginx directives, proxy targets,
  and log destinations cannot be supplied by the build server. Generated sites
  refuse to serve symlinks, and their roots must resolve below an allowed base.
  Each upload uses a per-run staged filename, and the privileged helper
  serializes nginx writes, validation, and rollback on the app server
- existing nginx sites can only be replaced or removed when their file starts
  with the Basaltwater deployment generator marker and their enabled link
  references that file. Administrator-owned or unrelated service sites are
  preserved; adopting an unmarked legacy site requires administrator review
- build logs live under `/var/lib/basaltwater/cicd/logs/`
- build scripts run as the dedicated `cicd-build` user
- `--build-server --node` and `--build-server --python` bootstrap the build
  toolchains for that user
- the receiver writes one bounded job file and the path unit starts the
  executor, so the receiver does not need systemd or polkit privileges
- jobs are consumed after one attempt, including malformed or failed jobs, so
  one bad payload cannot retrigger forever; unavailable or invalid configuration
  retains jobs without starting an attempt
- job and privileged request readers open paths without following symlinks or
  blocking on FIFOs, then require regular files. Job reads remain bounded even
  if a file grows after its initial size check
- durable receipts coalesce signed delivery retries within the retention window

Quick checks:

```bash
sudo systemctl status webhook-receiver.service
sudo journalctl -u webhook-receiver.service -f
sudo journalctl -u cicd-executor.service -f
sudo systemctl status cicd-executor.path
curl -fsS http://127.0.0.1:8080/webhook/health
```

Run `patch` on existing app servers to apply the deploy sudo policy and install
the privileged helper. The helper validates target names and paths before
allowing the deploy account to update an app server.

Upgrade both app and build servers for the structured nginx protocol. The new
helper accepts `install-site` with a staged JSON request and removes the old
`install-nginx` raw-text operation. Mixed versions fail instead of falling back
to installing unvalidated configuration; existing active sites remain in place.

If you need the full setup flow or command syntax, use
[Command-line reference](./COMMAND_LINE.md) and
[Cloudflare tunnels](./CLOUDFLARE.md).

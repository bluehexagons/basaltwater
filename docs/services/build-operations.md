# Operate selected-builder deployments

Use this guide after [enrolling a builder and deploying a target](build-systems.md#start-with-a-selected-builder).
Run controller commands from the same Basaltwater workspace that holds those
saved setups, SSH identities and verified host pins. Builder maintenance runs
through a separate root or sudo administration session.

## Switch an existing source-built target

Use a separate enrolled builder; do not add the build-server role to a small
web target. Both hosts must be Debian 13 x86_64 VMs or physical machines with
working administrative SSH. These commands operate from the controller's
original workspace, where the target setup and verified SSH host keys are saved.
Replace the example hosts, user, key and controller source before running them.

If the builder is not enrolled yet, configure an already reachable host:

```bash
basaltw setup server_lite build.example.com deploy --machine vm \
  --key /home/controller/.ssh/id_ed25519 \
  --ssh-access-source 192.0.2.5/32 --build-server --dry-run
```

Review and repeat without `--dry-run`. This enrolls wildcard project authority
for that controller key and installs the managed toolchains. See
[builder enrollment](build-systems.md#start-with-a-selected-builder) for
restrictive per-project grants and fresh VM provisioning.

Before converting the target:

- Check every saved site's committed root `basaltwater.json`. Node static
  builds and self-contained Go services are supported; a running Node service
  cannot use this delivery flow. Inferred ordinary deployments need explicit
  manifests first. Preserve destination paths and component names, and compare
  generated deployment roots with the installed roots before applying.
- Back up persistent data, saved setup state and serving configuration. Keep
  other setup/deployment controllers idle. Activation restarts affected services;
  the complete multi-application switch is not atomic.
- Schedule a maintenance window for separate applications sharing a domain.
  Each activation publishes the routes currently in the artifact registry.
  During conversion, an ordinary sibling absent from that registry can lose
  its route until its own activation succeeds. A later failure can leave that
  route absent. Including all siblings in the command does not make publication
  atomic; do not assume mixed source/artifact routes on one domain are preserved.
- Check physical bytes and inodes on the controller, builder and target. The
  target retains bundles and also needs extraction/staging space beside the
  live release. Its default 8 GiB retained-storage ceiling is a logical bound,
  not allocated disk space or a promise that a small disk can accommodate it.
  Cache maintenance does not collect target artifacts or release evidence.
  Dry runs do not execute builds, qualify existing-site conversion, or establish
  the target's physical peak headroom. See [capacity limits](#choose-maintenance-by-the-exhausted-resource).

Use `patch` to retain the saved target profile and its complete deployment set:

```bash
basaltw patch web.example.com deploy --machine vm \
  --build-on build.example.com --no-node --no-go \
  --ssh-access-source 192.0.2.5/32 --dry-run
```

The explicit negative toolchain flags clear previously saved installation
requests; they do not uninstall runtimes. Use the target's existing setup user
explicitly; an omitted user defaults to the controller's current username.
Repeat `--ssh-access-source` for all
administration sources that must retain access: an explicit list replaces the
saved SSH source list. The selected target key must also authorize root setup.
Omitted deployment destinations remain selected, and the command rebuilds
**every saved site**. If a repository URL or selector changes, append its
`--deploy DOMAIN[/PATH] HTTPS_URL` pair; this replaces that destination's saved
source. Initial target grants are generated automatically. An already enrolled
target needing changed grants additionally requires `--build-target-auto`.

After reviewing preflight and the backups, repeat the target patch without
`--dry-run`. Verify all routes, service health and persistent data before
considering old build caches or installed toolchains separately. Existing
runtimes and build accounts are retained; this switch alone does not reclaim
their disk space. Later saved deployments use:

```bash
basaltw deploy web.example.com --yes
```

On failure, preserve the printed attempt/journal and inspect the actual target
state using the [recovery procedure](#diagnose-a-refusal). Do not retry blindly,
delete guards or use `--no-build-on` as a rollback. Native qualification covers
selected fresh-target and retained-refresh flows; it does not establish a
populated source-built target conversion or a crash-safe multi-site switch.

## Add a site or change its grants

An ordinary source revision update needs no new policy when its complete
routes, service settings, exports and required tools still fit the existing
grants. Use the [revision update procedure](build-systems.md#update-and-reconcile).
A restrictive builder grant or changed target authority needs policy
updates first; wildcard builder enrollment needs no per-project addition. For generated enrollments, declare the new project on the
builder, then explicitly regenerate the target grants while adding the site:

```bash
basaltw patch builds --build-project docs.example.com 'https://github.com/OWNER/docs.git#tag=v1'
basaltw patch web.example.com --build-target-auto \
  --deploy docs.example.com 'https://github.com/OWNER/docs.git#tag=v1'
```

The builder patch preserves unrelated projects, keys and principals; an
already enabled builder needs no repeated `--build-server`. Managed builder
setup installs Go, Node and Python; project grants restrict which of those
runtimes a build may use. Jobs cannot install host toolchains. The installer
refuses an active worker instead of stopping it.

A shared builder can declare projects for different targets in one command,
even when those servers use the same fixed route, service port, public UDP port
or default deployment root. Each project is checked independently using the
shared manifest planner. Each target still checks its complete deployment set
and refuses collisions within that server. Duplicate builder project IDs remain
invalid. Retired webhook installations require the explicit
[migration procedure](build-systems.md#migrate-retired-installations) first.

The target patch generates grants from the complete merged saved deployment
set, including omitted siblings, then rebuilds and deploys that complete set.
It preserves unrelated grants, existing principals, unmodified limits and
retained-storage settings. Generated application IDs and default roots remain
stable; preserve manifest component names to retain their data identities.
Automatic regeneration refuses a conflicting custom root or
project identity; use an explicit policy update for custom enrollments.
For changed routes or runtime settings, repeat `--build-project` for the
affected builder destination and `--build-target-auto` on the target.
No flag implicitly grants new authority on a different server.
Explicit `--build-target-auto` also re-enables previously disabled target
enrollment after successful generation; it conflicts with `--no-build-target`.

### Generated policy limits

Use repeatable `--build-limit RESOURCE INTEGER` alongside `--build-project`
or `--build-target-auto` to set generated ceilings. Supported resources are
`memory_bytes`, `cpu_percent`, `scratch_bytes`, `artifact_bytes`,
`artifact_entries`, `timeout_seconds` and `log_bytes`. Defaults and protocol
request caps are described in [policy ceilings](build-policy-drafts.md).
Target grants use only the artifact byte and entry ceilings. Overrides apply
to the projects or applications regenerated by that command; other ceilings
on existing grants are preserved.

`--build-max-records N` and `--build-max-bytes BYTES` set the current role's
retained-storage bounds during generation. New enrollments default to 128
records and 64 GiB builder / 8 GiB target storage; patches preserve saved
values unless overridden. These flags accept integers and the shared policy
validators enforce bounds. For example, append
`--build-limit memory_bytes 2147483648 --build-max-records 256` to a builder
project command. Resource ceilings remain bounded by protocol request defaults.

### Custom policy files

A saved setup contains the decoded enrollment document. Editing an original
JSON file does not update the saved setup or either server. Explicitly pass
the reviewed replacement envelope to `patch` to apply and save it.

For enrollments originally generated by `prepare_build_policies.py`:

1. Keep all controllers idle and reconcile any unfinished deployment before
   changing authority. Preserve the current enrollment files and saved setups.
2. Generate drafts in a **new** private directory, including the existing
   destinations that must remain authorized and the new destination:

   ```bash
   python3 scripts/prepare_build_policies.py \
     --builder-key /home/controller/.ssh/build_ed25519.pub \
     --target-key /home/controller/.ssh/web_ed25519.pub \
     --output-dir /home/controller/policies-next \
     --deploy app.example.com 'https://github.com/OWNER/app.git#tag=v2' \
     --deploy docs.example.com 'https://github.com/OWNER/docs.git#tag=v1'
   ```

3. Review both complete envelopes against the current authority. They replace
   their respective enrollment, rather than adding individual grants. Retain
   other principals, keys, projects and applications still needed. A builder
   shared by several targets needs the union of their project grants; a target
   receives only its own applications. Preserve existing IDs, deployment roots,
   component names and capacity settings. The draft utility supports at most
   16 destinations and one principal; it does not merge existing enrollments.
4. Apply the builder envelope first. Include `--build-server` when supplying a
   builder policy, even on an existing builder:

   ```bash
   basaltw patch build.example.com --build-server \
     --build-host-policy /home/controller/policies-next/builder-enrollment.json
   ```

   Managed builder setup installs Go, Node and Python automatically; a policy
   grant does not let a job install host toolchains. Setup refuses an active
   worker instead of stopping it; inspect a refusal before trying again.
5. Preflight the target with its replacement envelope and new destination:

   ```bash
   basaltw patch web.example.com \
     --build-target-policy /home/controller/policies-next/target-enrollment.json \
     --deploy docs.example.com 'https://github.com/OWNER/docs.git#tag=v1' \
     --dry-run
   ```

   Repeat without `--dry-run` after reviewing the result. The patch retains
   omitted sibling destinations and the saved builder. It rebuilds the complete
   saved deployment set and applies the full profile; it is not an enrollment-only
   operation. Include any changed existing source selector in the patch too:
   generating a draft from `#tag=v2` does not update that saved selector.

Handwritten or template-based enrollments may use different application/project
IDs from generated drafts. Update their existing grant maps instead of replacing
them with newly generated IDs. Removing a grant revokes future transport
authority; it is not an application removal or data migration procedure.
Policy changes do not clear admission history or recover an uncertain deployment.

## Upgrade and restart a build host safely

Before the first upgrade from a worker that predates shared build capacity,
let its builds finish, then stop `basaltwater-build-host.service` through an
administrator session. Setup and refresh refuse an active, activating or
deactivating legacy worker, or an uncertain service query, before replacing
source. They inspect the installed worker's coordinator participation; a
coordinator directory created by another producer does not establish safety.
Neither command stops a busy worker automatically. Preserve job evidence and
inspect the refusal before retrying. Setup reenables the managed worker after
installing its coordinated implementation.

An explicit ordinary-host restart runs as a named target-side systemd job. It
reacquires the setup maintenance lock and idle build capacity after setup,
then persists a reboot drain before requesting reboot. If a build has started,
the job refuses reboot; retry when idle. Setup prints the job name; inspect
`journalctl -u UNIT.service --no-pager` for a refusal. With
`--wait-for-restart`, a failed job makes the command fail promptly. Without
waiting, successful dispatch only confirms that the job was queued.
Reboot intent remains drained if the reboot request fails or its outcome is
uncertain. Use the existing reboot cancellation or confirmed boot recovery
procedure; do not delete coordinator markers to reopen intake. Proxmox's
explicit restart retains its graceful guest shutdown and evacuation checks.
For a provisioned VM with pending hardware changes, setup also acquires idle
capacity inside the guest and persists its reboot drain before asking Proxmox
to restart it. `--shutdown` uses the same guest preparation before graceful
poweroff. A busy or unreachable guest prevents either power request. Failed or
uncertain provider requests retain a distinct external-power drain. Inspect
the provider's task state before arranging the power operation administratively;
a confirmed subsequent boot clears the previous-boot intent. Local
`shutdown -c` cannot cancel provider tasks and preserves this external drain.

## Choose maintenance by the exhausted resource

These limits are independent. Free filesystem space does not establish logical
quota or record capacity, and reclaiming logical charges does not erase history.

| Reported limit | Where it lives | Appropriate action |
| --- | --- | --- |
| Builder logical bytes | Builder input reservations and retained payloads | Inspect capacity and the exact terminal request; [settle unused charges](build-systems.md#inspect-capacity-and-retain-evidence). Optional pruning or bundle release has separate destructive effects. |
| Builder input or job/history records | Builder stores | Settlement, pruning and bundle release retain these identities. No remote history retirement command exists. Plan larger supported enrollment limits before exhaustion; independent history bounds still apply. |
| Target retained bytes or records | Target staging/admission/release stores | No general target collection command exists. Preserve installed releases and replay evidence; reconcile capacity with an administrator before another delivery. |
| Controller active attempts | Controller `build-setups/HOST-HASH` | Explicitly archive a proven completed attempt as shown below. Failed or unproven attempts need reconciliation. |
| Physical bytes or inodes | Each host's filesystem | Inspect that filesystem and retain required evidence. Logical settlement and controller archival do not promise physical reclamation. |

The policy draft defaults are 128 builder input records and 128 target artifact
records. A successful six-application deployment consumes six of each, so even
otherwise empty stores have room for at most 21 such deployments under those
limits. Other targets, failed attempts and independent history limits can reduce
that headroom. The controller separately permits 32 active attempts per target.
See [policy ceilings and request limits](build-policy-drafts.md) when sizing jobs.

## Archive completed controller attempts

Start with the retained attempt directory printed on successful setup. Its last
path component is the attempt ID; its parent contains the host's `setup.json`.
Use those original absolute paths, including after an archive rename.
Run from the controller checkout, without sudo, as the owner of that workspace:

```bash
python3 -B -m lib.build_setup_history \
  --journal /absolute/workspace/build-setups/HOST-HASH/setup.json \
  --attempt ATTEMPT_ID
```

Review the identities and copy the returned revision into a separate request:

```bash
python3 -B -m lib.build_setup_history \
  --journal /absolute/workspace/build-setups/HOST-HASH/setup.json \
  --attempt ATTEMPT_ID --archive --expected-revision SHA256
```

This renames the attempt to `archived-ATTEMPT_ID`, freeing one controller slot.
It preserves payloads, receipts and historical absolute paths and consumes the
same disk space. Inspect or retry an archive using the same original journal,
attempt ID and revision; never replay paths retained inside an archive.
An unfinished host setup guard refuses archival, even for another attempt.
Older attempts qualify only when their retained evidence proves final setup
success. No files or guards are deleted. See the [archival contract](../agents/contributing/build-setup-history.md)
for exact proof, locking and interruption rules.

## Diagnose a refusal

| Symptom | Next check |
| --- | --- |
| Builder selection is missing or ambiguous | Use an exact unique saved host, name or tag in the original controller workspace. Fresh bare `--build-server` enrolls wildcard project authority. |
| SSH identity or host verification fails | Check the selected private key, its enrolled Ed25519 `.pub` companion, root setup authorization and workspace host pins. Ambient SSH agent/configuration is not used for managed transport. See [selection requirements](../agents/contributing/build-selection.md). |
| Controller SSH source is rejected | Use the source address observed by this target in `--ssh-access-source`; a cloud VPS usually sees a public egress or VPN address. |
| Application match or runtime grant fails | Compare the pinned manifest's entire component/route/runtime map with the target envelope, and its project/tools with the installed builder grant. Regenerate and review authority only when needed. |
| Build tool or selected Node/package-manager version is unavailable | Provision a supported matching builder toolchain, then inspect its observed capabilities. Jobs cannot install host toolchains; target tool flags do not repair the builder. |
| A deployment failed or its reply was lost | Preserve the guard, attempt directory, pinned sources, request/admission IDs and receipts. Use the offline inspector below, then inspect actual remote state before operator reconciliation. |
| Archive reports `revision_changed` or `busy` | Inspect again after the cooperating operation finishes; use the current revision only after review. Do not delete locks or bypass a setup guard. |

For an unfinished controller setup, inspection is separate from completed-history
archival:

```bash
python3 -B -m lib.build_setup_recovery \
  --journal /absolute/workspace/build-setups/HOST-HASH/setup.json
```

The inspector contacts no host and reports remote state as `unobserved` and
payload contents as `unverified`. It does not resume, reset, cancel or clear
anything. Neither a new deployment ID nor `--no-build-on` is a recovery action.
The [coverage map](../plans/testing-coverage.md) distinguishes live qualification,
automated checks and remaining public Tunnel/ACME, crash and power-loss gaps.

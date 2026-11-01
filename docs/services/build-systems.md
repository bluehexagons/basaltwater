# Choose and operate a build system

Managed SSH builds and signed GitHub push webhooks use one policy, input store,
durable worker, scratch space, limits, logs and artifact pipeline. There is no
second webhook executor. Optional GitHub Actions runners retain GitHub's vendor
runner protocol and share durable host capacity with the managed worker.

## Start with a selected builder

Use Debian 13 x86_64 VMs or hardware with native systemd. Bare `--build-server`
enrolls the selected Ed25519 SSH public companion for principal `deploy` with
wildcard projects (`*`) and installs Go, Node and Python automatically.
No `--build-project` is required. Repositories sharing a builder must be
mutually trusted. Optional `--build-project DOMAIN HTTPS_REPO` selects restrictive
grant mode; explicit `--build-host-policy FILE` supports custom authority.
On updates the current SSH public companion must match existing enrollment.

The controller must reach both builder and target over SSH. Source and artifacts
pass through the controller; no inbound builder-to-target connection is needed.
A LAN builder can serve a LAN target or a cloud VPS. Keep the selected private
key and verified workspace host pins on the controller.

These fictional examples provision fresh VMs through registered node
`lab-node`, with VM image pool `image-pool` and disk pool `vm-pool`.
Substitute your pools, addresses and published sample repository; the repository
must contain a committed root `basaltwater.json`.

```bash
basaltw setup server_lite 192.0.2.20 operator \
  --machine vm --provision-on lab-node --name build-local \
  --image-storage image-pool --storage root vm-pool 80G \
  --memory 4G --cores 4 --key "$HOME/.ssh/id_ed25519" --build-server

basaltw setup server_web 192.0.2.30 operator \
  --machine vm --provision-on lab-node --name web-local \
  --image-storage image-pool --storage root vm-pool 20G \
  --memory 1G --cores 2 --key "$HOME/.ssh/id_ed25519" \
  --ssh-access-source 192.0.2.10/32 --build-on build-local \
  --deploy site.example.com https://github.com/example/site.git
```

Initial `--build-on SAVED_BUILDER --deploy DOMAIN HTTPS_REPO` generates exact
target authority from the complete manifests and builds through the SSH core.
It reserves applications, builds, stages verified bundles and activates under
release guards. A builder failure does not fall back to a target build.
Existing hosts require administrative SSH access and verified pins first.
See [deployment restrictions](deployments.md#build-on-a-saved-builder) and
[sample manifests](../examples/builds/README.md).

## Update and reconcile

Use `basaltw patch web-local --deploy site.example.com HTTPS_REPO` to update
saved source, then `basaltw deploy web-local --yes` for later saved deployments.
Retain exact request IDs, guards and receipts after an uncertain result.
See [policy updates and recovery](build-operations.md).

## Optional intake

Add `--github-runner` to `server_lite` or `server_web` for Actions jobs.
The dedicated `server_github_runner` remains a runner-only convenience profile
using the same installer. See [registration](github-runner.md).
Add [signed webhook builds](webhook-builds.md) for workflow-free build/test artifacts.
Webhooks do not deploy targets, publish releases or create status checks.

## Shared capacity and maintenance

The fixed lock is `/var/lib/basaltwater-build-coordinator/slot.lock`,
mode `0660`, owned by `root:basaltwater-builds`. The coordinator's `drain`
marker blocks new work. Runtime activation acquires and retains the drain before
code replacement through the entire full setup, including package operations.
Scheduled APT updates and restart checks defer while build capacity is busy.
Scheduled reboot grace periods retain a drain. Shared boot recovery removes
only a valid previous-boot reboot drain when the capacity lease is empty.
To cancel a scheduled reboot explicitly, run
`sudo /usr/bin/python3 /opt/basaltwater/common/service_tools/auto_restart_if_needed.py --cancel`.
Cancellation confirms `shutdown -c` before releasing that reboot drain;
uncertain build leases and other maintenance drains remain for inspection.
An Actions job waits at most 60 seconds for capacity, then is refused;
the webhook queue and worker retry until capacity is free.
Normal runner-guard exit clears its lease only when no cgroup orphans remain.
Crashes, guardian failures and remaining cgroup orphans retain the lease.
Root must inspect processes, cgroups and retained lease evidence before clearing it;
never blindly delete the lease or lock.

## Inspect capacity and retain evidence

Use a separate root administration shell on the builder:
```bash
sudo /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py --inspect-capacity
sudo /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py --inspect-retention REQUEST_ID
```
Settlement, pruning and bundle release require separate explicit decisions and
current revisions. See [retention](../agents/contributing/build-retention.md)
and [capacity operations](build-operations.md#choose-maintenance-by-the-exhausted-resource).

## Migrate retired installations

The `cicd` command, `--cicd`, `--app-server` and `--deploy-target` are retired,
as are the old executor, `webhook_manager` and `deploy_admin` flows.
Setup refuses `/etc/basaltwater/cicd` or old sudoers/unit evidence.

1. Stop webhook intake and Actions assignment; finish or explicitly stop jobs.
2. Preserve configuration, credentials, logs and recovery evidence privately
   outside Git. Disable `webhook-receiver.service`, `cicd-executor.service`
   and `cicd-executor.path`.
3. Revoke old rsync sudo and SSH deployment grants, including the legacy
   `/etc/sudoers.d/deploy-nginx` authority for
   `/usr/local/sbin/basaltwater-deploy-admin`. Inspect any local rsync grants
   separately; do not assume this is the only installed rule.
4. Archive old configuration outside its active path, then run new setup.
   Migration does not automatically delete user data.

Existing Actions runner units use a different old lock. Let jobs finish and
stop those units before upgrading; credentials are retained by the upgrade.

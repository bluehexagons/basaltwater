# Privilege execution qualification

## Status: active

Repair approved command execution on broker-managed Debian VMs and give human
review a practical expiry window. The observed VM has a 64 GiB home disk with
a 32 GiB ext4 partition; expanding that partition/filesystem is a separate,
explicitly approved live operation after the executor is qualified.

The current broker executes arbitrary approved argv inside its own
`ProtectSystem=strict` / `PrivateDevices` / `ProtectHome` sandbox. Approval of
`install -d /srv/...` therefore succeeds while execution fails. The existing
five-minute default expiry also regularly expires before an operator can review
the request. [Privilege approvals](../PRIVILEGE_APPROVALS.md) and the
[administrator reference](../PRIVILEGE_BROKER_REFERENCE.md) own durable behavior.

## Implementation and acceptance

- Retain the broker and approval service sandboxes. Dispatch only already
  approved command requests to a root-owned helper in a bounded transient systemd
  unit. Preserve literal argv, clean environment, closed interactive input,
  discarded privileged output, durable claims and no automatic replay.
- Expose safe exit/failure metadata without command output; preserve the audit
  database across schema migration and interrupted execution.
- Use a one-hour review default, permit an administrator-selected 30-second to
  24-hour policy window, and retain existing policy choices on setup reruns.
- Cover approval, rejection, literal argument transport, executor ownership,
  failures/timeouts, database migration, expiry, and existing setup preservation.
  Run focused tests, `make check`, and `make coverage` in the project environment.
- Publish tested source, refresh the VM through its existing approved maintenance
  operation, then exercise the actual approved `/srv` directory creation. Update
  this VM's expiry explicitly and verify it on a fresh request.
- Confirm the home disk layout before growing `/dev/sdb1`, back up the partition
  table, grow only into its adjacent free space, grow ext4 online, and verify
  mount identity, partition start, capacity and host health afterwards.

Artifacts remain in the active ignored Antistatic project workspace. Fold lasting
contracts into the references after implementation and retain unresolved live
checks here until qualification completes.

## Implementation evidence

The executor, review-window defaults, safe execution metadata and audit migration
are implemented. A response-size preflight also rejects requests whose duplicated
review argv would exceed the socket protocol limit before creating a pending
request. The 75 focused cases pass. `make check` and `make coverage` pass with
5,478 cases / five platform or expensive-test skips; wheel, CLI documentation,
link, compilation and generated-asset checks pass. Broker/approval confinement
remains enabled. Live refresh, directory creation, explicit policy-window update
and home growth remain the next qualification steps.

# Run and maintain storage operations

[Topic index](storage-operations.md)

## Inspect and run operations

In the web panel, open **Scheduled jobs → Load scheduled jobs** for a read-only
Storage integrity section alongside timer/process status. Its private root-produced
snapshot refreshes every five minutes and shows bounded findings and scan history.
Stale or unavailable snapshots are explicit; loading the page never starts a scan.
Use the CLI for the complete report and remediation.

```bash
basaltw scrub status fileserver
basaltw scrub inspect fileserver --file /srv/data/example.bin
basaltw scrub verify fileserver --file /srv/data/example.bin
sudo systemctl status storage-ops.timer
sudo systemctl start storage-ops.service
sudo journalctl -u storage-ops.service -n 200 --no-pager
sudo ls -l /var/log/storage-ops /var/log/scrub
sudo cat /var/lib/storage-ops/last_run.json
```

The service uses `/run/lock/storage-ops.lock` to prevent overlapping runs and
writes its last-run timestamps atomically to
`/var/lib/storage-ops/last_run.json` after each completed operation, so a later
failure cannot lose earlier completion timestamps. Scrub timestamps represent completed scans,
including scans that found unrepairable files, or the initial reference that
defers a first full scan. Inspect the scrub report to establish actual full-scan
completion. Sync timestamps represent success.
Incomplete or skipped operations remain due for a later run. The timer itself is
hourly; each specification's interval is enforced by the orchestrator.
The `_attempts` mapping in that state file records consecutive operational
failures and `retry_after` Unix timestamps separately from completed cadence.
Starting the service manually still respects this backoff; targeted CLI verify
and recovery remain available under the shared operation lock.

Only a missing last-run file starts a new schedule. Malformed JSON, invalid
timestamps or retry entries, symlinks, special files, and files over 1 MiB stop
all scheduled operations before any sync or parity work. The service reports
failure and retains the file; restore a verified backup or explicitly quarantine
it after reviewing cadence and retry history. Quarantining it resets that history
and delays the first full scrub by its configured interval.

The root-owned `/var/lib/storage-ops/controls.json` stores paused routine
identities independently of this cadence/retry history. Paused mirrors do not
copy files. Paused scrubs skip both full verification and daily parity updates;
their declarations and recorded findings still protect mirrors. A missing
control file means no pauses. Malformed, unsafe or oversized control state
stops work rather than resuming it. A routine's identity uses its kind and
lexically normalized endpoints; interval/redundancy edits retain the identity.
Controller changes to endpoints create a new identity, so review pause state
after deploying changed specifications.

The [approved routine worker](../agents/privilege-broker-reference.md#storage-routine-operations)
can save one configuration change or run one reviewed routine. Saving
reconciles the hourly units without an initial copy or parity build. A selected
run bypasses cadence and retry delay once, including for a paused routine, and
retains ordinary mount checks, mirror findings protections and history recording.
It does not resume a paused routine. Any unfinished target setup or routine-save
marker stops scheduled and selected work until recovery is resolved.

## Change or remove storage work

Use the normal saved-configuration flow to change storage specifications:

```bash
basaltw patch fileserver admin \
  --sync /srv/data /mnt/backup/data weekly
basaltw deploy fileserver
```

Review the saved configuration with `basaltw info fileserver` before
changing a production mirror. Existing storage state and logs are retained;
the next service run uses the saved specification set.

Notifications are shared with maintenance and security services. See
[Notifications](../services/notifications.md) for target types and delivery behavior.

## Troubleshooting

- If a run skips a path, verify the mount with `findmnt --target PATH` and
  inspect the `storage-ops.service` journal.
- If parity verification reports an unrepairable file, restore it from an
  independent copy; parity cannot repair damage beyond the configured
  redundancy.
- If a run is already active, the second invocation exits cleanly after
  reporting the lock rather than running concurrently.

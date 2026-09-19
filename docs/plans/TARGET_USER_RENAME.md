# Managed target-user rename

This plan defines the `basaltw user rename` operation for changing the
login name of the account stored as a target's setup username. The public
operation runs from the controller. It does not add a separate administrator
command intended to be run locally on the target.

## Goals and boundaries

The operation must preserve the account's numeric UID, primary identity, home
content, SSH access, supplementary groups, and Basaltwater-managed services.
It must update the target's current machine/setup state and the controller's
current setup cache. Historical setup records remain unchanged.

The operation is deliberately limited to local Debian accounts and targets
with systemd. It must reject `root`, directory-service accounts, existing
destination names, ambiguous homes, and unmanaged configuration that refers to
the old name. Arbitrary third-party application data is not rewritten by
blind text substitution.

## Public command

```text
basaltw user rename HOST NEW_USERNAME [--admin-user USER] [--key PATH]
                           [--new-home PATH | --keep-home]
                           [--dry-run] [--yes] [--resume OPERATION_ID]
```

The old username is inferred from the saved setup configuration and checked
against the target's `machine.json`, `setup.json`, and local account database.
`--admin-user` is useful when the saved target account is the account being
renamed; a separate administrative identity is preferred. The command
requires non-interactive `sudo` so the controller can continue after the
target user's SSH session is terminated.

## Execution phases

1. Acquire a controller-side host lock and validate host, destination
   username, key path, and option combinations.
2. Run a target preflight as root. Record the old UID/GID, shell, home,
   private-group relationship, linger state, active managed units, and exact
   configuration paths that will change.
3. Create a root-owned operation directory and manifest under
   `/var/lib/basaltwater/user-renames/<operation-id>/`. Stage a persistent,
   self-cleaning systemd oneshot unit that runs the target migration helper,
   and submit its start without waiting on the SSH session it will terminate.
4. Start the unit and return the operation ID. The helper makes the old shell
   unavailable, stops managed services/timers, terminates the user's sessions
   and user manager, waits for all processes with the recorded UID to exit,
   and then performs the identity change.
5. Rename the login, move the conventional home when requested, rename a
   private primary group, move username-keyed mail/crontab state, restore the
   original shell, and update linger state. Recovery identifies the account by
   UID, so a partially completed identity phase can be resumed.
6. Rewrite only schema-known path values in saved setup configuration. Update
   the username in `machine.json` and `setup.json` atomically.
7. Reconcile Basaltwater-owned sudoers files, systemd units, timers, mount
   units, and user-scoped wrappers. Unmanaged references discovered during
   preflight are blockers rather than being rewritten opportunistically.
8. Reload systemd, restore the active/enabled state recorded during preflight,
   and verify the new account, home, UID/GID, SSH material, state files,
   managed units, sudoers syntax, and absence of old-name references in
   managed locations.
9. Write a non-secret status record, remove temporary manifests/backups/unit
   files, and leave the final status available through the administrative SSH
   connection.
10. The controller polls using the administrative identity and, when needed,
    the new username. Only after a successful new-name SSH verification does
    it update the workspace setup cache. An interrupted controller can resume
    by operation ID without changing historical records.

## Safety and recovery

- Failures restore the original shell on whichever account name still owns the
  recorded UID, leaving either the old or renamed login usable for recovery.
- After the identity phase, recovery rolls forward from the durable phase
  marker instead of attempting an ambiguous automatic rollback.
- Account database files and managed configuration receive root-only backups
  for the operation lifetime; they are removed after successful verification.
- Existing unfinished target setup or rename operations block a new rename.
- A destination home or account/group collision, mounted or symlinked home,
  cron/mail or managed SMB credential collision, unmanaged
  `User=`/SSH/sudoers reference, or a process supervisor that recreates the
  account's processes aborts the operation before cutover.
- Old names in logs, journal history, and historical setup records are left
  intact for auditability.

## Implementation seams

- Add parser/dispatch entries in `lib/sysadmin_cli.py` and the main command
  dispatch in `basaltwater.py`.
- Add controller orchestration in `lib/sysadmin_user.py`, using the existing
  SSH builders, sudo preflight, workspace cache, and host credentials.
- Add the internal target helper in `lib/user_rename.py`; it is invoked by the
  staged systemd unit and is not exposed as a normal local workflow.
- Extend cache/state helpers only where needed to preserve metadata while
  changing the current username and path-valued fields.
- Document the command in `docs/SYSADMIN.md` and `docs/COMMAND_LINE.md`.

## Verification

Tests must cover validation, manifest generation, parser/dispatch behavior,
preflight blockers, process/session cleanup with mocked system calls, private
versus shared primary groups, home moves, state/cache updates, managed unit
rewrites, operation resume, controller interruption, and failure before and
after the identity phase. A disposable Debian systemd target should exercise
the full SSH disconnect/reconnect handoff; repository tests must never mutate
the development host.

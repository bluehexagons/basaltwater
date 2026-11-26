# MB-R056 feedback intake, 2026-11-26

**Recorded and triaged only; implementation and native qualification deferred.**
An authorized consuming controller owns the shared desktop. This intake changes
no runtime code, installed source, helper, service, permission, grant or live
desktop state. It activates no maintenance, fixture, game or restoration test.

Source: the received controller handoff and its
[sanitized feedback report at consuming revision `ee4cdd8`](https://github.com/bluehexagons/game-testing/blob/ee4cdd8/games/mythic-battle/runs/MB-R056/basaltwater-feedback.md).
The GitHub connector returned 404 for that report during intake; its contents
were not independently retrieved. Findings below are attributed to the handoff,
not reproduced here. No private evidence was inspected or copied into Git.

The handoff reports native KDE Wayland with matching CLI/worker `55d7b7d8`.
That is reported run provenance, not an observation of the current installation.

## First: portal-start diagnostic completeness

Reported behavior: two KDE Start-response timeouts left prompt visibility
unknown. A subsequent fresh owner approval covered the match and saved a grant.
Reuse after a restart remains untested. Neither timeout establishes a visible
prompt, denial, sleeping display or failed token restoration as the cause.

Triage: an observability/recovery follow-up, not a confirmed portal defect.
Reuse `desktop/display_power.py`, `desktop/portal.py`,
`desktop/native_session.py` and `desktop/native_grants.py`; current source already
reports display power in doctor, current/last portal stage and selected grant
metadata. First compare the actual timeout receipts with those existing fields
at a separately authorized review; add only demonstrated missing information.

Acceptance for a future bounded assignment:

- Retain timestamped display-power evidence with unknown/partial results; do not
  infer power from a socket, unlock state or successful Start request.
- Preserve the last portal stage and distinguish fresh access from attempted
  restoration, saved replacement, consumed-without-replacement and unavailable
  token disposition. Report metadata only, never token contents or identifiers.
- Keep prompt visibility explicitly unknown unless independently observed.
  Saved-grant presence and restoration attempt do not establish restored access.
- Use mocked fresh/restore, timeout and unavailable-observation cases first.
  Qualify saved-grant reuse only at an owner-selected maintenance point after
  desktop ownership is released; preserve the failed attempts and later success.

## Next: capture-free external-process observation

Reported behavior: Steam Quit used completed input-only delivery plus separate
exact PID/start-identity checks for the game and wrapper, avoiding desktop capture.
Triage: a closeout convenience gap, not evidence of failed input or cleanup.
Reuse [process receipts](../../workstations/native-qa-evidence.md#retain-process-identities)
and the existing one-shot `desktop/evidence.py` closeout states before proposing
another identity format. An owned launcher's status alone cannot cover an
externally launched child. Any future observer must remain read-only.

Acceptance for a future bounded assignment:

- Accept explicitly retained current-account identities for game and wrapper
  separately, with boot scope and bounded process count, cadence and deadline.
- Preserve input delivery independently; observe running, absent, replaced and
  unavailable identities without capture, input replay, signals or Steam control.
- Reject incomparable boot identities; treat permission/read failures and deadline
  expiry as unresolved. PID reuse is replacement, not the original process running.
- Retain observation timestamps and unknown exit code/normal-exit status. Absence
  does not prove normal exit, cloud synchronization or absence of every game child.
- Cover running-to-absent, replacement, boot mismatch, unavailable reads and timeout
  with synthetic/mocked tests before any authorized disposable native check.

## Retained positive feedback and disposition

The handoff reports that preview mapping worked and the first-purchase re-audit
matched all three links. Preserve these as selected consuming observations; they
do not establish general targeting accuracy or semantic correctness for other
transactions. No mapping change or broader qualification follows from this intake.

Both follow-ups remain proposed. Source review and documentation checks establish
only this triage; the consuming controller retains exclusive ownership of live
input, recordings and cleanup. Private evidence needs no action for this intake.

# Web-panel scrub triage and disposition

Status: Implemented with offline regressions and selected installed Debian
qualification on 2026-11-08; see the [native evidence and remaining boundaries](../testing/evidence.md#storage-triage-and-native-filesystems).
Extend the existing **Shares & Data → Scrubs & findings**
view with bounded bulk triage and reviewed file disposition. Single-file verify,
repair, restore and accept already use the panel's approval flow; this plan adds
bulk management for findings where isolating or removing the file is the chosen
integrity decision.

Implementation limits: ten selected visible findings from one task per batch,
10,000 canonical parameter bytes, and the existing six-hour worker deadline.
Selection clears on navigation. Quarantine requires Linux `renameat2` and the
file's filesystem/subvolume; permanent deletion uses the same private staging before
unlinking. Disposition rejects files covered by another configured scrub task
or inside any configured parity database; coordinating dispositions across
multiple reports is unsupported. Mirror protection release is intentionally
unavailable and remains a separate future reviewed operation.

## Scope

- Select findings by scrub task and current filters, with a fixed batch limit.
  Review the exact paths and one action before submitting a batch.
- Initial batch actions: verify, quarantine as corrupt, and permanently delete.
  Keep repair and restore file-specific; each needs its own candidate or copy.
  Keep acceptance one file at a time because it replaces the parity baseline.
- Show per-file progress and outcomes, including skipped stale entries and
  partial completion. An uncertain or interrupted batch is never replayed
  automatically; retry only individually reviewed remaining files.

## Disposition and safeguards

- Quarantine is the recommended way to remove corrupt content from its active
  path while retaining the file. Move it atomically into a dedicated,
  root-owned, mode-0700 quarantine directory on the same filesystem/subvolume.
  Nested mount and Btrfs subvolume boundaries use their own quarantine directory
  inside the scrub root. Read-only sources remain unchanged. Preserve
  its original relative path and use a collision-safe `.corrupt` name. Refuse
  to commandeer an existing unrecognized quarantine directory.
- Exclude quarantine content from scrub inventory and mirror traversal. Keep
  the original parity and a disposition record; do not turn the quarantined
  file into a new protected baseline.
- Record intentional quarantine or deletion as a distinct disposition, not as
  healthy data. Future scans must not reopen the original path as an ordinary
  missing-file finding. A file that later appears at that path needs fresh
  verification.
- Source disposition does not imply deleting a mirror copy. Keep mirror
  protection for disposed paths until an operator explicitly reviews and
  approves releasing it. Retain parity after permanent deletion as recovery
  evidence. Reject or clearly handle multiply hard-linked files.
- Bind every item to its scrub task, finding revision and file identity.
  Preflight the complete selection before effects, then recheck each item under
  the existing setup guard and storage operation lock immediately before its
  action. Record durable per-item results so an interruption cannot repeat
  completed moves or deletions. Retain batch parameters and outcomes in root-only
  per-job claim records even after another job replaces the panel's latest result.
- Permanent deletion gets a separate review listing every path, a count-based
  typed confirmation. The broker automatically authorizes the confirmed scrub
  action without another approval-page interaction. Explain that source bytes
  are removed; keep the finding/disposition history and parity metadata.

## Acceptance

- The panel can select a bounded set of findings, review exact targets, and
  report a result for every item without requiring a shell for supported
  verification, quarantine or deletion.
- Changed findings or file identities are rejected before their item is acted
  on. Partial batches show completed, skipped and unresolved items without
  replaying completed operations.
- Quarantined content is absent from normal scrub and mirror traversal. The
  original finding remains traceable, parity is retained, and mirror copies are
  preserved until separately approved for removal.
- Permanent deletion requires the exact reviewed batch and explicit
  confirmation. No operation deletes parity or bulk-accepts new baselines.
- Tests cover stale selections, path collisions, symlinks, hard links, worker
  interruption, partial results, subsequent full scans, and sync protections.
  Update the [Shares & Data guide](../services/shares-data.md),
  [scrub recovery guide](../storage/scrub-recovery.md), and
  [storage operations guide](../storage/storage-operations.md).

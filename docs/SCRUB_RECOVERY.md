# Inspecting and recovering scrub findings

Update the controller's `basaltw` and rerun setup on the NAS before using these
commands. Commands read the NAS's saved scrub configuration; they do not start
a full-directory scrub or change its schedule. The default SSH account is
`root`, matching setup, and the saved SSH key is reused. Override it with
`--key PATH`; `--username USER` requires non-interactive sudo on the NAS.
Interactive SSH can prompt for an encrypted key. Use `localhost` to operate on
the local system as root.

## Inspect and verify

```bash
basaltw scrub status nas
basaltw scrub status nas --all --json
basaltw scrub inspect nas --file /srv/data/photo.jpg
basaltw scrub verify nas --file /srv/data/photo.jpg
```

`status` lists open findings, classification, first detection, last check, and
suggested action, plus the last full scan. Full scans and daily parity updates
are recorded separately. `--all` includes resolved records. `inspect` shows the saved
finding, file identity, and parity paths without running PAR2. `verify` checks
only the named file against existing parity and updates its finding; it does
not repair or regenerate anything. Healthy verification resolves an existing
finding. Missing parity is not considered verified health.

All file paths are **absolute paths on the NAS**, including backup paths.
Commands reject paths outside configured scrub roots, symlinks, and paths
inside the parity database. If multiple scrub jobs protect the same file, use
`--directory /srv/data --database /srv/parity` to select exactly one. These
selectors also filter `status`.

Classification uses PAR2's documented result codes, along with observations of
file changes during verification. The report retains up to 8 KiB of PAR2 output
per finding and the last 20 record transitions. See the
[upstream PAR2 reference](https://github.com/Parchive/par2cmdline/blob/master/man/par2.1).

| Category | Meaning and next action |
| --- | --- |
| `unrepairable` | Insufficient recovery blocks; restore from an independent copy. |
| `repairable` | PAR2 reports sufficient recovery data; use targeted repair. |
| `repair_failed` | Repair did not yield valid content; investigate or restore. |
| `missing_parity` | No usable baseline found; confirm healthy content before accepting it. |
| `invalid_parity` | Critical parity information is invalid or incomplete; recover parity or explicitly establish a new baseline. |
| `io_error` | Check mounts, permissions, and disk health, then retry. |
| `changed_during_scan` | Verification was inconclusive because the file or parity changed; stop writers and retry. |
| `changed_unverified` | A newer source timestamp or pending remediation needs verification; intent is unknown. |
| `missing_file` | Verification or the directory inventory found a protected file absent; its parity is retained. |
| `tool_error` | PAR2 could not complete normally; inspect its evidence. |

A timestamp cannot prove an intentional edit. Scheduled maintenance preserves
existing parity rather than replacing it when source timestamps advance.
Newer content that differs from parity requires an operator decision: repair
to the old baseline, restore an independent copy, or accept the new content.

## Recover or accept one file

```bash
basaltw scrub repair nas --file /srv/data/photo.jpg
basaltw scrub restore nas --file /srv/data/photo.jpg --from /mnt/backup/photo.jpg
basaltw scrub accept nas --file /srv/data/photo.jpg
```

Each command asks for confirmation; `--yes` supplies it explicitly for scripts.
There is no bulk accept, automatic deletion, or automatic acceptance of damaged
content.

`repair` copies the existing file and parity into a private workspace, runs
PAR2 repair there, then independently verifies the candidate. `restore` copies
the supplied backup into that workspace and verifies it against existing parity.
A rejected candidate leaves the live file intact. A successful candidate is
published through a temporary file beside the live file and an atomic replace.
Existing ownership and permissions are retained when replacing a file.

`accept` means **you have confirmed that the current content is the desired
baseline**. It builds and verifies new parity in staging before replacing the
active parity set. It does not prove that the current content is historically
correct. It can also establish protection when parity is missing or invalid.
Absent and empty files cannot be accepted as PAR2 baselines. Restore and repair
require existing parity so a candidate cannot silently become a new baseline.

## Persistence, locking, and recovery copies

Reports are stored at `DATABASE/.basaltwater-scrub/findings.json`. The metadata
directory name is reserved. Original files, original parity, staged candidates,
and an operation manifest are retained in private `recovery-*` directories
beside the report. The command result includes their location. These copies
consume additional space on the parity volume and are not automatically pruned.
Keep them until you have confirmed recovery and made an independent backup.
They are recovery evidence, not a replacement for an independent backup.

Commands share `/run/lock/storage-ops.lock` with scheduled storage work and
refuse to run while it is busy. Required configured mounts must be present.
Keep protected files and parity stable during recovery: ordinary concurrent
writes are checked, but these tools do not provide filesystem snapshots or
protection from a hostile writer racing path checks.

Findings survive daily parity maintenance and setup reruns. A successful fast
maintenance pass does not resolve them. Inventory checks preserve parity for
all missing protected files and never delete retained recovery copies. Intentional
deletions therefore also require review; there is no automatic parity pruning.

Accepting a baseline updates a multi-file parity set, so it is not an atomic
transaction. The original set and a persisted operation manifest are retained
before publication. If interrupted, the finding stays open; inspect the
`recovery-*` directory and rerun explicit acceptance only after checking the
source. No command silently discards a corrupt report or rebuilds parity for
an already flagged file whose parity is missing.

Old log-only errors are not imported. The next scheduled scan, or a targeted
`verify`, creates findings. An absent report means no findings have been
recorded by this version, not that the data has been verified healthy.

`--json` is available on every command. Status and inspection return zero on
successful retrieval even when findings are open. Verification and remediation
return zero only for a healthy result, and one for unresolved findings or an
operational error. SSH transport errors retain the SSH exit code.

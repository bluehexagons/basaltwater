# Review storage findings

Follow the [review and result checks](shares-data.md#review-approve-and-check-the-result)
for recovery actions and inspect the exact worker result before retrying.

## Review and resolve one finding

In **Scrubs & findings**, search file paths and categories under **Browse findings**.
**Task health** has a separate scrub-root search. **Browse this task’s findings**
selects its exact root and parity directory; **All scrub tasks** clears that
selection. Each list shows twenty rows per page. Filtering, clearing and pagination
preserve the other section's filters. Searches accept 200 Unicode characters,
match case-insensitively and treat punctuation literally.

Open **Inspect evidence and recovery options** on a finding. Browser actions are
followed by a controller command for reading its private evidence:

| Action | Effect / confirmation |
| --- | --- |
| **Verify file** | Check existing parity and update the finding; no typed confirmation |
| **Repair file** | Reconstruct and verify a staged candidate, retain originals; type `CHANGE FILE` |
| **Restore file** | Stage an independent copy from an absolute path on this host, verify against parity and retain originals; type `CHANGE FILE` |
| **Accept current content** | Replace the integrity baseline with content you independently confirmed is correct; type `ACCEPT CURRENT CONTENT` |

Choose **Review recovery**, check the file, scrub root, parity directory and copy
path and enter any required confirmation to start the worker. Check its result
under **Recovery activity**. The worker rejects changed findings
or changed file identities. A missing-file scan records absence as its reviewed
identity; a file appearing afterward blocks recovery. Verify older findings
without an identity before recovery. Verification that still detects damage
reports **findings**, not success.
Acceptance, repair and restoration remain file-specific.

## Triage selected findings

Choose **Task health → Browse this task’s findings**, then apply category and path
filters. Select up to ten visible findings and choose **Verify selected files**,
**Quarantine as corrupt (recommended)** or **Permanently delete selected files**.
Selection clears when changing pages or filters; it never includes hidden matches.
Long paths can require a smaller batch to fit the review limit.

**Review selected findings** lists every exact path and one action. Quarantine
requires `CHANGE FILE`; permanent deletion has its own review and requires
`DELETE N FILES`, with the displayed count in place of `N`. Submit the reviewed
batch to start the worker.
Acceptance of a new parity baseline is available for individual files only.

Quarantine atomically moves source files into a recognized root-owned, mode-0700
`.basaltwater-quarantine` directory on the source filesystem, preserving relative
paths under unique operation directories with `.corrupt` names. The quarantine
directory is placed inside a nested mount or Btrfs subvolume when necessary so
the move stays atomic. A filename too long for the `.corrupt` suffix is stored
as `source.corrupt` in its own operation directory, with its original name in the
disposition record. Read-only snapshots cannot be quarantined or deleted;
the worker reports that restriction without changing snapshot flags.
Both actions reject symlinks, multiple hard links and unsafe
or unrecognized quarantine directories. They also reject reserved scrub metadata,
files inside any configured parity database and files covered by overlapping
scrub tasks, including paused tasks. Verification remains available for overlapping
tasks; coordinate recovery across their reports through the administrator command
line. Permanent deletion removes source bytes; it retains the finding/disposition
history, parity and mirror protection.

An older worker could leave a root-owned, mode-2700 `.basaltwater-quarantine`
directory with no `.owner` marker on a Samba share. If an administrator confirms
it is that empty leftover, remove just the empty directory with `rmdir` before
reviewing the action again. Preserve marked or nonempty directories for inspection.

**Recovery activity** lists each file as pending, running, completed, skipped,
failed or unresolved, with a count for each state. Completed means the action
finished; a completed verification can still report damage. Missing item results
are shown as unresolved. Activity identifies the scrub root and parity directory
and links back to that exact task's findings. Cancelling a recovery review also
returns to its selected task.

The whole selection is checked before effects, including required nested mounts
and read-only mount flags. Each file is checked again before acting. If its
identity changes before any move is attempted, the worker skips it so it can be
verified and reviewed again. Interruption
can leave partial completion or an unresolved action. Inspect actual state and
individually review remaining files; batches are never replayed automatically.
Findings below still follow the five-minute export.

Disposed paths do not reopen as ordinary missing files on later scans. A new
file appearing there requires fresh verification against retained parity.
Quarantine content is excluded from scrubs and mirrors. Mirror copies remain
protected even after later healthy verification; this feature provides no mirror
protection release control. Keep the scrub declaration to retain these safeguards.

A recorded scan does not verify later file changes. Missing mounts, unreadable
reports and unsupported metadata show incomplete/unavailable results rather than
healthy or empty results. Invalid configuration or task-health data cannot hide
valid findings. Use `basaltw scrub status HOST` for controller inspection and
`--all` for resolved findings. See [scrub recovery](../storage/scrub-recovery.md)
for evidence, staged repair and recovery decisions.

## Inspect Btrfs filesystem health

**Overview** and **Scrubs & findings** show a separate Btrfs health section,
including hosts without PAR2 tasks. Match its filesystem UUIDs to mount points
with `findmnt -t btrfs -o TARGET,UUID` on the host. The five-minute exporter reads
kernel metadata and retained native scrub records; opening the panel runs no
Btrfs command or filesystem scan.

Each device shows retained write, read, flush, corruption and generation error
counters. Nonzero counters are highlighted and retain historical failures even
after recovery. Missing counters are unavailable, including on kernels without
the sysfs statistics interface; they are never assumed to be zero.

Native scrub records show recorded completion, cancellation or unfinished work,
device coverage, and corrected and uncorrectable error counts. Retained status
files can omit the **unverified** count even when live tools support it; the panel
shows **Unavailable**, never an assumed zero. Unverified errors, when recorded,
describe intermittent reads that succeeded on recheck and are distinguished from
uncorrectable errors. Expand **Native scrub error details** for recorded read,
checksum, header verification, superblock and memory allocation counts. Missing
detail fields remain unavailable; recorded allocation failures warn that results
may be incomplete. The parser follows the
[upstream retained status writer](https://github.com/kdave/btrfs-progs/blob/master/cmds/scrub.c),
which differs from the live ioctl/CLI output.

An unfinished record does not establish that work is currently running. Records
can cover selected devices only. Missing, malformed or unsupported records are
reported separately from PAR2 findings. The inventory supports sixteen mounted
filesystems and sixty-four devices per filesystem; exceeding a bound marks the
affected inventory unavailable.

Btrfs checks filesystem data and metadata checksums; PAR2 checks configured file
contents against retained parity. Native repair needs a good replica; single-copy
data has none. NODATASUM file data is not checksummed by native scrub. Snapshots
and reflinks share blocks and are not independent backups. Neither zero device
counters nor a completed native record prove current health.

The separate **Storage** usage diagnostic sums file allocation metadata. Reflinks
and snapshots can count shared extents more than once, so its directory estimates
do not establish unique physical usage or space reclaimable by deleting files.

Use the [native scrub guide](../storage/storage-operations.md#btrfs-filesystem-scrub)
to inspect or explicitly run native scrub through the administrator command line.
The panel does not start, schedule, cancel or resume native scrubs, reset device
counters, or perform balance, conversion or filesystem repair operations.

## Inspect other Linux filesystems

**Overview → Linux filesystems** and **Scrubs & findings** show local mount points,
filesystem types, sources, device numbers and read-only/read/write modes from
PID 1's mount table. This avoids the panel service's sandbox-only read-only bind
mounts. Read-only mode can be intentional; an unexpected change warrants checking
the kernel journal. Bind mounts can repeat one filesystem. The dashboard's
**Mounted storage** section already reports capacity and inode usage.

The five-minute exporter reads only bounded kernel metadata, without traversing
mount paths or probing devices. It supports ext2/3/4, XFS, F2FS, Btrfs, ZFS,
bcachefs, JFS, ReiserFS, FAT/exFAT, native NTFS, ISO9660 and UDF mount records.
Network, FUSE and virtual mounts are excluded. At most 64 mounts are shown,
prioritizing the root mount and read-only mounts; additional records are counted.
The collapsed mount list shows how many displayed mounts are read-only.
Malformed records and unrepresentable names are omitted and counted with an
explicit incomplete-inventory notice, preserving other valid mounts. They never
produce an empty-inventory claim. Unreadable or oversized mount metadata is
unavailable, independently of ext4 history, Btrfs and PAR2 reports.

For up to 64 **ext4 driver devices**, the panel reads the retained filesystem
error count and first/last error times from sysfs. Nonzero counts are highlighted.
Match kernel names such as `dm-0` to mount device numbers using
`lsblk -o NAME,MAJ:MIN,FSTYPE,MOUNTPOINTS`; device-mapper aliases may differ.
Unavailable counters and timestamps are labelled individually, including on
older kernels. Zero counts do not establish current health; history can remain
after a problem is resolved. The panel neither resets counters nor reads raw
superblocks. Exceeding the device bound makes ext4 inventory unavailable.

| Filesystem | Administrator inspection |
| --- | --- |
| ext2/ext3/ext4 | Plan `e2fsck` checks with the filesystem unmounted; use rescue mode for the root filesystem. Mounted results are unreliable. |
| XFS | Where supported, `xfs_scrub -n MOUNTPOINT` checks mounted filesystem metadata without repairs or optimizations. It does not compare file contents with known checksums. Run `xfs_repair` only with the filesystem unmounted. |
| Other local filesystems | Mount observations and dashboard usage are available; native integrity status is not collected. Use the filesystem's own administrator tools and documentation. |

These are inspection hints, not panel actions. The panel does not install tools,
start native checks, change mount settings or perform filesystem repairs.
Native metadata checks, retained error history and PAR2 content verification
cover different evidence. Consult the upstream [e2fsck manual](https://man7.org/linux/man-pages/man8/e2fsck.8.html)
and [XFS scrub manual](https://man7.org/linux/man-pages/man8/xfs_scrub.8.html)
when planning maintenance.

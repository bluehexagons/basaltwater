# Storage operations

`--sync`, `--backup`, and `--scrub` configure recurring storage work. They are saved with
the machine configuration and run together through the root-owned
`storage-ops.service` and its hourly `storage-ops.timer`.

## Choose a topic

<a id="configure-a-mirror-and-parity-protection"></a>
<a id="sync-behavior"></a>
<a id="parity-and-repair-behavior"></a>
<a id="btrfs-filesystem-scrub"></a>

- [Storage sync, parity, and repair](storage-sync-and-parity.md) Configure a mirror and understand sync, PAR2, and Btrfs scrub behavior.

<a id="inspect-and-run-operations"></a>
<a id="change-or-remove-storage-work"></a>
<a id="troubleshooting"></a>

- [Run and maintain storage operations](storage-operation-lifecycle.md) Inspect, run, change, remove, and troubleshoot storage work.

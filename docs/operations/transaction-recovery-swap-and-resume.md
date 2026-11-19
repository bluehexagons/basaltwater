# Recover compressed swap and resume configuration

[Topic index](transaction-recovery.md)

## Compressed swap recovery

Swap files require root-owned directories without group/other write access
throughout their ancestor chain. A root-owned leaf below a user-controlled
directory is insufficient: its ancestor can be replaced during setup.

Zram setup checks the generated services and confirms that owned devices are
inactive and reset before changing its generator configuration. A failed stop
retains the previous configuration and ownership; earlier successful stops can
already have reduced available swap. Inspect the named service and
`swapon --show` before repairing the cause and rerunning setup.
A failed setup service can retain an allocated device even after swap is
inactive: a successful `systemctl stop` alone does not prove its cleanup ran.
Check the device size, mounts, holders and pending jobs as well. Reset an owned
device manually only after confirming it is unused; setup refuses to overwrite
its policy while the allocation remains.
Removed devices retain zero-size entries in the owned generator dropin so the
package's vendor defaults cannot reactivate them. Setup retains these entries
when adding other devices. First installation publishes the requested policy
before package hooks can activate defaults; activation starts generated swap
units, which pull in their required setup services.

If activation fails after publication, recovery first quiesces the candidate,
then restores the previous generator file, reloads systemd and reactivates
previously active owned devices. Restoration and live priorities are checked.
If recovery cannot quiesce a candidate, it retains the candidate configuration
for inspection instead of overwriting it beneath a live device. A later recovery
failure can leave the previous configuration restored but inactive. Preserve
`/opt/basaltwater/state/swap.json`, inspect the generator file and units, repair
the reported cause and rerun the matching setup. Ownership records are not a
durable phase journal or a backup of the previous generator file across process
loss; the outer setup guard still controls interrupted-operation recovery.

Zswap removal checks stop/disable and loaded state even if its unit file was
unlinked. It stops the loader before disabling the kernel cache, verifies that
cache is disabled, then removes the managed definition. A failed step retains
the definition and ownership for repair; earlier changes are not rolled back.
Failed swap inventories and unreadable zram device state never establish absence.
Zswap activation also verifies the requested enabled state and optional pool
limit after starting its loader. A successful service command with mismatched
or unreadable kernel state fails setup; the definition remains available for
inspection and repair. Earlier kernel writes are not automatically rolled back.

## Resume-device initramfs recovery

Swap setup persists `/opt/basaltwater/state/swap.json.resume-pending` before
adding, replacing or removing its managed resume-device file. It removes this
intent only after `update-initramfs -u` succeeds. A matching rerun retries the
rebuild even if the resume file already matches or is already absent. Setup's
outer operation guard still governs whether that rerun is permitted.

Keep this private intent file after a failure or interruption. Inspect the
resume policy and initramfs error, repair the cause, then rerun the matching
setup through [phase recovery](transaction-recovery-phases.md#recover-by-phase).
Do not delete the intent merely
to make a rerun skip boot-image reconciliation. Unchanged setups with no
pending intent do not rebuild initramfs. This rebuild recovery does not qualify
hibernation, restore a prior boot image or repair unrelated initramfs hooks.

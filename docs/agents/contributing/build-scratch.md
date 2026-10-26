# Debian build scratch backend

`lib.build_scratch` is an internal prerequisite for the on-demand build host.
It does not enable a build command, service, or production caller. It provides
one root-worker-only API:

```python
handle = create_scratch(scratch_root, request_id, size_bytes=2 * 1024**3)
# The coordinator provisions and supervises the unprivileged worker here.
# After all descendants stopped and artifacts were snapshotted:
handle.release(work_stopped=True)
```

The configured scratch root must already exist, be canonical, root-owned and
mode `0700`. Every path component is opened without following symlinks, and
every ancestor must be protected from renames by unprivileged users. An
ancestor writable by group or other users is rejected unless it is sticky and
both its owner and the next child are trusted; this permits a root-owned sticky
directory such as `/tmp` when its child is root-owned. Tests may model the
worker with the current effective UID. The image and marker are root-owned
mode `0600` files.

After creating the request directory, the worker writes and syncs a
`preparing` identity marker before creating the mountpoint or image. Once those
staging operations are complete, it atomically replaces that marker with the
final `prepared` identity and syncs the directory before running any native
command. An existing request directory blocks replay, including requests with
only a preparation marker or partially staged files. There is no automatic
cleanup or replay of those states. Request-directory creation or the initial
marker write and sync can fail before a complete durable marker exists; a
directory already created still blocks replay, but cannot always contain a
complete identity record.

The sparse image is limited to a MiB-aligned size from 256 MiB through 128
GiB and is formatted only with fixed `mkfs.ext4` arguments. The final marker
records request, root, directory, image and mountpoint identities before
formatting or mounting.

Mounting uses the fixed `mount -t ext4 -o loop,nodev,nosuid` boundary. The
kernel's automatic loop-device autoclear handles the device after unmount;
this module does not scan or detach loop devices. Before returning a handle,
the adapter checks the exact `findmnt` target, ext4 type, source image (through
the loop backing-file identity where applicable), mount ID, required mount
options and observed filesystem capacity. Capacity must be positive and no
larger than the image size. A `findmnt` error or any identity mismatch fails
closed.
An exact mount lookup with status `1` and no output or diagnostics means absence;
empty output on a successful lookup remains unconfirmed.

The coordinator owns the full lifecycle. It must supervise and stop every
worker descendant, finish the artifact snapshot, and only then call
`release(work_stopped=True)`. The caller must put the workspace, home directory
and build caches on this mounted filesystem so they share its hard size cap.
Broker logs and retained artifacts need separate bounded storage. The caller
may grant the build identity only the traversal and filesystem access it needs
after provisioning; the image and marker remain private to the root worker.
Because the image is sparse, the size cap does not reserve host disk blocks;
worker admission must account for backing storage across concurrent jobs.

Release verifies the saved image and exact mount identity before one normal
unmount, confirms the mount is absent, then deletes the image and marker. A
busy or unconfirmed unmount, failed observation, changed image, unexpected
entry, timeout, or interruption retains the request directory and evidence.
There is no automatic cleanup, retry, or loop-device detach. Keep capacity
blocked until an operator reconciles retained state. Do not release while any
build process may still be running.

Tests mock all native commands and capacity observations and use temporary
directories. This code has not been qualified against a real Debian mount,
loop device, power loss, or concurrent privileged host mutation. File and
directory syncs preserve useful evidence during ordinary failures, but there
is no power-loss atomicity guarantee. The backend is not an untrusted
pull-request isolation boundary and remains disabled until the parent build
worker wires the lifecycle, storage policy and host qualification.

# Build request and artifact contracts

The shared build-host implementation starts with two internal interfaces:
`lib.build_protocol` validates version-1 documents and `lib.build_artifacts`
packages and verifies selected outputs. The [target admission store](build-admission.md)
records deployment intent, and `DeploymentOrchestrator.deploy_prebuilt()` uses the
existing activation transaction for verified bundles. The [durable job store](build-jobs.md)
and [Debian worker](build-worker.md) compose source verification, bounded scratch,
supervised execution and packaging. [Runtime validation](build-runtime.md) checks
the initial static and self-contained service scope before target activation.
The internal [process entrypoint](build-host-entrypoint.md) connects a typed
SSH dispatcher, project policy, bounded source admission and an independent
worker. The public [controller setup flow](build-setup.md) connects explicit
`--build-on` selection, managed enrollment, pinned sources, artifact delivery and
guarded final profile application within its bounded static/Go scope. Ordinary
deployments without a selected builder retain build-on-target behavior. Live
public-flow qualification remains separate from these contracts and dry runs.
The [shared build-host
plan](../../plans/cicd-manifest-reuse.md) describes the remaining work.

## Build requests

A request contains exactly `schema`, `request_id`, `project`, `source`,
`platform`, `components` and `limits`. IDs are 32 lowercase hexadecimal digits;
source records a full Git revision plus SHA-256 hashes of the source snapshot
and manifest. Platform records OS, architecture and baseline. Parsing validates
their shape; a worker must separately enforce its supported platform and tools.

Each component carries a name, type, explicit relative export paths, literal
build commands and build environment inputs. The controller resolves manifest
defaults with `lib.project_manifest.artifact_paths()`. Target paths, ports,
runtime environment values, secret bindings and deployment credentials are
excluded from this request. Build inputs are visible to build code; never insert
runtime secrets. Commands are data until an authorized worker executes them
under its unprivileged identity.
`lib.build_manifest` selects and revalidates those inputs against the pinned
manifest. Command-bearing remote builds require explicit `build_runtimes` so
the controller can check installed tools before admission. Ready-to-serve
outputs without commands need no declared compiler. This requirement applies
to remote builds; ordinary build-on-target inference is unchanged.

Limits cover elapsed build time, memory, CPU percentage (100 is one CPU), scratch
filesystem capacity, export bytes and entry count, and log bytes. Scratch capacity
defaults to 8 GiB and must be a whole number of MiB between 256 MiB and 128 GiB.
The parser imposes upper bounds; admitting
policy can lower them. Parsing alone does not reserve or enforce resources.
The request digest covers the normalized complete request, including attempt
ID, source, commands, environment, exports, platform and limits.

## Retained descriptor and bundle

The descriptor retains request/project/source/platform identity, the request
digest, component export lists, observed tool versions and a sorted inventory.
It does not retain build commands or environment values. Each inventory entry
has a relative path, regular-file/directory kind, byte count, SHA-256 for files
and an executable flag. Every parent directory is inventoried. Directory
metadata has zero bytes, an empty checksum and no executable flag. Consumers
normalize directories to `0755`, files to `0644` or `0755`, and the private
extraction root to `0700`; ownership, set-ID bits and extended metadata are not
transported.

The uncompressed bundle is `BASALTWATER-ARTIFACT\0\x01\n`, followed by an
eight-byte unsigned big-endian descriptor length, the UTF-8 JSON descriptor,
then the exact bytes of each regular file in inventory order. There are no
per-file archive headers, links or trailing bytes. JSON is deterministic and
rejects duplicate keys and non-finite numbers. The descriptor is at most 64 MiB;
inventory and payload must satisfy both protocol and admitted request limits.

`create_artifact()` copies exports through no-follow file/directory descriptors
into a private snapshot before calculating inventory or writing the bundle.
The caller must first stop all build work and use a broker-owned parent that
the build identity cannot access. Symbolic links, hard-linked files and special
files are rejected. Whole-root static exports omit Git and other control
metadata. Packaging preserves repository-relative layout and does not silently
remove explicitly exported dependency directories.

`unpack_artifact()` checks the descriptor against the admitted request, verifies
every file checksum and the expected whole-bundle digest, and only then
publishes a new private tree. Both APIs require a private parent owned by their
effective UID, reject symlink path components, and refuse existing destination
entries. A post-publication filesystem-sync error may leave a complete output;
the operation owner must reconcile it rather than blindly replacing it.
The initial filesystem adapter requires Linux no-replace rename support;
Windows publication needs its own qualified adapter.

These checks establish byte identity, not authorization or provenance. The
transport and durable job/admission layers must bind authenticated ownership,
source identity and target generation before execution or activation. They must
also manage artifact retention, cancellation, transfer pins and replay recovery.

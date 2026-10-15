# T3 orchestration V2 migration status

The V2 client/server protocol and thread database migration are not part of
Basaltwater's current stable deployment guidance. This reference is a status
boundary, not an upgrade procedure; do not install a preview or migrate a
managed service based on earlier nightly behavior.

`basaltw agent doctor --capability t3code` checks the managed server runtime
and service. It does not establish client/server protocol compatibility or
confirm that every conversation migrated correctly. Never resolve a version
mismatch by deleting T3 data or resetting pairing.

When upstream publishes a stable migration contract, follow that exact
client/server version's documentation and qualify the migration on disposable
data before a production update. Preserve the complete private T3 data
directory, including database sidecars and attachments, according to the
release-specific backup procedure. Keep recovery copies private and do not run
a T3 server against them.

See the [upstream T3 Code releases](https://github.com/pingdotgg/t3code/releases)
and use the release-specific migration and client compatibility guidance when
it is published.

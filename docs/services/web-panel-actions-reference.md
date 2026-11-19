# Git, credentials, and administration

[Documentation index](web-panel-reference.md)

## Git and credentials

`GET /credentials` reads only public author fields from the account's standard
global Git files and the presence of a stored `github.com` token. It does not
contact providers, read repository-local settings, follow Git includes, run
credential helpers, or return credential contents. A `saved` query accepts only
the finite `identity`, `github`, or `removed` result labels; all other fields and
duplicates are rejected.

The fixed CSRF-protected POST routes are `/actions/credentials/identity`
(`name`, `email`), `/actions/credentials/github` (`token`), and
`/actions/credentials/github-remove` (`confirmation=remove`). They use the normal
16 KiB body limit, reject unknown/duplicate fields, validate author fields and
single-line tokens, and cap tokens at 8,192 characters. Token replacement and
removal require both an HTTPS manifest URL and the HTTPS scheme supplied by
the local Nginx proxy. Responses and redirects contain no submitted token,
vendor output, or arbitrary exception details. No credential history is stored.

Editing requires the current UID to match the configured non-root account and
its owned home. Paths must have no symlink components, must be regular bounded
files (1 MiB maximum), and must not be writable by other users. Existing GitHub
credential files must also be private. Writes use private temporary files and
atomic replacement with mode `0600`; locks and concurrent-content checks avoid
overwriting another update. The panel modifies fixed identity/helper keys in a
staged Git config and preserves unrelated settings. GitHub replacement requires
Git and GitHub CLI; it directly stores the token without a classic-scope login
probe. Only the selected host entry is replaced or removed. Token removal does
not revoke provider credentials or clear a keyring or environment.

These settings belong to this VM account, not the controller's credential store.
Ordinary controller setup preserves seeded target values; explicit saved author
overrides are reapplied on reruns. The page provides the existing device-login
command for agent subscription sessions rather than copying renewable sessions.

## Administration controls

`GET /admin` reads bounded broker status and recent requests. Its optional
`action` query selects a fixed review form; it performs no host action.
`POST /actions/admin` requires CSRF, one finite action name, a matching single-use
five-minute review ticket, and host-name confirmation for reboot/power-off.
Unknown and duplicate fields are rejected. `POST /actions/admin/cancel` accepts
only CSRF and a request ID and delegates owner checks to the broker. Lost
responses do not cause automatic retries; inspect request history first.

The panel requests `admin.run` with a finite `action` parameter through the
kernel-authenticated requester socket. It cannot choose argv, environment,
service names, paths, or approval decisions. Every host action requires the
independent approval identity, including service restarts that could otherwise
have an administrator `allow` rule. The existing reboot `deny` policy also
denies the screen's reboot and shutdown actions. Cancellation of a scheduled
power action remains separately approved.

The broker's read-only `admin-status` operation returns up to 20 recent owned
administration requests from its latest 100 owned requests, fixed availability
reasons, and the latest job metadata. It returns no root command output or setup
configuration. Unavailable service state blocks new maintenance; a retained
running result with an inactive unit becomes interrupted, never successful.

Long actions use `systemd-run` to start the fixed
`basaltwater-admin-maintenance.service` outside the panel/broker cgroups.
`Type=exec` verifies process launch, and a six-hour runtime limit plus
`KillMode=control-group` bounds the job and its children. The shared unit name
and a lifetime file lock prevent overlapping maintenance. The private latest
result is atomically stored at
`/var/lib/basaltwater-privilege-broker/admin-job.json` before effects and after
completion. It is not a per-request completion log; inspect each request's
dispatch state separately. No job or ambiguous dispatch is replayed.

Scheduled power cancellation uses the fixed `shutdown -c` command directly,
so it can be approved even while a maintenance job is active. Root refresh
validates ownership and permissions throughout the installed source and Git
metadata before running the CLI; it never executes a selected user checkout.
The source's managed state directory is excluded from this code check because
the refresh command validates its saved setup separately. The approved check
reports validation success or failure without exposing privileged plan output.
Proxmox panels omit the administration page and its navigation entry because
the optional broker is unsupported on that profile. Other profiles retain the
page's setup instructions when the broker is unavailable; administration actions
require a configured broker.

## Agent tool preparation

`GET /agent-tools` displays a fixed catalog or setup form without collecting
logs or running work. Query fields are restricted to one `tool` and, for log
and job reviews, fixed `service`, `window`, and `priority` values. Job review
accepts only managed job services. Duplicate, unknown, and incompatible query
fields are rejected; the query is capped at 512 characters and four fields.
Contextual links never embed log entries, sender tokens, arbitrary commands,
or message-text search values.

`POST /actions/agent-tool/prepare` uses the normal panel CSRF and 16 KiB body
limit. It accepts one tool and only that tool's finite input fields. Preparation
validates paths with the shared filesystem validator and runner directory
validator, then renders the ordinary Agents task form. It does not create
state, call a collector, run Codex, or create output folders. Validation errors
preserve escaped form values for correction. Saving, scheduling, and running
still use `/actions/agent-task/save` with the existing validation and permissions.

Data paths are capped at 512 bytes, canonicalized, and required to stay strictly
below a dedicated account-home workspace. Source paths must exist and be regular
files or directories; output parents may be new or existing directories.
Hidden/credential paths, paths through symlinks, and nested or overlapping
source/output paths are rejected. Imports accept staged CSV/JSON/JSONL files and
one of those output formats. Cleanup requires a directory and a whole-number
minimum age of 1–3650 days. All generated prompts fit the runner's 4,000-byte
limit; paths travel as escaped JSON data, never executable command arguments.

Preparation does not lock source files or guarantee their later identity.
The prompt requires run-time path, ownership, and boundary revalidation.
Checksum/manifest, fresh-output, file-count, data-size, privacy, and quarantine
rules are agent instructions rather than an extra OS sandbox or deterministic
transfer implementation. The existing workspace sandbox and runtime remain the
execution boundary; independently configured integrations keep their permissions.
Only metadata and paths are handled by the workbench; the scheduled agent reads
source contents, which may enter its configured provider context. No download
or arbitrary-file serving endpoint is added; artifacts remain local to the
workspace and are reported in run output.

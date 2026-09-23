# HomeBox service support

Status: native setup, recovery, recurring updates, and operator documentation
implemented on 2026-09-07. Full disposable Debian VM qualification remains a
release validation task. This record tracks [issue #99](https://github.com/bluehexagons/basaltwater/issues/99)
under the [lightweight service project](LIGHTWEIGHT_SERVICE_CANDIDATES.md).

The live operator contract is [HomeBox inventory](../HOMEBOX.md). This plan
records the delivered boundary, validation evidence, and the work still needed
before calling the integration release-qualified.

## Delivered contract

- One native HomeBox instance per supported Debian server profile, using the
  verified amd64 `v0.26.2` release by default. Other architectures, OCI, and
  custom step pipelines are rejected before HomeBox target mutations.
- A dedicated `homebox` account, hardened systemd unit, loopback backend, and
  either SSH-tunnel access or managed Nginx/Let's Encrypt HTTPS. Cloudflare,
  containers, PostgreSQL, object storage, multiple instances, subpaths, OIDC,
  and unmanaged-install adoption remain outside the contract.
- Validated local storage and mount identity, SQLite with local attachments,
  persistent secrets, private first-user bootstrap, closed registration, and
  non-secret health output. Reruns preserve users, data, passwords, and the
  API-key pepper.
- Explicit stable upgrades and the weekly `auto-update-homebox.timer` use one
  stopped-service transaction: maintenance gate, complete recovery archive,
  verified release, readiness checks, and rollback before reopening ingress.
  Four newest automatic `*-before-setup.tar.gz` archives are retained;
  manually named backups are untouched.
- Absolute-path backup and restore commands include the binary, state,
  secrets, SQLite sidecars, attachments, and recovery safeguards. Disabling
  HomeBox removes its service and ingress while retaining inventory and
  recovery material.

## Implementation map

| Area | Owning code | Delivered behavior |
| --- | --- | --- |
| Configuration and intent | `lib/homebox_config.py`, `lib/config.py`, `lib/arg_parser.py`, `plugins/server.py` | Validated setup/patch options, round trips, conflicts, dry runs, and service composition |
| Target lifecycle | `web/homebox_steps.py` | Release verification, account/storage setup, bootstrap, systemd/Nginx, health, locking, upgrade, backup, restore, disable, and rollback |
| Controller operations | `lib/homebox_cli.py` | Remote health, backup, and restore commands with saved SSH settings and confirmation for destructive restore |
| Recurring maintenance | `common/service_tools/auto_update_homebox.py`, `common/common_steps.py` | Stable release check, shared upgrade transaction, result state, and configured notifications |
| Documentation and tests | `docs/HOMEBOX.md`, `docs/COMMAND_LINE.md`, `docs/MAINTENANCE.md`, `tests/test_homebox.py`, `tests/service_tools/test_auto_update_homebox.py` | Operator procedures, maintenance policy, focused lifecycle coverage, and non-secret health reporting |

## Validation evidence

Focused tests cover malformed specs, paths, versions, ports, conflicts,
unsupported profiles and architectures, config/patch round trips, dry runs,
release integrity, archive traversal, bootstrap policy, registration closure,
idempotency, complete restore, damaged-state repair, updater state, timer
health, and failures before and after ingress reopening. System calls are
mocked and fixture data uses temporary directories.

The amd64 `v0.26.2` binary was exercised in temporary storage for startup,
closed registration, group invitations, login, item creation, attachment
upload, API-key access, password-reset URL generation, stopped backup, and
full restore. Login, API keys, and attachment hashes survived restore.
Generated Nginx configurations passed `nginx -t`, and the systemd unit passed
`systemd-analyze verify` with fixture inputs. These checks establish the
native lifecycle contract; they do not replace a real Debian host test.

The release API and upstream documentation used for the initial baseline are
[the v0.26.2 assets](https://github.com/sysadminsmedia/homebox/releases/tag/v0.26.2),
[native installation](https://github.com/sysadminsmedia/homebox/blob/v0.26.2/docs/src/content/docs/en/quick-start/install.mdx),
[configuration](https://github.com/sysadminsmedia/homebox/blob/v0.26.2/docs/src/content/docs/en/quick-start/configure/index.mdx),
[configuration source](https://github.com/sysadminsmedia/homebox/blob/v0.26.2/backend/internal/sys/config/conf.go),
[database startup](https://github.com/sysadminsmedia/homebox/blob/v0.26.2/backend/app/api/setup.go),
and [proxy guidance](https://github.com/sysadminsmedia/homebox/blob/v0.26.2/docs/src/content/docs/en/quick-start/configure/proxy.mdx).
Each later release is checked for the exact stable tag, amd64 asset, and
publisher-supplied SHA-256 digest before activation.

## Remaining release qualification

Run the integration on a disposable amd64 Debian VM and exercise:

- reboot and service restart with the declared data mount;
- public certificate issuance/renewal and external firewall reachability;
- browser login, item and attachment flows, and WebSocket events;
- repeat setup, upgrade migration/rollback, and restore to a clean instance.

No disposable target is currently configured for this repository, so these
checks remain explicitly open. The larger monitoring project can consume the
existing HomeBox health facts after this qualification.

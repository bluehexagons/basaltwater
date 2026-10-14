# Keep an existing development site current

When editing a project whose local development site is already set up and
authorized, refresh that site as part of the work. Do the required build,
publication, or restart yourself instead of asking the user to run it. A
read-only review does not authorize a refresh, and this does not authorize
production deployment or exposing another project.

Inspect `preview list --json` and `forward list --json` to identify the site's
owner, project, and route. Preserve its URL, ports, upload limit, environment,
database, and other runtime data. Follow the project's development instructions:

- With working hot reload, verify the changed page or resource through the
  existing HTTPS route; source edits alone do not prove it refreshed.
- For a managed preview that needs rebuilding or reloading, run
  `basaltwater-web preview restart NAME --wait 60 --json`. This reruns its
  saved launch command and checks HTTP readiness without replacing the route.
  If the command executes a compiled binary, build it first.
- For a forward whose process belongs to an existing user service, inspect
  `systemctl --user show UNIT -p WorkingDirectory -p ExecStart -p Environment`
  locally to confirm it runs this project, then restart that service yourself.
  For example, GoClick's `go run ./cmd/server` service needs
  `systemctl --user restart goclick-dev` after Go or embedded frontend edits.
  Keep environment output private. Do not restart the gateway, T3 Code, or
  another project's service to refresh this application.
- For a copied static publication, rebuild and publish the same site after
  the final source edits, then use `site verify` as described in [static publications](static-sites.md).

After a restart, check service activity, run `basaltwater-web doctor NAME`, and
verify a changed public resource or behavior through HTTPS. Health alone does
not prove freshness. Inspect the scoped service logs on failure and repair the
cause within the development task. Report any unresolved failure instead of
claiming the updated site is ready. Leave a working development site running.

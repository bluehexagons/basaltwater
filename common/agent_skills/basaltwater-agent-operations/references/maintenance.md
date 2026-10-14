# Maintenance holds

Create a hold only when work must cross the normal restart window:

```bash
basaltw agent maintenance status --json
basaltw agent maintenance hold --hours 8
basaltw agent maintenance release
```

The hold is shared by this account's tasks, not owned by an individual thread.
Inspect an existing hold before replacing it; do not shorten its deadline or
release it while another task still depends on it. Release a hold you created
when the protected work ends. Holds expire after at most 72 hours and do not
override the host's forced-restart deadline. A task's runtime cap does not create
or renew a hold automatically.

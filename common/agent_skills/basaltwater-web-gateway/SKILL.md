---
name: basaltwater-web-gateway
description: Publish or refresh local development sites through the managed HTTPS gateway.
metadata:
  managed-by: basaltwater
---

# Managed HTTPS gateway

Use only when `command -v basaltwater-web` succeeds. Keep upstreams on loopback
and TLS verification enabled; do not replace missing provisioning with ad hoc
Nginx/firewall changes. Run as the configured owner without sudo.

| Task | Read |
| --- | --- |
| Publish or verify a copied site snapshot | [Static sites](references/static-sites.md) |
| Start or restart a supervised live project | [Managed previews](references/previews.md) |
| Provide a separate HTTPS URL for an existing service | [Live forwards](references/forwards.md) |
| Keep an already configured development site current after edits | [Refresh](references/refresh.md) |
| Build or publish a Godot export with the `godot` site profile | Installed Godot web skill |

When editing a project with an already authorized local site, handle its needed
build, publication, or restart yourself; do not leave the user a manual restart.
Preserve URL, ports, body limit, environment, database, and runtime data.
Verify a changed resource or behavior: health alone does not prove freshness.
Leave a working development site running.

Publication and new forwarding require that scope. A read-only review, local
build, or browser check does not authorize exposure or refresh; maintaining a
local site does not authorize production deployment. Stop/remove only when
requested. Use the installed browser skill and session policy for rendered checks.

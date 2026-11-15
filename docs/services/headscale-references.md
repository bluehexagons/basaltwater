# Headscale server references

The current `basaltw headscale` commands save endpoint references in the active
private workspace. They support discovery only: a reference does not grant
permission to administer the server, and removing one only removes the local
reference. These commands do not install or configure Headscale or enroll a
client.

```bash
basaltw headscale add office --url https://vpn.example.com
basaltw headscale add team --url https://headscale.example.net \
  --origin managed --onboarding-url https://join.example.net
basaltw headscale list
basaltw headscale show office
basaltw headscale onboarding team
basaltw headscale rename office home
basaltw headscale remove home
```

Names are unique without regard to case. `show`, `rename`, `remove`, and
`onboarding` accept an exact saved name or stable record ID. URLs must use
HTTPS. The optional onboarding URL is printed only when one was saved;
`onboarding` reports an error when it is absent. No QR code or remote request
is involved.

Use `--workspace PATH` before `headscale` or after a Headscale action to select
the workspace. `--dry-run` on `add`, `rename`, and `remove` previews the
operation without creating or changing registry state. Registry reads and
completion do not create files. Invalid or corrupt saved state is reported by
commands and is never silently reset.

See the [setup configuration contract](headscale-configuration.md) and
[Headscale implementation plan](../plans/headscale-implementation.md)
for the later hosting, enrollment, administration, and recovery milestones.

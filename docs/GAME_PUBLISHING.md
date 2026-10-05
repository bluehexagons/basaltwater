# Publishing games from a VM

Implementation is being delivered on the game publishing branch. Accounts and
durable operational state remain local to the panel user's VM. Neither the
manifest nor build records contain credentials. The shared VM account can access
its native sessions; human review is a managed workflow boundary, not isolation
from unrestricted code running as that account.

The project manifest accepts communication languages:

```json
{
  "version": 1,
  "components": [],
  "publishing": {"languages": {"source": "en", "supported": ["en", "es"]}}
}
```

Absent language metadata defaults to English only. Other valid tags are accepted;
English and Spanish have structural tests initially. The declaration does not
claim that the game itself is localized. Older Basaltwater installations must be
upgraded before reading this additive version-1 extension. Create a minimal
game manifest with `basaltw manifest init --kind publishing`.

Each build completion record pins a directory, internal build identifier and
content digest. Snapshots reject links, special files, hard links and credential
paths. Native upload commands use fixed arguments and generated Steam VDFs
without `SetLive`; Steam default release/rollback always happen on Steamworks.
An interrupted dispatch is held for reconciliation rather than repeated.

Writing and supplied translations start as drafts. Each final destination and
language revision requires a human review in the authenticated panel. Editing
the source invalidates dependent translations. Scheduled posts use explicit UTC
instants and a reviewed lateness window. Steam announcements and itch.io devlogs
initially export reviewed text for a human editor handoff; they do not advertise
automatic remote publication. A recorded human receipt is labelled
operator-confirmed, not provider-verified.

See the [publishing plan](plans/GAME_PUBLISHING.md) and
[release communications plan](plans/GAME_RELEASE_COMMUNICATIONS.md) for follow-on
provider qualification, rich assets, Steam locale CSV imports, website/blog,
social adapters, and reviewed campaigns.

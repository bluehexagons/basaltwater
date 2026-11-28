# Coordinate a desktop team

Use a single assignment to brief seat players, visual reviewers, planners or
evidence specialists. Workers can analyze private saved observations in parallel.
For a shared desktop, the current lease owner alone operates the helper and
gamepad. Independent desktops can have independent owners after each route is
qualified in its own environment.

## Prepare and dispatch

Create a JSON specification in a private directory outside Git. Give each
worker an objective they can finish and concrete evidence to return. The
`coordinator` entry is required and can cover any assigned resource.

```json
{
  "assignment": "local-match",
  "objective": "Complete one local team match",
  "resources": {"couch": "cachyos-native"},
  "scopes": ["shared", "host", "guest"],
  "workers": {
    "coordinator": {
      "role": "coordinator",
      "task": "Qualify the desktop and reconcile the final outcome",
      "acceptance": "Both summary tabs and process closeout verified"
    },
    "host": {
      "role": "keyboard player",
      "task": "Plan and operate the host board",
      "acceptance": "Host Ready and terminal result evidenced",
      "resource": "couch",
      "watches": ["shared", "host"],
      "instructions": ["/absolute/project/host-controls.md"],
      "workspace": "/absolute/project"
    },
    "guest": {
      "role": "gamepad player",
      "task": "Plan and operate the guest board",
      "acceptance": "Guest Ready and terminal result evidenced",
      "resource": "couch",
      "watches": ["shared", "guest"]
    },
    "reviewer": {
      "role": "evidence specialist",
      "task": "Review the guest pane during combat",
      "acceptance": "Return the observed outcome and supporting observation ID",
      "watches": ["shared", "guest"]
    }
  }
}
```

```sh
basaltw desktop team prepare --spec /private/assignment.json --output /private/match-team --json
```

Give each worker its own `brief.json` and verify access from its actual
environment. The brief includes its evidence directory, checkpoint path, scope
dependencies, and digest-pinned `desktop instructions` reads. Use the
[controller preparation guide](playtest-preparation.md) to track reading and
qualify observation. Workspace paths describe the intended location. Your
orchestrator provisions workspaces and owns model settings, worker lifecycle,
deadlines and wakeups.

`desktop_groups` lists workers who will share a desktop, and `offline_workers`
lists workers available for artifact review. Deploy independent desktop groups
on separately qualified resources. Resource names alone do not create a display,
account or device. Each local backend has one lease per login runtime, and the
Linux pad bridge is shared between local backends. A remote worker needs access
to a private copy of its brief and evidence or an explicitly shared private
store. Leases are local to each backend and host.

## Publish observations and collect decisions

The controller captures once, preserves the capture receipt with its run evidence,
and selects small, reviewed PNG crops for recipients. Use
[`evidence crop`](native-qa-evidence.md) to
prepare literal views. The team record copies supplied PNGs and records their
hashes. A view file can carry screen content outside the named scope, so check
the pixels before publishing it. Supply up to four absolute PNG paths per
recipient. Include the optional `capture` receipt to
record the source frame's ID, time, geometry and digest.

```json
{
  "capture": "/private/run/capture.json",
  "updates": {
    "shared": "Round 11 shop",
    "host": "Host shop open",
    "guest": "Guest Ready"
  },
  "views": {
    "guest": ["/private/crops/guest.png"],
    "reviewer": ["/private/crops/guest.png"]
  }
}
```

```sh
basaltw desktop team observe /private/match-team coordinator --after initial --spec /private/round-11.json --json
basaltw desktop team view /private/match-team guest --json
basaltw desktop team decide /private/match-team guest --observation 000001 --spec /private/guest-plan.json --json
basaltw desktop team check /private/match-team 000001 --json
```

A decision spec has a `proposal` string and an optional `assumptions` array of
strings. Later observations use the latest observation ID in `--after` and list
every scope the new observation changes. Update `shared` for a scene, round, or
other change relevant to everyone. Update both seat scopes when a transfer
changes both boards. A host-only purchase can leave the guest decision current.
A card that reopens the guest shop invalidates its old Ready decision. Check
changed scope names in `stale_scopes` and replan when needed.

`view` displays the worker's watched summaries and supplied images, each with
its own observation ID. Specify `--observation` to revisit a prior packet.
Revision checks cover published updates. The acting worker still verifies the
actual current screen and uses a fresh helper capture for input. Observation
records and recipient filtering are for cooperating workers in one account.

## Claim, hand off and recover input

Use this route after a live helper status advertises `team-lease-v1`. If using
a supervised gamepad, verify its running status advertises the same capability.
Start both through their normal qualified routes. On CachyOS add `--native`
before `team`; on Debian omit it. Preserve the human's existing permission
choice, portal/session generation and game settings.

```sh
basaltw desktop --native team lease claim /private/match-team couch --worker host --json
basaltw desktop --native team lease status --json
```

The response gives a private `lease_file` for the first owner and a
`recovery_file` for the coordinator. Pass only the current owner's lease path
to that worker. Set `BASALTW_DESKTOP_LEASE_FILE` in the environment of its
desktop commands and wrappers. The helper and pad validate it before admitting
an operation and share a lock through input dispatch, release and receipt
writing. Capture in the current epoch; the first keyboard, pointer or pad
input consumes the helper's observation for both routes. Inspect accessible
elements again in the current epoch before using their references. Keep the
team directory intact while the lease is active.

At a safe point, confirm released inputs and the visible effect, then publish a
result under the current worker. The lease requires a result written after the
last input:

```json
{
  "state": "ready",
  "delivery": "completed",
  "summary": "Host Ready; guest still shows End Turn; inputs released",
  "next": "Guest inspects its shop and takes control",
  "observation": "000001",
  "evidence": ["/private/run/host-input.json"]
}
```

`evidence` accepts up to eight absolute `.png`, `.json`, `.md` or `.txt`
paths per result. Include the decisive action and after-frame receipts. If
more files are relevant, include a private evidence index as one attachment
and retain the underlying files in the run directory. Count automatically
included files before publishing the observation and result. If result
validation fails after an observation was published, check the observation
head and lease before correcting the result. The published observation remains
available for the corrected result.

```sh
basaltw desktop team result /private/match-team host --spec /private/host-result.json --json
basaltw desktop --native team lease handoff --epoch 1 --credential "$BASALTW_DESKTOP_LEASE_FILE" \
  --worker guest --result 000001 --note "Host Ready and controls released" --json
```

Send the next owner the new lease path and result ID. They verify the lease
epoch, inspect the desktop and act on a new capture. Use the run ledger for
match context, evidence links and timing. Busy or stale-epoch rejections
precede dispatch.

If the transfer response is lost, pause input and inspect `lease status`.
When it shows the intended recipient, new epoch and result ID, the coordinator
can retrieve that epoch's private reply with the recovery credential.

```sh
basaltw desktop --native team lease receipt --epoch 2 --credential "$RECOVERY_FILE" \
  --result 000001 --json
```

This command reads the current lease after a handoff or recovery. It keeps the
epoch and credential in place and refuses uncertain input. Keep the response
private and pass the owner path only to the assigned recipient. If the current
credential file is unavailable, follow the coordinator recovery procedure.
Check the run ledger separately before the recipient acts.

If a worker disappears, inspect its orchestration state, lease status and last
input receipt. Keep input idle while resolving any uncertain action. An input
without a confirmed server outcome leaves `pending_input` on the lease. The
coordinator verifies the live screen and neutral controls, writes a new result
describing what actually happened, and uses `lease recover --epoch E
--credential RECOVERY_FILE --worker coordinator --result ID --note RECONCILIATION`.
This rotates ownership and binds it to the observed running helper generation.
An action report with `unknown` or `partial` delivery remains useful evidence
while reconciliation is underway. Use human control to inspect or stop when
necessary. After closing the owned work, the last owner publishes a final
result and calls `lease release --epoch E --credential LEASE_FILE --result ID
--note CLOSEOUT`. The archived lease journal stays in the team directory.

The lease covers Basaltwater helper operations and pad action admission.
It preserves human override, helper stop, and pad stop. It shares the existing
desktop account's trust model. Direct shell tools, other clients with their own
desktop access, and independent desktop automations are outside this route.

## Resume orchestration from durable results

Use `ready`, `finished`, `blocked`, or `interrupted` for result state and
`none`, `not-sent`, `completed`, `partial`, or `unknown` for delivery.
Results also accept linked decision IDs and copy selected evidence. Have the
worker return the assignment, result ID and current lease epoch in its
orchestrator completion. On wake, compare them to the current lease and run
context. A delayed return can be genuine history from before another player
resumed. When ownership moves to the coordinator to clear a blocker, notify
the coordinator through the orchestrator with the durable result ID and epoch
promptly so recovery can begin. `team results TEAM --after CURSOR --json` reads
up to 20 new entries per page and returns the next cursor and number remaining.
Basaltwater records the result. The orchestration host delivers notifications.

## Qualification

The offline desktop suite exercises brief generation, concurrent read-only review,
an independent resource, competing input and handoff, cross-device capture
consumption, worker interruption, and revision checks when a card reopens a
Ready seat's shop. Qualify each live route in a disposable application with
the actual client and serving helper. Check that a former owner and an offline
worker are rejected, the receiver needs a fresh capture, a gamepad hold
releases before transfer, and human override remains available. Then run a
representative team task and record handoff latency, stale decisions, any
advice that arrived in time, and the final outcome.

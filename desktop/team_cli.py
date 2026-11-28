"""Command line for desktop team artifacts and local resource ownership."""

from __future__ import annotations

from desktop import team_lease, team_records
from lib.tool_results import add_json_to_leaves


def add_team_parser(commands):
    parser = commands.add_parser("team", help="Prepare worker briefs, share observations, and assign local desktop ownership")
    sub = parser.add_subparsers(dest="team_command", required=True)
    prepare = sub.add_parser("prepare", help="Generate private worker briefs from one team specification")
    prepare.add_argument("--spec", required=True)
    prepare.add_argument("--output")
    observe = sub.add_parser("observe", help="Publish changed scopes and worker-specific image copies")
    observe.add_argument("--after", required=True, help="Expected observation head ID or initial")
    view = sub.add_parser("view", help="Return a worker's scoped view and applicable revisions")
    view.add_argument("--observation", help="Historical observation ID; defaults to latest")
    decision = sub.add_parser("decide", help="Record a proposal against one scoped observation")
    decision.add_argument("--observation", required=True)
    check = sub.add_parser("check", help="Compare a proposal's dependencies with the latest revisions")
    check.add_argument("team")
    check.add_argument("decision")
    result = sub.add_parser("result", help="Publish a structured worker update and optional evidence")
    results = sub.add_parser("results", help="Read structured worker updates since a cursor")
    results.add_argument("team")
    results.add_argument("--after", default="000000")
    results.add_argument("--worker")
    for command in (observe, view, decision, result):
        command.add_argument("team")
        command.add_argument("worker")
        if command is not view:
            command.add_argument("--spec", required=True)
    lease = sub.add_parser("lease", help="Inspect or change owner of the selected physical desktop")
    actions = lease.add_subparsers(dest="lease_command", required=True)
    actions.add_parser("status")
    receipt = actions.add_parser("receipt", help="Recover the private reply for the current completed lease transfer")
    receipt.add_argument("--epoch", required=True, type=int, help="Current recipient epoch")
    receipt.add_argument("--credential", required=True, help="Private coordinator recovery file path")
    receipt.add_argument("--result", required=True, help="Result ID recorded on the current transfer")
    claim = actions.add_parser("claim")
    claim.add_argument("team")
    claim.add_argument("resource")
    claim.add_argument("--worker", default="coordinator")
    for name in ("handoff", "recover", "release"):
        action = actions.add_parser(name)
        action.add_argument("--epoch", required=True, type=int)
        action.add_argument("--credential", required=True, help="Private lease file path, or recovery file path for recover")
        action.add_argument("--result", required=True, help="New result ID published by the current owner or recovering coordinator")
        action.add_argument("--note", required=True)
        if name != "release":
            action.add_argument("--worker", required=True)
    add_json_to_leaves(parser)


def run_team_command(args, *, backend, native):
    operation = args.team_command
    if operation == "lease":
        if args.lease_command == "status":
            return team_lease.status(native=native)
        if args.lease_command == "claim":
            return team_lease.claim(args.team, args.resource, args.worker, native=native, backend=backend)
        if args.lease_command == "receipt":
            return team_lease.receipt(native=native, epoch=args.epoch, credential=args.credential, result=args.result)
        return team_lease.transition(args.lease_command, native=native, backend=backend, epoch=args.epoch,
                                     worker=getattr(args, "worker", None), credential=args.credential,
                                     result=args.result, note=args.note)
    if operation == "prepare":
        return team_records.prepare(args.spec, args.output)
    if operation == "view":
        return team_records.view(args.team, args.worker, args.observation)
    if operation == "check":
        return team_records.check(args.team, args.decision)
    if operation == "results":
        return team_records.results(args.team, args.after, args.worker)
    spec = team_records.object_file(args.spec)
    if operation == "observe":
        return team_records.observe(args.team, args.worker, args.after, spec)
    if operation == "decide":
        return team_records.decide(args.team, args.worker, args.observation, spec)
    return team_records.result(args.team, args.worker, spec)

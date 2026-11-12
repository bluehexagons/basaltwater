"""CLI registration for bounded private native QA tools."""

from __future__ import annotations

import argparse


def add_qa_parser(commands: argparse._SubParsersAction, evidence: argparse._SubParsersAction) -> None:
    process = evidence.add_parser("process", help="Retain one selected current-account Linux PID/start identity; no desktop input or process control")
    process.add_argument("--pid", type=int, required=True)
    process.add_argument("--output", help="New private directory outside Git")
    process.add_argument("--json", action="store_true")
    conditional = evidence.add_parser("conditional", help="Validate offline plans or explicitly run bounded native observe/act/verify")
    conditional_ops = conditional.add_subparsers(dest="conditional_command", required=True)
    check_plan = conditional_ops.add_parser("validate", help="Offline schema validation; grants no input authority")
    check_plan.add_argument("plan")
    check_plan.add_argument("--json", action="store_true")
    for name in ("execute", "wait"):
        operation = conditional_ops.add_parser(name, help="Requires --native and existing enabled control; never starts or refreshes helper")
        operation.add_argument("plan")
        operation.add_argument("--profile", required=True)
        operation.add_argument("--template", nargs=2, action="append", default=[], metavar=("TEMPLATE_ID", "PNG"))
        operation.add_argument("--output", help="New private directory outside Git")
        operation.add_argument("--json", action="store_true")
    perception = evidence.add_parser("perception", help="Offline fixed-region recognition and frozen held-out evaluation")
    perception_ops = perception.add_subparsers(dest="perception_command", required=True)
    recognize = perception_ops.add_parser("recognize")
    recognize.add_argument("artifact", help="Verified record bundle manifest.json")
    recognize.add_argument("packet_id")
    recognize.add_argument("frame_id")
    evaluate = perception_ops.add_parser("evaluate")
    evaluate.add_argument("dataset", help="Frozen region-heldout JSON")
    evaluate.add_argument("--bundle", nargs=2, action="append", default=[], metavar=("RUN_ID", "MANIFEST"))
    for operation in (recognize, evaluate):
        operation.add_argument("--profile", required=True)
        operation.add_argument("--template", nargs=2, action="append", default=[], metavar=("TEMPLATE_ID", "PNG"))
        operation.add_argument("--output", help="New private directory outside Git")
        operation.add_argument("--json", action="store_true")
    corpus = evidence.add_parser("corpus", help="Offline closed-corpus validation and condition-aware summaries")
    corpus.add_argument("source", help="Version-1 corpus manifest JSON")
    corpus.add_argument("--source", dest="sources", nargs=2, action="append", default=[], metavar=("SOURCE_ID", "RECORDS_JSON"))
    corpus.add_argument("--output", help="New private directory outside Git")
    corpus.add_argument("--json", action="store_true")
    records = evidence.add_parser("records", help="Offline immutable observation/decision/coach bundles; no input authority")
    record_ops = records.add_subparsers(dest="records_command", required=True)
    create = record_ops.add_parser("create")
    create.add_argument("source", help="Version-1 closed record graph JSON")
    create.add_argument("--media", nargs=2, action="append", default=[], metavar=("FRAME_ID", "PNG"))
    create.add_argument("--output", help="New private bundle outside Git")
    validate = record_ops.add_parser("validate")
    validate.add_argument("artifact", help="Private manifest.json returned by create")
    validate.add_argument("--current-state", help="Explicit current revision, or unknown")
    validate.add_argument("--at", help="Explicit evaluation UTC; pair with --current-state")
    for operation in (create, validate):
        operation.add_argument("--json", action="store_true")
    run = evidence.add_parser("run", help="Offline controller-written match clocks/checkpoints; no watcher or input")
    operations = run.add_subparsers(dest="run_command", required=True)
    start = operations.add_parser("start")
    start.add_argument("--run-id", required=True)
    start.add_argument("--match-id", required=True)
    start.add_argument("--phase", required=True)
    start.add_argument("--output")
    for name in ("show", "checkpoint", "suspend", "resume", "context", "policy", "finish"):
        operation = operations.add_parser(name)
        operation.add_argument("artifact", help="Private run.json returned by start; continue the same match")
        if name != "show":
            operation.add_argument("--phase")
            operation.add_argument("--note", help="Policy changes require the authorization/instruction source" if name == "policy" else None)
            operation.add_argument("--context")
            operation.add_argument("--artifact", dest="artifacts", action="append", default=[], help="Link evidence/recording segment without resetting run clocks")
            operation.add_argument("--game-clock", help="Operator-observed game clock, distinct from host time")
        if name == "policy":
            policy = operation
        operation.add_argument("--json", action="store_true")
    for operation in (start, policy):
        operation.add_argument("--review-seconds", type=int, help="Controller-local soft review cadence; never pauses for approval")
        limits = operation.add_mutually_exclusive_group() if operation is policy else operation
        limits.add_argument("--hard-seconds", type=int, help="Hard wall limit from ORIGINAL start; controller must enforce it")
        if operation is policy:
            limits.add_argument("--clear-hard-limit", action="store_true",
                                help="Explicitly remove the hard limit with a source note; grants no permission. Omission keeps the current limit")
    start.add_argument("--json", action="store_true")
    closeout = evidence.add_parser("closeout", help="Save a new read-only inventory/verification receipt; never stop apps or delete media")
    closeout.add_argument("artifact", help="Recording JSON artifact returned by evidence start/finish; not a directory or closeout receipt")
    closeout.add_argument("--retention", nargs=2, action="append", default=[], metavar=("RECEIPT", "SHA256"),
                          help="Explicitly pinned wait receipt for intentional ring evictions; repeat up to 16")
    closeout.add_argument("--extra", action="append", default=[], help="Extra PNG capture outside the recorder; repeat up to 50")
    closeout.add_argument("--derived", action="append", default=[], help="Literal crop manifest; repeat up to 16")
    closeout.add_argument("--previous", help="Prior closeout.json for the SAME recording; different recording segments belong in evidence run checkpoint --artifact")
    closeout.add_argument("--process", nargs=2, action="append", default=[], metavar=("PID", "START_TICKS"),
                          help="Read-only exact process identity check; never claims normal exit or stops an active match")
    closeout.add_argument("--output")
    closeout.add_argument("--json", action="store_true")
    audio = commands.add_parser("audio", help="Application-only PipeWire capture and offline WAV analysis")
    operations = audio.add_subparsers(dest="audio_command", required=True)
    sources = operations.add_parser("sources", help="List playback node serials for an exact application PID")
    sources.add_argument("--pid", type=int, required=True)
    capture = operations.add_parser("capture", help="Experimental stereo application capture; preserves existing playback links")
    capture.add_argument("--pid", type=int, required=True)
    capture.add_argument("--node-serial", type=int, required=True)
    capture.add_argument("--seconds", type=int, default=10)
    analysis = operations.add_parser("analyze", help="Analyze at most 120 seconds of WAV without playing it")
    analysis.add_argument("path")
    analysis.add_argument("--capture-manifest", help="Hash-matching qualified application capture receipt for timing provenance")
    crop = evidence.add_parser("crop", help="Create a literal PNG crop and captioned report with source hashes")
    crop.add_argument("path")
    crop.add_argument("--rectangle", nargs=4, type=int, required=True, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    crop.add_argument("--caption", required=True)
    excerpt = evidence.add_parser("excerpt", help="Create a WAV excerpt of at most ten seconds with interval provenance")
    excerpt.add_argument("path")
    excerpt.add_argument("--start", type=float, required=True)
    excerpt.add_argument("--seconds", type=float, required=True)
    export = evidence.add_parser("export", help="Copy a reviewed derivative bundle with portable references")
    export.add_argument("path", help="Derivative manifest.json")
    export.add_argument("--reviewed-by", required=True, help="Affirm pixels/audio, caption and privacy have been reviewed")
    export.add_argument("--output", required=True, help="New directory beneath an existing private parent outside Git")
    timeline = evidence.add_parser("timeline", help="Join action, accessibility and audio receipts from the same Linux boot")
    timeline.add_argument("--artifact", action="append", required=True)
    for operation in (capture, analysis, crop, excerpt, timeline):
        operation.add_argument("--output", help="New private directory outside Git; default creates a private temporary bundle")
    for operation in (sources, capture, analysis, crop, excerpt, export, timeline):
        operation.add_argument("--json", action="store_true")


def run_qa_command(args: argparse.Namespace, *, backend=None) -> dict:
    if args.desktop_command == "evidence" and args.evidence_command == "process":
        from desktop.qa_artifacts import process_snapshot
        return process_snapshot(args.pid, args.output)
    if args.desktop_command == "evidence" and args.evidence_command == "conditional":
        from desktop import conditional, conditional_contract, conditional_native, perception
        from desktop.record_bundles import _read
        from lib.atomic_io import parse_json
        try:
            plan = parse_json(_read(args.plan, 65536))
            conditional_contract.validate(plan)
            if args.conditional_command == "validate":
                return {"validation": "valid", "schema_version": 1, "plan_id": plan["id"], "input_authority": False}
            if not getattr(args, "native", False) or backend is None:
                raise ValueError("Conditional execution/event waits require --native")
            paths = dict(args.template)
            if len(paths) != len(args.template):
                raise ValueError("Duplicate template ID")
            _, profile, templates = perception.load_profile(args.profile, paths)
            controller = conditional_native.NativeController(backend, profile, templates,
                **{key: plan[key] for key in ("run_id", "match_id", "phase")})
            operation = conditional.execute if args.conditional_command == "execute" else conditional.wait
            return operation(plan, controller, output=args.output)
        except (OSError, ValueError, RuntimeError) as exc:
            return getattr(exc, "receipt", {"error": str(exc), "partial": False, "input_authority": False})
    if args.desktop_command == "evidence" and args.evidence_command == "perception":
        from desktop import perception_reports
        try:
            templates = dict(args.template)
            if len(templates) != len(args.template):
                raise ValueError("Duplicate template ID")
            if args.perception_command == "recognize":
                return perception_reports.recognize(args.artifact, args.packet_id, args.frame_id,
                                                     args.profile, templates, args.output)
            bundles = dict(args.bundle)
            if len(bundles) != len(args.bundle):
                raise ValueError("Duplicate held-out run binding")
            return perception_reports.evaluate(args.dataset, bundles, args.profile, templates, args.output)
        except (OSError, ValueError, RuntimeError) as exc:
            return getattr(exc, "receipt", {"error": str(exc), "partial": False, "input_authority": False})
    if args.desktop_command == "evidence" and args.evidence_command == "corpus":
        from desktop import corpus
        try:
            inputs = dict(args.sources)
            if len(inputs) != len(args.sources):
                raise ValueError("Duplicate corpus source ID")
            return corpus.create(args.source, inputs, args.output)
        except (OSError, ValueError, RuntimeError) as exc:
            return getattr(exc, "receipt", {"error": str(exc), "partial": False, "input_authority": False})
    if args.desktop_command == "evidence" and args.evidence_command == "records":
        from desktop import record_bundles
        try:
            if args.records_command == "create":
                media = dict(args.media)
                if len(media) != len(args.media):
                    raise ValueError("Duplicate media frame ID")
                return record_bundles.create(args.source, media, args.output)
            return record_bundles.validate(args.artifact, current_state_revision=args.current_state, at=args.at)
        except (OSError, ValueError, RuntimeError) as exc:
            return getattr(exc, "receipt", {"error": str(exc), "validation": "invalid",
                                            "partial": False, "input_authority": False})
    if args.desktop_command == "evidence" and args.evidence_command == "run":
        from desktop import run_checkpoint
        if args.run_command == "start":
            return run_checkpoint.start(args.run_id, args.match_id, args.phase, output=args.output,
                                        review_seconds=args.review_seconds, hard_seconds=args.hard_seconds)
        fields = ("phase", "note", "context", "review_seconds", "hard_seconds", "clear_hard_limit", "artifacts", "game_clock")
        return run_checkpoint.update(args.artifact, args.run_command,
                                     **{key: getattr(args, key) for key in fields if hasattr(args, key)})
    if args.desktop_command == "evidence" and args.evidence_command == "closeout":
        from desktop.evidence import closeout
        return closeout(args.artifact, extras=args.extra, derivatives=args.derived, output=args.output,
                        previous=args.previous, processes=args.process, retention=args.retention)
    if args.desktop_command == "audio":
        from desktop import audio_capture, audio_analysis
        if args.audio_command == "sources":
            return audio_capture.sources(args.pid)
        if args.audio_command == "capture":
            return audio_capture.capture(args.pid, args.node_serial, args.seconds, args.output)
        return audio_analysis.analyze(args.path, args.output, capture_manifest=args.capture_manifest)
    from desktop import evidence_media, qa_timeline
    if args.evidence_command == "crop":
        return evidence_media.crop(args.path, args.rectangle, args.caption, args.output)
    if args.evidence_command == "excerpt":
        return evidence_media.excerpt(args.path, args.start, args.seconds, args.output)
    if args.evidence_command == "export":
        return evidence_media.export_reviewed(args.path, args.output, args.reviewed_by)
    return qa_timeline.timeline(args.artifact, args.output)

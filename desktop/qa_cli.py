"""CLI registration for bounded private native QA tools."""

from __future__ import annotations

import argparse


def add_qa_parser(commands: argparse._SubParsersAction, evidence: argparse._SubParsersAction) -> None:
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
    closeout.add_argument("artifact")
    closeout.add_argument("--extra", action="append", default=[], help="Extra PNG capture outside the recorder; repeat up to 50")
    closeout.add_argument("--derived", action="append", default=[], help="Literal crop manifest; repeat up to 16")
    closeout.add_argument("--previous", help="Preserve a previous closeout receipt when rechecking restored evidence")
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


def run_qa_command(args: argparse.Namespace) -> dict:
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
                        previous=args.previous, processes=args.process)
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

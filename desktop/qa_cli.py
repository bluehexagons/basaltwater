"""CLI registration for bounded private native QA tools."""

from __future__ import annotations

import argparse


def add_qa_parser(commands: argparse._SubParsersAction, evidence: argparse._SubParsersAction) -> None:
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

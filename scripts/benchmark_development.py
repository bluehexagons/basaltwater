#!/usr/bin/env python3
"""Measure bounded, offline CLI and focused-test workloads; write private JSON."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.agent_resources import inspect_workload_limits
from lib.streamed_process import CommandOutputLimitError, run_captured
from lib.validation import validate_filesystem_path


WORKLOADS = {
    "version": ["--version"],
    "help": ["--help"],
    "setup-help": ["setup", "--help"],
    "network-help": ["network", "--help"],
    "smoke": ["--suite", "smoke"],
    "validation": ["tests.test_validation"],
    "network": ["tests.test_network_setup"],
    "atomic": ["tests.test_atomic_io"],
}
TEST_WORKLOADS = frozenset(("smoke", "validation", "network", "atomic"))


def _seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 1800:
        raise argparse.ArgumentTypeError("seconds must be finite and between 0 and 1800")
    return number


def _command(name: str, cli: str | None, *, checkout: Path = ROOT) -> list[str]:
    if name in TEST_WORKLOADS:
        return [sys.executable, str(checkout / "run_tests.py"), *WORKLOADS[name]]
    launcher = [cli] if cli else [sys.executable, str(checkout / "basaltwater.py")]
    return [*launcher, *WORKLOADS[name]]


def _measure(name: str, cli: str | None, timeout: float, *, checkout: Path = ROOT) -> dict:
    """Run in a fresh worker so child CPU/RSS accounting belongs to one trial."""
    before = inspect_workload_limits()
    started = time.perf_counter()
    result: dict = {"status": "completed", "return_code": None}
    try:
        completed = run_captured(_command(name, cli, checkout=checkout), timeout=timeout,
                                 max_bytes=1024 * 1024, cwd=str(checkout))
        result.update(return_code=completed.returncode,
                      stdout_bytes=len(completed.stdout.encode("utf-8")),
                      stderr_bytes=len(completed.stderr.encode("utf-8")))
        # Tests report their run/skip counts here. Raw output remains private.
        if name in TEST_WORKLOADS or completed.returncode:
            result["output_tail"] = (completed.stdout + completed.stderr)[-16384:]
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
    except CommandOutputLimitError:
        result["status"] = "output_limit"
    except OSError:
        result["status"] = "unavailable"
    result["elapsed_seconds"] = time.perf_counter() - started
    result["cpu_user_seconds"] = result["cpu_system_seconds"] = None
    result["largest_child_peak_rss_bytes"] = None
    result["child_block_io"] = {"ru_inblock": None, "ru_oublock": None}
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        result.update(cpu_user_seconds=usage.ru_utime, cpu_system_seconds=usage.ru_stime)
        result["child_block_io"] = {name: getattr(usage, name, None) for name in ("ru_inblock", "ru_oublock")}
        if sys.platform.startswith("linux") or sys.platform == "darwin":
            result["largest_child_peak_rss_bytes"] = usage.ru_maxrss * (1024 if sys.platform.startswith("linux") else 1)
    except ImportError:
        pass
    try:
        result["load_average"] = list(os.getloadavg())
    except (AttributeError, OSError):
        result["load_average"] = None
    result.update(observations_before=before, observations_after=inspect_workload_limits())
    return result


def _summary(samples: list[dict]) -> dict:
    times = sorted(sample["elapsed_seconds"] for sample in samples)
    return {"samples": len(samples), "median_seconds": statistics.median(times),
            "min_seconds": times[0], "max_seconds": times[-1],
            "p95_seconds": times[math.ceil(0.95 * len(times)) - 1] if len(times) >= 20 else None}


def _revision(checkout: Path = ROOT) -> dict:
    try:
        head = run_captured(["git", "rev-parse", "HEAD"], cwd=str(checkout), timeout=5, max_bytes=4096)
        state = run_captured(["git", "status", "--porcelain"],
                             cwd=str(checkout), timeout=5, max_bytes=65536)
        return {"commit": head.stdout.strip() if head.returncode == 0 else None,
                "dirty": bool(state.stdout) if state.returncode == 0 else None}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def _cancel(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", action="append", choices=WORKLOADS)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--timeout", type=_seconds, default=60)
    parser.add_argument("--total-seconds", type=_seconds, default=300)
    parser.add_argument("--checkout", default=str(ROOT), help="Absolute checkout to measure; use one benchmark revision for before/after runs")
    parser.add_argument("--cli", help="Absolute installed launcher path; tests always use the checkout")
    parser.add_argument("--cache-state", choices=("uncontrolled", "warm", "application-caches-disabled"), default="uncontrolled",
                        help="Label the prepared cache state; this command does not clear caches")
    parser.add_argument("--output", help="New private JSON evidence file (required outside internal workers)")
    parser.add_argument("--worker", choices=WORKLOADS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 30:
        parser.error("samples must be between 1 and 30")
    validate_filesystem_path(args.checkout, must_exist=True)
    checkout = Path(args.checkout)
    if not checkout.is_absolute() or not checkout.is_dir():
        parser.error("checkout must be an absolute directory")
    if args.cli:
        validate_filesystem_path(args.cli, must_exist=True)
        if not os.path.isabs(args.cli) or not Path(args.cli).is_file():
            parser.error("cli must be an absolute launcher file")
    if args.worker:
        # Let the shared process supervisor clean up the command on cancellation.
        if os.name == "posix":
            signal.signal(signal.SIGTERM, _cancel)
        print(json.dumps(_measure(args.worker, args.cli, args.timeout, checkout=checkout)))
        return 0
    if not args.output:
        parser.error("output is required")
    validate_filesystem_path(args.output)
    output = Path(args.output)
    if not output.is_absolute():
        parser.error("output must be absolute")
    # Refuse overwrite/symlink destinations and restrict evidence permissions.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    if os.name == "posix":
        os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        names = list(dict.fromkeys(args.workload or WORKLOADS))
        deadline = time.monotonic() + args.total_seconds
        revision = _revision(checkout)
        report = {"schema": 1, "revision": revision, "checkout": str(checkout),
                  "benchmark_revision": revision if checkout == ROOT else _revision(),
                  "python": platform.python_version(),
                  "platform": platform.system(), "machine": platform.machine(),
                  "cache_state": args.cache_state, "cli": args.cli or "checkout",
                  "limits": {"samples": args.samples, "timeout_seconds": args.timeout,
                             "total_seconds": args.total_seconds, "output_bytes_per_trial": 1024 * 1024},
                  "metric_scope": {"elapsed": "command supervision/capture; excludes worker setup and observation probes",
                                   "rss": "kernel-recorded largest reaped child; not aggregate RSS or page cache",
                                   "block_io": "raw getrusage counters; platform-dependent units, excluding cached reads",
                                   "output_bytes": "UTF-8 bytes after replacement decoding",
                                   "observations": "shared visible process allocation; not exclusive job attribution"},
                  "workloads": {}, "status": "completed"}
        failed = False
        try:
            for name in names:
                samples = []
                entry = report["workloads"][name] = {"arguments": WORKLOADS[name], "trials": samples}
                for _ in range(args.samples):
                    remaining = deadline - time.monotonic()
                    if remaining <= 15:
                        report["status"] = "budget_exhausted"
                        failed = True
                        break
                    timeout = min(args.timeout, remaining - 15)
                    command = [sys.executable, str(Path(__file__).resolve()), "--worker", name,
                               "--timeout", str(timeout), "--checkout", str(checkout)]
                    if args.cli:
                        command.extend(["--cli", args.cli])
                    trial = run_captured(command, cwd=str(ROOT), timeout=timeout + 15, max_bytes=128 * 1024)
                    sample = json.loads(trial.stdout) if trial.returncode == 0 else {"status": "worker_failed"}
                    samples.append(sample)
                    if sample.get("status") != "completed" or sample.get("return_code") != 0:
                        failed = True
                        break
                if samples and all("elapsed_seconds" in sample for sample in samples):
                    entry["summary"] = _summary(samples)
                if failed:
                    break
        except (OSError, subprocess.SubprocessError, ValueError, KeyboardInterrupt) as exc:
            failed = True
            report["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "worker_failed"
        finally:
            if failed and report["status"] == "completed":
                report["status"] = "failed"
            json.dump(report, stream, indent=2)
            stream.write("\n")
    print(f"Benchmark {report['status']}: {output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

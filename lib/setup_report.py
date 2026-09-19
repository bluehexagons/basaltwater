"""Collect important setup output for a concise end-of-run report."""

from __future__ import annotations

import contextlib
import contextvars
import re
import sys
from dataclasses import dataclass
from typing import Iterator, TextIO


_ACTIVE_REPORT: contextvars.ContextVar["SetupReport | None"] = contextvars.ContextVar(
    "basaltwater_setup_report",
    default=None,
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class SetupNote:
    """One warning or error worth repeating in the final setup summary."""

    severity: str
    message: str
    step: str | None = None


class _ObservingStream:
    """Forward text to a stream while allowing the report to inspect lines."""

    def __init__(self, stream: TextIO, report: "SetupReport", channel: str) -> None:
        self._stream = stream
        self._report = report
        self._channel = channel
        self._pending = ""

    def write(self, text: str) -> int:
        result = self._stream.write(text)
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._report.observe(line, channel=self._channel)
        return result

    def flush(self) -> None:
        self._stream.flush()
        if self._pending:
            self._report.observe(self._pending, channel=self._channel)
            self._pending = ""

    def isatty(self) -> bool:
        return self._stream.isatty()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def get_setup_report() -> "SetupReport | None":
    """Return the report active for the current setup invocation, if any."""

    return _ACTIVE_REPORT.get()


class SetupReport:
    """Collect and render important warning/error output from setup steps.

    Output remains live on the original streams.  The observer only records
    lines carrying an explicit warning/error marker, so normal progress output
    is not repeated at the end of a run.
    """

    _WARNING_MARKERS = ("⚠", "warning:", "warning -", "warning ")
    _ERROR_MARKERS = (
        "✗",
        "error:",
        "error -",
        "error ",
        "failed:",
        "failed ",
        "could not ",
        "unable to ",
    )

    def __init__(self) -> None:
        self._notes: list[SetupNote] = []
        self._seen: set[tuple[str, str, str | None]] = set()
        self.current_step: str | None = None

    @property
    def notes(self) -> tuple[SetupNote, ...]:
        return tuple(self._notes)

    def set_step(self, step: str | None) -> None:
        self.current_step = step

    def add(
        self,
        severity: str,
        message: str,
        *,
        step: str | None = None,
    ) -> None:
        normalized = self._normalize_message(message)
        if not normalized:
            return
        if severity not in {"warning", "error"}:
            raise ValueError(f"Unsupported setup note severity: {severity}")
        note = SetupNote(severity, normalized, step if step is not None else self.current_step)
        key = (note.severity, note.message, note.step)
        if key in self._seen:
            return
        self._seen.add(key)
        self._notes.append(note)

    def warning(self, message: str, *, step: str | None = None) -> None:
        self.add("warning", message, step=step)

    def error(self, message: str, *, step: str | None = None) -> None:
        self.add("error", message, step=step)

    def observe(self, line: str, *, channel: str = "stdout") -> None:
        """Record an explicitly marked warning/error line from setup output."""

        del channel  # Reserved for future structured stream filtering.
        message = _ANSI_ESCAPE.sub("", line).strip()
        if message.startswith("✓"):
            return
        lowered = message.lower()
        if any(marker in lowered for marker in self._ERROR_MARKERS):
            self.error(message)
        elif any(marker in lowered for marker in self._WARNING_MARKERS):
            self.warning(message)

    @staticmethod
    def _normalize_message(message: str) -> str:
        normalized = _ANSI_ESCAPE.sub("", message).strip()
        return re.sub(r"^(?:[✗⚠]\s*)+", "", normalized).strip()

    @contextlib.contextmanager
    def activate(self) -> Iterator["SetupReport"]:
        token = _ACTIVE_REPORT.set(self)
        try:
            yield self
        finally:
            _ACTIVE_REPORT.reset(token)

    @contextlib.contextmanager
    def capture(self) -> Iterator["SetupReport"]:
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        stdout = _ObservingStream(original_stdout, self, "stdout")
        stderr = _ObservingStream(original_stderr, self, "stderr")
        sys.stdout = stdout
        sys.stderr = stderr
        try:
            yield self
        finally:
            try:
                stdout.flush()
                stderr.flush()
            finally:
                sys.stdout = original_stdout
                sys.stderr = original_stderr

    def render(self) -> None:
        """Print a compact, grouped report after setup output has completed."""

        if not self._notes:
            return
        errors = [note for note in self._notes if note.severity == "error"]
        warnings = [note for note in self._notes if note.severity == "warning"]
        print("\nRun notes:")
        if errors:
            print(f"  Errors ({len(errors)}):")
            self._render_notes(errors)
        if warnings:
            print(f"  Warnings ({len(warnings)}):")
            self._render_notes(warnings)

    @staticmethod
    def _render_notes(notes: list[SetupNote]) -> None:
        for note in notes:
            context = f" [{note.step}]" if note.step else ""
            marker = "✗" if note.severity == "error" else "⚠"
            print(f"    {marker}{context} {note.message}")

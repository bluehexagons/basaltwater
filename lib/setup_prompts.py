"""Relay target-side setup prompts through the controller's existing connection."""

from __future__ import annotations

import json
import os
import sys

PROMPT_RELAY_ENV = "BASALTWATER_SETUP_PROMPT_RELAY"
PROMPT_PREFIX = "BASALTWATER_SETUP_PROMPT "
MAX_PROMPT_LENGTH = 4096


def setup_input(message: str) -> str:
    """Use terminal input locally, or a framed request on the setup stream."""
    if os.environ.get(PROMPT_RELAY_ENV) != "1":
        return input(message)
    if len(message) > MAX_PROMPT_LENGTH:
        raise ValueError("Setup prompt is too long")
    print("\n" + PROMPT_PREFIX + json.dumps(message), flush=True)
    response = sys.stdin.readline(MAX_PROMPT_LENGTH * 6 + 2)
    if not response or not response.endswith("\n"):
        raise RuntimeError("Setup prompt connection closed or response exceeded its limit")
    value = json.loads(response)
    if not isinstance(value, str) or len(value) > MAX_PROMPT_LENGTH:
        raise ValueError("Invalid setup prompt response")
    return value


def answer_setup_prompt(line: str) -> bytes | None:
    """Answer a framed prompt at the controller; leave ordinary output alone."""
    if not line.startswith(PROMPT_PREFIX):
        return None
    message = json.loads(line.removeprefix(PROMPT_PREFIX))
    if not isinstance(message, str) or len(message) > MAX_PROMPT_LENGTH:
        raise ValueError("Invalid target setup prompt")
    answer = input(message)
    if len(answer) > MAX_PROMPT_LENGTH:
        raise ValueError("Setup prompt response is too long")
    return (json.dumps(answer) + "\n").encode("utf-8")

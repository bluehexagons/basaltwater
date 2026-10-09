"""Project-local model selection and reviewed outcomes, without paid model calls."""

from __future__ import annotations

import argparse
import fcntl
import math
import os
import re
import stat
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from lib.agent_workspace import primary_agent_repository
from lib.atomic_io import read_json_file, write_json_atomic
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


SAFE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
_FAMILIES = ("luna", "sol", "astra")
_OUTCOMES = ("accepted", "reworked", "failed")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_TEMPLATE = Path(__file__).resolve().parents[1] / "common/agent_skills/basaltwater-subagents/assets/agent-models.json"
_MAX_BYTES = 2 * 1024 * 1024


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


def _number(value: object, label: str, *, minimum: float = 0) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"Invalid {label}: expected a finite number >= {minimum}")
    return float(value)


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("Expected an ISO date")
    return date.fromisoformat(value)


def validate_model_policy(value: object) -> JSONDict:
    """Validate configurable model IDs and capabilities rather than a release list."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported agent model policy schema")
    for key in ("catalog_max_age_days", "evidence_max_age_days", "minimum_samples"):
        if type(value.get(key)) is not int or not 1 <= value[key] <= 3650:
            raise ValueError(f"Invalid {key}")
    if not 0 < _number(value.get("minimum_acceptance"), "minimum_acceptance") <= 1:
        raise ValueError("minimum_acceptance must be in (0, 1]")
    models, tasks = value.get("models"), value.get("tasks")
    if not isinstance(models, list) or not models or len(models) > 100:
        raise ValueError("Expected 1-100 catalog models")
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("Invalid model entry")
        identifier = _name(model.get("id"), "model ID")
        if identifier in seen or model.get("provider") != "openai" or model.get("family") not in _FAMILIES:
            raise ValueError("Models must have unique IDs and an OpenAI Luna, Sol, or Astra role")
        seen.add(identifier)
        efforts = model.get("efforts")
        if not isinstance(efforts, list) or not efforts or any(effort not in SAFE_EFFORTS for effort in efforts):
            raise ValueError("Catalog efforts must exclude max and ultra")
        for field in ("input_usd_per_million", "output_usd_per_million"):
            _number(model.get(field), field)
        _date(model.get("verified_on"))
        source = model.get("source")
        if not isinstance(source, str) or not source.startswith((
            "https://developers.openai.com/", "https://platform.openai.com/",
        )) or len(source) > 500 or any(ord(char) < 32 for char in source):
            raise ValueError("Model metadata needs an official OpenAI source URL")
    if not isinstance(tasks, dict) or not tasks or len(tasks) > 100:
        raise ValueError("Expected 1-100 task policies")
    for name, task in tasks.items():
        _name(name, "task class")
        if not isinstance(task, dict) or task.get("family") not in _FAMILIES or task.get("effort") not in SAFE_EFFORTS:
            raise ValueError("Invalid task family or effort")
        _name(task.get("evaluation"), "evaluation version")
        for field in ("expected_input_tokens", "expected_output_tokens"):
            _number(task.get(field), field, minimum=1)
    return value


def _paths(repository: str) -> tuple[Path, Path, Path]:
    primary = Path(primary_agent_repository(repository))
    directory = primary / ".basaltwater"
    validate_filesystem_path(str(directory))
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise ValueError("Unsafe project model directory")
    return primary, directory / "agent-models.json", directory / "agent-model-results.json"


@contextmanager
def _lock(primary: Path) -> Iterator[None]:
    # The primary checkout owns the shared Git directory; avoid a tracked lock.
    path = primary / ".git/basaltwater-agent-models.lock"
    validate_filesystem_path(str(path))
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("Expected a primary checkout with a Git directory")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("Unsafe model-results lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another parent is updating project model data; retry after it finishes") from exc
        yield
    finally:
        os.close(descriptor)


def init_model_policy(repository: str) -> JSONDict:
    """Create a reviewable project catalog, preserving any existing policy."""
    primary, policy_path, _ = _paths(repository)
    with _lock(primary):
        if os.path.lexists(policy_path):
            raise ValueError("Project model policy already exists; review and edit it explicitly")
        policy = validate_model_policy(read_json_file(str(_TEMPLATE)))
        write_json_atomic(str(policy_path), policy)
    return {"path": str(policy_path), "status": "created", "catalog_requires_review": True}


def _policy_and_runs(repository: str) -> tuple[JSONDict, list[JSONDict]]:
    _, policy_path, results_path = _paths(repository)
    policy = validate_model_policy(read_json_file(str(policy_path)))
    try:
        results = read_json_file(str(results_path), max_bytes=_MAX_BYTES)
    except FileNotFoundError:
        return policy, []
    if not isinstance(results, dict) or results.get("schema_version") != 1 or not isinstance(results.get("runs"), list):
        raise ValueError("Unsupported project model results schema")
    for run in results["runs"]:
        _validate_run(run)
    return policy, results["runs"]


def _validate_run(run: object) -> None:
    if not isinstance(run, dict):
        raise ValueError("Invalid model outcome")
    for field in ("task", "evaluation", "model"):
        _name(run.get(field), field)
    if run.get("effort") not in SAFE_EFFORTS or run.get("outcome") not in _OUTCOMES or run.get("service") != "standard":
        raise ValueError("Outcomes require a safe effort, standard service, and reviewed outcome")
    _date(run.get("date"))
    for field in ("cost_usd", "seconds", "rework_minutes"):
        if run.get(field) is not None:
            _number(run[field], field)
    if type(run.get("attempts")) is not int or not 1 <= run["attempts"] <= 1000:
        raise ValueError("Invalid attempt count")
    validation = run.get("validation")
    if not isinstance(validation, str) or not 1 <= len(validation) <= 500 or any(ord(char) < 32 for char in validation):
        raise ValueError("Provide a printable validation result (1-500 characters)")


def record_model_outcome(repository: str, values: JSONDict) -> JSONDict:
    """Record the parent's reviewed result under the primary repository lock."""
    primary, _, results_path = _paths(repository)
    with _lock(primary):
        policy, runs = _policy_and_runs(repository)
        task = _name(values.get("task"), "task class")
        if task not in policy["tasks"]:
            raise ValueError("Unknown project task class")
        model = next((model for model in policy["models"] if model["id"] == values.get("model")), None)
        if model is None or values.get("effort") not in model["efforts"]:
            raise ValueError("Record a catalog model and its supported effort")
        run = {key: values.get(key) for key in (
            "task", "model", "effort", "outcome", "cost_usd", "seconds", "rework_minutes", "attempts", "validation",
        )}
        run.update({"date": datetime.now(timezone.utc).date().isoformat(),
                    "evaluation": policy["tasks"][task]["evaluation"], "service": "standard"})
        _validate_run(run)
        result = {"schema_version": 1, "runs": [*runs, run]}
        # Match the bounded reader and preserve all prior results on failure.
        import json

        if len((json.dumps(result, indent=2) + "\n").encode("utf-8")) > _MAX_BYTES:
            raise ValueError("Model results are full; archive reviewed old results before recording more")
        write_json_atomic(str(results_path), result)
    return run


def _summary(runs: list[JSONDict]) -> JSONDict:
    accepted = sum(run["outcome"] == "accepted" for run in runs)
    known_costs = [run["cost_usd"] for run in runs if run["cost_usd"] is not None]
    known_rework = [run["rework_minutes"] for run in runs if run["rework_minutes"] is not None]
    return {
        "samples": len(runs), "accepted": accepted,
        "acceptance_rate": accepted / len(runs) if runs else None,
        "reworked": sum(run["outcome"] == "reworked" for run in runs),
        "failed": sum(run["outcome"] == "failed" for run in runs),
        "attempts": sum(run["attempts"] for run in runs),
        "cost_samples": len(known_costs), "known_cost_usd": sum(known_costs),
        "cost_per_accepted_usd": sum(known_costs) / accepted
        if accepted and len(known_costs) == len(runs) else None,
        "rework_samples": len(known_rework), "known_rework_minutes": sum(known_rework),
        "rework_minutes": sum(known_rework) if runs and len(known_rework) == len(runs) else None,
        "mean_seconds": sum(run["seconds"] for run in runs) / len(runs)
        if runs and all(run["seconds"] is not None for run in runs) else None,
    }


def report_model_outcomes(repository: str) -> list[JSONDict]:
    """Keep model versions, efforts, task classes, and evaluation versions separate."""
    _, runs = _policy_and_runs(repository)
    groups: dict[tuple[str, str, str, str], list[JSONDict]] = {}
    for run in runs:
        key = tuple(run[field] for field in ("task", "evaluation", "model", "effort"))
        groups.setdefault(key, []).append(run)
    return [{**dict(zip(("task", "evaluation", "model", "effort"), key)), **_summary(group)}
            for key, group in sorted(groups.items())]


def _available(path: str) -> dict[str, list[str]]:
    """Read a session inventory or Codex's public model-selector cache."""
    value = read_json_file(os.path.abspath(os.path.expanduser(path)))
    models = value.get("models") if isinstance(value, dict) else value
    if not isinstance(models, list) or len(models) > 100:
        raise ValueError("Available models must be a list or an object containing models")
    result: dict[str, list[str]] = {}
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("Invalid available model")
        if "visibility" in model and model["visibility"] != "list":
            continue
        identifier = _name(model.get("id", model.get("slug")), "available model ID")
        efforts = model.get("efforts")
        if efforts is None:
            levels = model.get("supported_reasoning_levels", [])
            if not isinstance(levels, list):
                raise ValueError("Invalid supported reasoning levels")
            efforts = [level.get("effort") for level in levels if isinstance(level, dict)]
        if not isinstance(efforts, list) or any(not isinstance(effort, str) for effort in efforts) or identifier in result:
            raise ValueError("Invalid or duplicate available model efforts")
        result[identifier] = [effort for effort in efforts if effort in SAFE_EFFORTS]
    return result


def recommend_agent_model(
    repository: str, task: str, available_path: str, *, astra_reason: str = "", today: date | None = None,
) -> JSONDict:
    """Choose from current capabilities using fresh project evidence and costs."""
    policy, runs = _policy_and_runs(repository)
    task = _name(task, "task class")
    if task not in policy["tasks"]:
        raise ValueError("Unknown project task class")
    rule = policy["tasks"][task]
    if not isinstance(astra_reason, str) or len(astra_reason) > 500 or any(ord(char) < 32 for char in astra_reason):
        raise ValueError("Invalid Astra justification")
    available = _available(available_path)
    now = today or datetime.now(timezone.utc).date()
    cutoff = now - timedelta(days=policy["evidence_max_age_days"])
    catalog_ids = {model["id"] for model in policy["models"]}
    unknown = sorted(set(available) - catalog_ids)
    stale: list[str] = []
    candidates: list[JSONDict] = []
    fallback: list[JSONDict] = []
    for model in policy["models"]:
        identifier = model["id"]
        if identifier not in available or model["family"] == "astra" and not astra_reason.strip():
            continue
        if not 0 <= (now - _date(model["verified_on"])).days <= policy["catalog_max_age_days"]:
            stale.append(identifier)
            continue
        efforts = [effort for effort in SAFE_EFFORTS
                   if effort in available[identifier] and effort in model["efforts"]
                   and SAFE_EFFORTS.index(effort) >= SAFE_EFFORTS.index(rule["effort"])]
        if not efforts:
            continue
        recent = [run for run in runs if run["task"] == task and run["evaluation"] == rule["evaluation"]
                  and run["model"] == identifier and cutoff <= _date(run["date"]) <= now]
        estimate = (rule["expected_input_tokens"] * model["input_usd_per_million"]
                    + rule["expected_output_tokens"] * model["output_usd_per_million"]) / 1_000_000
        for effort in efforts:
            evidence = [run for run in recent if run["effort"] == effort]
            if effort != efforts[0] and not evidence:
                continue
            candidate = {"model": identifier, "family": model["family"], "effort": effort,
                         "estimated_token_cost_usd": estimate, **_summary(evidence)}
            candidates.append(candidate)
            # Only the lowest supported effort is an automatic provisional choice.
            if (effort == efforts[0] and candidate["samples"] < policy["minimum_samples"]
                    and _FAMILIES.index(model["family"]) >= _FAMILIES.index(rule["family"])):
                fallback.append(candidate)
    proven = [candidate for candidate in candidates if candidate["samples"] >= policy["minimum_samples"]
              and candidate["acceptance_rate"] >= policy["minimum_acceptance"]]
    # Unproven alternatives are trials, not automatic replacements for a proven setting.
    pool = proven or fallback
    empirical = bool(proven) and all(candidate["cost_per_accepted_usd"] is not None for candidate in pool)
    cost_field = "cost_per_accepted_usd" if empirical else "estimated_token_cost_usd"
    selected = min(pool, key=lambda item: (
        item[cost_field], SAFE_EFFORTS.index(item["effort"]), item["model"],
    )) if pool else None
    return {
        "task": task, "evaluation": rule["evaluation"], "selection": selected,
        "service": "standard", "astra_reason": astra_reason.strip() or None,
        "basis": "reviewed_cost_per_accepted" if empirical else "token_cost_estimate",
        "provisional": bool(selected and selected["samples"] < policy["minimum_samples"]),
        "trial_candidates": [candidate for candidate in candidates if candidate["samples"] < policy["minimum_samples"]],
        "uncatalogued_models": unknown, "stale_models": stale,
        "refresh_required": bool(unknown or stale),
    }


def add_models_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("models", help="Learn cost-aware subagent choices per project")
    actions = parser.add_subparsers(dest="agent_models_command", required=True)
    for action in ("init", "recommend", "record", "report"):
        command = actions.add_parser(action)
        command.add_argument("repository", metavar="REPOSITORY")
        command.add_argument("--json", action="store_true")
        if action == "recommend":
            command.add_argument("task", metavar="TASK_CLASS")
            command.add_argument("--available", required=True, metavar="JSON_FILE")
            command.add_argument("--astra-reason", default="", metavar="JUSTIFICATION")
        if action == "record":
            for field in ("task", "model", "effort", "outcome", "validation"):
                choices = SAFE_EFFORTS if field == "effort" else _OUTCOMES if field == "outcome" else None
                command.add_argument(f"--{field}", required=True, choices=choices)
            for field in ("cost-usd", "seconds", "rework-minutes"):
                command.add_argument(f"--{field}", type=float)
            command.add_argument("--attempts", type=int, default=1)


def run_models_command(args: argparse.Namespace) -> int:
    import json

    try:
        if args.agent_models_command == "init":
            result = init_model_policy(args.repository)
        elif args.agent_models_command == "recommend":
            result = recommend_agent_model(args.repository, args.task, args.available, astra_reason=args.astra_reason)
        elif args.agent_models_command == "record":
            result = record_model_outcome(args.repository, vars(args))
        else:
            result = report_model_outcomes(args.repository)
        print(json.dumps(result, indent=2))
        return 1 if args.agent_models_command == "recommend" and result["selection"] is None else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

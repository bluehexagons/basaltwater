"""Private recognition artifacts and frozen-run held-out evaluation."""

from __future__ import annotations

import hashlib
import json
import time

from desktop import learning_records as contract, perception
from desktop.qa_artifacts import new_bundle
from desktop.record_bundles import _read, read_packet
from lib.atomic_io import parse_json, write_bytes_atomic, write_json_atomic


def _check_budget(folder, additional):
    # Only our bounded, exclusively created files live in this private bundle.
    used = sum(path.stat().st_size for path in folder.iterdir())
    if used + additional > 128 * 1024 * 1024:
        raise ValueError("Perception bundle exceeds 128 MiB")


def _assets(folder, profile_bytes, templates):
    _check_budget(folder, len(profile_bytes) + sum(map(len, templates.values())))
    write_bytes_atomic(str(folder / "profile.json"), profile_bytes)
    pins = {}
    for index, (key, data) in enumerate(sorted(templates.items())):
        name = f"template-{index}.png"
        write_bytes_atomic(str(folder / name), data)
        pins[key] = {"artifact": name, "sha256": hashlib.sha256(data).hexdigest()}
    return pins


def _sample(folder, index, packet, frame, result, crops):
    prefix = f"sample-{index}"
    packet_bytes = (json.dumps({"schema_version": 1, "records": [packet]}, indent=2, allow_nan=False) + "\n").encode()
    _check_budget(folder, len(packet_bytes) + len(frame) + sum(map(len, crops.values())))
    write_bytes_atomic(str(folder / f"{prefix}-packet.json"), packet_bytes)
    write_bytes_atomic(str(folder / f"{prefix}-frame.png"), frame)
    result["frame_artifact"] = f"{prefix}-frame.png"
    result["packet_artifact"] = f"{prefix}-packet.json"
    result["crops"] = {}
    for number, (name, data) in enumerate(crops.items()):
        filename = f"{prefix}-crop-{number}.png"
        write_bytes_atomic(str(folder / filename), data)
        result["crops"][name] = {"artifact": filename, "sha256": hashlib.sha256(data).hexdigest()}


def recognize(artifact, packet_id, frame_id, profile_source, paths, output=None):
    profile_bytes, profile, templates = perception.load_profile(profile_source, paths)
    packet, frame = read_packet(artifact, packet_id, frame_id)
    result, crops = perception.recognize(packet, frame_id, frame, profile, templates)
    folder = new_bundle(output)
    try:
        result["templates"] = _assets(folder, profile_bytes, templates)
        result["profile_sha256"] = hashlib.sha256(profile_bytes).hexdigest()
        _sample(folder, 0, packet, frame, result, crops)
        _check_budget(folder, len(json.dumps(result, indent=2).encode()) + 1)
        write_json_atomic(str(folder / "recognition.json"), result)
    except (OSError, ValueError, RuntimeError) as exc:
        _partial(exc, folder)
    return {"artifact": str(folder / "recognition.json"), "drift": result["drift"],
            "fields": result["fields"], "partial": False, "input_authority": False}


def _partial(exc, folder):
    error = ValueError(str(exc))
    error.receipt = {"error": str(exc), "partial": True, "bundle": str(folder), "input_authority": False}
    raise error from exc


def evaluate(dataset_source, bundles, profile_source, paths, output=None):
    dataset_bytes = _read(dataset_source, 65536)
    dataset = parse_json(dataset_bytes)
    contract.bounded(dataset)
    contract.keys(dataset, "schema_version kind id profile_sha256 calibration_runs heldout_runs samples")
    if type(dataset["schema_version"]) is not int or dataset["schema_version"] != 1 or dataset["kind"] != "region-heldout":
        raise ValueError("Unsupported held-out dataset/version")
    contract.text(dataset["id"])
    contract.digest(dataset["profile_sha256"])
    profile_bytes, profile, templates = perception.load_profile(profile_source, paths)
    if hashlib.sha256(profile_bytes).hexdigest() != dataset["profile_sha256"]:
        raise ValueError("Frozen profile hash changed")
    runs = []
    for name in ("calibration_runs", "heldout_runs"):
        values = contract.sequence(dataset[name], 32)
        for value in values:
            contract.text(value)
        if len(set(values)) != len(values):
            raise ValueError("Duplicate dataset run ID")
        runs.append(set(values))
    if not runs[1] or runs[0] & runs[1] or set(bundles) != runs[1]:
        raise ValueError("Held-out runs must be nonempty, disjoint from calibration and exactly bound")
    fields = {field["name"] for field in profile["fields"]}
    samples = contract.sequence(dataset["samples"], 32)
    if not samples:
        raise ValueError("Held-out dataset is empty")
    ids, frames, loaded, total = set(), set(), [], 0
    for sample in samples:
        contract.keys(sample, "id run_id packet_id frame_id case expected")
        for name in ("id", "run_id", "packet_id", "frame_id", "case"):
            contract.text(sample[name])
        contract.keys(sample["expected"], " ".join(sorted(fields)))
        for value in sample["expected"].values():
            if value is not None:
                contract.text(value)
        if sample["id"] in ids or sample["run_id"] not in runs[1]:
            raise ValueError("Duplicate sample or sample outside frozen held-out runs")
        ids.add(sample["id"])
        packet, frame = read_packet(bundles[sample["run_id"]], sample["packet_id"], sample["frame_id"])
        if packet["run_id"] != sample["run_id"]:
            raise ValueError("Sample run differs from packet run")
        digest = hashlib.sha256(frame).hexdigest()
        if digest in frames:
            raise ValueError("Duplicate held-out frame bytes would inflate evaluation")
        frames.add(digest)
        total += len(frame)
        if total > 32 * 1024 * 1024:
            raise ValueError("Held-out frames exceed 32 MiB")
        loaded.append((sample, packet, frame))
    if {sample["run_id"] for sample in samples} != runs[1]:
        raise ValueError("Every held-out run needs a sample")
    folder = new_bundle(output)
    try:
        write_bytes_atomic(str(folder / "dataset.json"), dataset_bytes)
        template_pins = _assets(folder, profile_bytes, templates)
        results, metrics = [], {}
        started = time.perf_counter()
        for index, (sample, packet, frame) in enumerate(loaded):
            result, crops = perception.recognize(packet, sample["frame_id"], frame, profile, templates)
            _sample(folder, index, packet, frame, result, crops)
            results.append({"sample": sample, "recognition": result})
            for field in result["fields"]:
                metric = metrics.setdefault(field["name"], {"samples": 0, "false_confident": 0, "abstentions": 0,
                                                          "correct": 0, "latencies_seconds": [], "cases": {}})
                expected = sample["expected"][field["name"]]
                wrong = field["value"] is not None and field["value"] != expected
                abstained = field["value"] is None
                correct = field["value"] == expected
                metric["samples"] += 1
                metric["false_confident"] += int(wrong)
                metric["abstentions"] += int(abstained)
                metric["correct"] += int(correct)
                metric["latencies_seconds"].append(result["elapsed_seconds"])
                case = metric["cases"].setdefault(sample["case"], {"samples": 0, "false_confident": 0, "abstentions": 0})
                case["samples"] += 1
                case["false_confident"] += int(wrong)
                case["abstentions"] += int(abstained)
        report = {"schema_version": 1, "kind": "region-heldout-report", "dataset_id": dataset["id"],
                  "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                  "profile_sha256": dataset["profile_sha256"], "templates": template_pins, "results": results,
                  "metrics": metrics, "elapsed_seconds": time.perf_counter() - started,
                  "qualification": "offline-heldout-only", "game_qualification": "not-asserted", "input_authority": False}
        # Full settings occur per sample; retain a hard report/storage bound.
        if len(json.dumps(report, indent=2).encode()) > 8 * 1024 * 1024:
            raise ValueError("Held-out report exceeds 8 MiB")
        _check_budget(folder, len(json.dumps(report, indent=2).encode()) + 1)
        write_json_atomic(str(folder / "report.json"), report)
    except (OSError, ValueError, RuntimeError) as exc:
        _partial(exc, folder)
    return {"artifact": str(folder / "report.json"), "samples": len(results), "metrics": metrics,
            "partial": False, "input_authority": False, "game_qualification": "not-asserted"}

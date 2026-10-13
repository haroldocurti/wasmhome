#!/usr/bin/env python3
"""Fail-closed offline consistency checks for a captured E1 device session."""

import argparse
import hashlib
import json
import pathlib
import re
import sys

from collect_results import FIELDS, PREFIX
from evidence_json import loads

MEMORY_PREFIX = "WASMHOME_MEMORY,"
EVENT_PREFIX = "WASMHOME_EVENT,"
SCENARIOS = ("native", "interp", "aot")
EXPECTED_CHECKSUM = "0xDBB2F0D4"
HEX_256 = re.compile(r"^[0-9a-f]{64}$")
BOOT_ID = re.compile(r"^[0-9a-fA-F]{16}$")
WARMUP_LINE = re.compile(
    r"E1 preliminary calls: scenario=(native|interp|aot) warmup=(\d+) measured=(\d+); "
    r"one load, not independent updates"
)
FINAL_LINE = re.compile(
    r"E1 scenario=(native|interp|aot) status=(ok|invalid) "
    r"heap_before_runtime=(\d+) heap_after_release=(\d+)"
)
MEMORY_PHASE_ORDER = (
    "before_runtime", "after_init", "after_load", "after_instantiate",
    "after_exec_env_lookup", "after_warmup", "before_execution", "execution",
    "after_release",
)
REPEATED_MEMORY_PHASES = {"before_execution", "execution"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_file(root, value, label):
    _require(isinstance(value, str) and value, f"{label}: missing relative path")
    relative = pathlib.Path(value)
    _require(not relative.is_absolute() and ".." not in relative.parts,
             f"{label}: path must stay inside the session directory")
    resolved_root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label}: path escapes the session directory") from exc
    _require(path.is_file(), f"{label}: file is missing: {value}")
    return path


def _check_sha(value, label):
    _require(isinstance(value, str) and HEX_256.fullmatch(value) is not None,
             f"{label}: expected lowercase SHA-256")


def _event_json(line, marker, label):
    position = line.find(marker)
    if position < 0:
        return None
    _require(line.find(marker, position + len(marker)) < 0,
             f"{label}: repeated marker on one serial line")
    try:
        value = loads(line[position + len(marker):].strip())
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid JSON event: {exc}") from exc
    _require(isinstance(value, dict), f"{label}: JSON event must be an object")
    return value


def _check_heap_dimension(value, label, *, nullable):
    fields = ("total_bytes", "free_bytes", "largest_free_block_bytes")
    _require(isinstance(value, dict), f"{label}: expected an object")
    if nullable:
        _require(value.get("status") == "not_applicable",
                 f"{label}: absent capability must be not_applicable")
        _require(all(value.get(field) is None for field in fields),
                 f"{label}: absent capability values must be null")
        _require(value.get("phase_peak_status") == "not_applicable"
                 and value.get("phase_peak_free_bytes") is None,
                 f"{label}: absent phase peak must be null/not_applicable")
        return
    _require(value.get("status") == "measured", f"{label}: missing measured status")
    sizes = [value.get(field) for field in fields]
    _require(all(type(size) is int and size >= 0 for size in sizes),
             f"{label}: byte values must be nonnegative integers")
    total, free, largest = sizes
    _require(total > 0 and free <= total and largest <= free,
             f"{label}: inconsistent total/free/largest-free sizes")
    _require(value.get("phase_peak_status") == "not_instrumented"
             and value.get("phase_peak_free_bytes") is None,
             f"{label}: point-in-time sample cannot be represented as a phase peak")


def _check_memory_event(event, scenario, boot_id, line_number):
    prefix = f"{scenario} memory line {line_number}"
    _require(event.get("schema") == "wasmhome-memory-v1", f"{prefix}: unknown schema")
    _require(event.get("scenario") == scenario, f"{prefix}: scenario mismatch")
    _require(event.get("boot_id") == boot_id, f"{prefix}: boot_id mismatch")
    _require(event.get("units") == "bytes" and event.get("scope") == "dynamic_heap",
             f"{prefix}: memory unit/scope is missing")
    internal = event.get("internal")
    _require(isinstance(internal, dict) and internal.get("capability") == "MALLOC_CAP_INTERNAL",
             f"{prefix}: internal heap capability is missing")
    _check_heap_dimension(internal, f"{prefix} internal", nullable=False)

    psram = event.get("psram")
    _require(isinstance(psram, dict) and psram.get("capability") == "MALLOC_CAP_SPIRAM",
             f"{prefix}: PSRAM capability is missing")
    _check_heap_dimension(psram, f"{prefix} PSRAM",
                          nullable=psram.get("status") == "not_applicable")
    static_memory = event.get("static_memory")
    _require(static_memory == {"status": "external_elf", "source": "ELF", "bytes": None},
             f"{prefix}: static memory must remain an ELF-derived value")


def _validate_memory_events(events, scenario, boot_id):
    expected_na = {"after_init", "after_load", "after_instantiate", "after_exec_env_lookup"} if scenario == "native" else set()
    by_phase = {}
    repeated = {phase: [] for phase in REPEATED_MEMORY_PHASES}
    for event, line_number in events:
        phase = event.get("phase")
        _require(phase in MEMORY_PHASE_ORDER, f"{scenario}: unknown memory phase {phase!r}")
        _check_memory_event(event, scenario, boot_id, line_number)
        status = event.get("phase_status")
        if phase in REPEATED_MEMORY_PHASES:
            repeated[phase].append((event, line_number))
            expected_status = "measured" if phase == "before_execution" else "ok"
            _require(status == expected_status,
                     f"{scenario}: {phase} memory status must be {expected_status}")
            repetition = event.get("repetition")
            _require(type(repetition) is int and 1 <= repetition <= 30,
                     f"{scenario}: {phase} repetition must be 1..30")
        else:
            _require(event.get("repetition") is None,
                     f"{scenario}: non-execution phase cannot carry a repetition")
            _require(phase not in by_phase, f"{scenario}: duplicate memory phase {phase}")
            by_phase[phase] = (event, line_number)
            expected_status = "not_applicable" if phase in expected_na else ("measured" if phase == "before_runtime" else "ok")
            _require(status == expected_status,
                     f"{scenario}: {phase} status must be {expected_status}")

    required = set(MEMORY_PHASE_ORDER) - REPEATED_MEMORY_PHASES
    _require(set(by_phase) == required,
             f"{scenario}: missing memory phases {sorted(required - set(by_phase))}")
    for phase in REPEATED_MEMORY_PHASES:
        _require([event["repetition"] for event, _ in repeated[phase]] == list(range(1, 31)),
                 f"{scenario}: {phase} memory samples must be repetitions 1..30")

    phase_positions = [by_phase[phase][1] for phase in (
        "before_runtime", "after_init", "after_load", "after_instantiate",
        "after_exec_env_lookup", "after_warmup",
    )]
    before_positions = [line_number for _, line_number in repeated["before_execution"]]
    execution_positions = [line_number for _, line_number in repeated["execution"]]
    _require(phase_positions == sorted(phase_positions),
             f"{scenario}: initialization memory phases are out of order")
    _require(before_positions == sorted(before_positions) and execution_positions == sorted(execution_positions),
             f"{scenario}: execution memory samples are out of order")
    _require(before_positions[0] > by_phase["after_warmup"][1],
             f"{scenario}: execution memory sample preceded warmup")
    _require(all(before < after for before, after in zip(before_positions, execution_positions)),
             f"{scenario}: before_execution sample must precede its execution sample")
    _require(execution_positions == sorted(execution_positions),
             f"{scenario}: execution memory samples are out of order")
    _require(by_phase["after_release"][1] > execution_positions[-1],
             f"{scenario}: release memory event preceded execution")


def _validate_log(path, scenario, run, expected_checksum):
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    boot_events = []
    memory_events = []
    warmup_messages = []
    final_messages = []
    csv_header_lines = []
    rows = []

    for number, line in enumerate(lines, start=1):
        event = _event_json(line, EVENT_PREFIX, f"{scenario} serial line {number}")
        if event is not None and event.get("event") == "boot_observed":
            boot_events.append((event, number))
        memory = _event_json(line, MEMORY_PREFIX, f"{scenario} serial line {number}")
        if memory is not None:
            memory_events.append((memory, number))

        match = WARMUP_LINE.search(line)
        if match:
            warmup_messages.append((match.groups(), number))
        match = FINAL_LINE.search(line)
        if match:
            final_messages.append((match.groups(), number))

        marker = line.find(PREFIX)
        if marker >= 0:
            try:
                import csv
                values = next(csv.reader([line[marker + len(PREFIX):].strip()]))
            except (csv.Error, StopIteration) as exc:
                raise ValueError(f"{scenario} serial line {number}: invalid CSV: {exc}") from exc
            if values == FIELDS:
                csv_header_lines.append(number)
            else:
                if len(values) != len(FIELDS):
                    raise ValueError(f"{scenario}: CSV line {number} has {len(values)} fields")
                rows.append((dict(zip(FIELDS, values)), number))

    _require(len(boot_events) == 1, f"{scenario}: expected exactly one boot_observed event")
    boot, boot_line = boot_events[0]
    _require(boot.get("scenario") == scenario, f"{scenario}: boot scenario mismatch")
    observed_boot = boot.get("boot_id")
    _require(isinstance(observed_boot, str) and BOOT_ID.fullmatch(observed_boot) is not None,
             f"{scenario}: invalid boot_id in serial event")
    _require(observed_boot.lower() == run["boot_id"].lower(),
             f"{scenario}: boot_id differs from session manifest")

    _require(len(warmup_messages) == 1, f"{scenario}: expected one warmup/count marker")
    warmup, warmup_line = warmup_messages[0]
    _require(warmup == (scenario, "5", "30"), f"{scenario}: expected five checked warmups and 30 calls")
    _require(len(csv_header_lines) == 1, f"{scenario}: expected one CSV header")
    _require(warmup_line < csv_header_lines[0], f"{scenario}: warmups must precede the CSV measurements")
    _require(len(rows) == 30, f"{scenario}: expected exactly 30 measured CSV rows, got {len(rows)}")
    _require(csv_header_lines[0] < rows[0][1], f"{scenario}: CSV header must precede measurements")
    _require([row["repetition"] for row, _ in rows] == [str(n) for n in range(1, 31)],
             f"{scenario}: CSV repetitions must be ordered 1..30")
    _require(all(row["scenario"] == scenario for row, _ in rows), f"{scenario}: CSV scenario mismatch")
    _require(all(row["status"] == "ok" for row, _ in rows), f"{scenario}: CSV contains invalid runs")
    _require(all(row["checksum"].upper() == expected_checksum.upper() for row, _ in rows),
             f"{scenario}: CSV checksum mismatch")
    parsed_rows = []
    for row, number in rows:
        numeric = {}
        for field in ("load_us", "instantiate_us", "execute_us", "heap_before",
                      "heap_after_load", "heap_after_instantiate", "heap_min"):
            try:
                value = int(row[field], 10)
            except ValueError as exc:
                raise ValueError(f"{scenario} CSV line {number}: {field} is not an integer") from exc
            _require(value >= 0, f"{scenario} CSV line {number}: {field} is negative")
            numeric[field] = value
        parsed_rows.append((numeric, number))
    _require(len({(row["load_us"], row["instantiate_us"]) for row, _ in rows}) == 1,
             f"{scenario}: load/instantiate must be a single runner instance")

    _require(len(final_messages) == 1, f"{scenario}: expected one final E1 status")
    final, final_line = final_messages[0]
    _require(final[0] == scenario and final[1] == "ok", f"{scenario}: final E1 status is not ok")
    _require(final_line > rows[-1][1], f"{scenario}: final E1 status preceded the last measurement")

    _validate_memory_events(memory_events, scenario, observed_boot.lower())
    after_release_line = next(line_number for event, line_number in memory_events
                              if event.get("phase") == "after_release")
    _require(final_line > after_release_line,
             f"{scenario}: final E1 status preceded after_release")
    internal_samples = [event["internal"] for event, _ in memory_events]
    internal_totals = {sample["total_bytes"] for sample in internal_samples}
    _require(len(internal_totals) == 1,
             f"{scenario}: internal heap capacity changed within one boot/session")
    internal_total = next(iter(internal_totals))
    observed_free_minimum = min(sample["free_bytes"] for sample in internal_samples)
    for numeric, number in parsed_rows:
        for field in ("heap_before", "heap_after_load", "heap_after_instantiate", "heap_min"):
            _require(numeric[field] <= internal_total,
                     f"{scenario} CSV line {number}: {field} exceeds internal heap capacity")
        _require(numeric["heap_min"] <= observed_free_minimum,
                 f"{scenario} CSV line {number}: heap_min exceeds observed free heap")
    _require([numeric["heap_min"] for numeric, _ in parsed_rows]
             == sorted((numeric["heap_min"] for numeric, _ in parsed_rows), reverse=True),
             f"{scenario}: heap_min must be nonincreasing across measured repetitions")

    before_events = [(event, number) for event, number in memory_events
                     if event.get("phase") == "before_execution"]
    execution_events = [(event, number) for event, number in memory_events
                        if event.get("phase") == "execution"]
    for repetition, ((_, before_line), (_, execution_line), (_, csv_line)) in enumerate(
        zip(before_events, execution_events, rows), start=1
    ):
        _require(before_line < execution_line < csv_line,
                 f"{scenario}: memory samples for repetition {repetition} are out of order")
    _require(memory_events[0][1] > boot_line, f"{scenario}: memory events preceded boot identity")
    _require(memory_events[0][0].get("phase") == "before_runtime",
             f"{scenario}: first memory event must be before_runtime")
    _require(memory_events[-1][0].get("phase") == "after_release",
             f"{scenario}: last memory event must be after_release")
    _require(csv_header_lines[0] > next(line for event, line in memory_events
                                        if event.get("phase") == "after_warmup"),
             f"{scenario}: warmup memory event must precede CSV header")

    return {
        "scenario": scenario,
        "boot_id": observed_boot.lower(),
        "elf_sha256": run["elf_sha256"],
        "bin_sha256": run["flashed_bin_sha256"],
        "log_sha256": hashlib.sha256(raw).hexdigest(),
        "checked_warmups": 5,
        "measured_calls": len(rows),
        "checksum": expected_checksum,
        "memory_events": len(memory_events),
    }


def validate_session(manifest_path):
    manifest_path = pathlib.Path(manifest_path).resolve()
    manifest = loads(manifest_path.read_text(encoding="utf-8-sig"))
    _require(isinstance(manifest, dict), "session manifest must be a JSON object")
    _require(manifest.get("schema") == "wasmhome-e1-device-session-v1",
             "unsupported E1 device session schema")
    _require(manifest.get("scenario_order") == list(SCENARIOS),
             "scenario_order must be native, interp, aot")
    checksum = manifest.get("expected_checksum")
    _require(checksum == EXPECTED_CHECKSUM, f"expected_checksum must be {EXPECTED_CHECKSUM}")
    artifacts = manifest.get("artifacts")
    runs = manifest.get("runs")
    _require(isinstance(artifacts, dict) and set(artifacts) == set(SCENARIOS),
             "manifest must contain native, interp and aot artifacts")
    _require(isinstance(runs, dict) and set(runs) == set(SCENARIOS),
             "manifest must contain native, interp and aot runs")

    checked = []
    seen_boots = set()
    for scenario in SCENARIOS:
        artifact = artifacts[scenario]
        run = runs[scenario]
        _require(isinstance(artifact, dict) and isinstance(run, dict),
                 f"{scenario}: artifact/run must be objects")
        for name in ("elf_sha256", "bin_sha256"):
            _check_sha(artifact.get(name), f"{scenario} artifact {name}")
        elf = _relative_file(manifest_path.parent, artifact.get("elf_path"), f"{scenario} ELF")
        binary = _relative_file(manifest_path.parent, artifact.get("bin_path"), f"{scenario} BIN")
        _require(_sha256(elf) == artifact["elf_sha256"], f"{scenario}: ELF hash differs from manifest")
        _require(_sha256(binary) == artifact["bin_sha256"], f"{scenario}: BIN hash differs from manifest")

        _require(run.get("flash_verified") is True, f"{scenario}: flash verification is not recorded")
        _check_sha(run.get("elf_sha256"), f"{scenario} run ELF SHA-256")
        _check_sha(run.get("flashed_bin_sha256"), f"{scenario} run BIN SHA-256")
        _require(run["elf_sha256"] == artifact["elf_sha256"], f"{scenario}: ELF identity differs from manifest")
        _require(run["flashed_bin_sha256"] == artifact["bin_sha256"], f"{scenario}: flashed BIN identity differs from manifest")
        boot_id = run.get("boot_id")
        _require(isinstance(boot_id, str) and BOOT_ID.fullmatch(boot_id) is not None,
                 f"{scenario}: invalid manifest boot_id")
        normalized_boot = boot_id.lower()
        _require(normalized_boot not in seen_boots, f"duplicate boot_id across scenarios: {normalized_boot}")
        seen_boots.add(normalized_boot)

        log_sha = run.get("log_sha256")
        _check_sha(log_sha, f"{scenario} serial log SHA-256")
        log_path = _relative_file(manifest_path.parent, run.get("log_path"), f"{scenario} serial log")
        _require(_sha256(log_path) == log_sha, f"{scenario}: serial log hash differs from manifest")
        result = _validate_log(log_path, scenario, run, checksum)
        checked.append(result)

    return {
        "schema": "wasmhome-e1-device-validation-v1",
        "status": "session_logs_and_manifest_consistent",
        "scope": "offline_consistency_only_not_independent_flash_or_hardware_attestation",
        "scenario_order": list(SCENARIOS),
        "expected_checksum": checksum,
        "scenarios": checked,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_manifest", type=pathlib.Path)
    args = parser.parse_args()
    result = validate_session(args.session_manifest)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

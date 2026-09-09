#!/usr/bin/env python3
import argparse
import csv
import pathlib
import sys

PREFIX = "WASMHOME_CSV,"
FIELDS = [
    "scenario", "repetition", "load_us", "instantiate_us", "execute_us",
    "heap_before", "heap_after_load", "heap_after_instantiate", "heap_min",
    "checksum", "status",
]


def parse_log(lines):
    rows = []
    for line in lines:
        marker = line.find(PREFIX)
        if marker < 0:
            continue
        values = next(csv.reader([line[marker + len(PREFIX):].strip()]))
        if values == FIELDS:
            continue
        if len(values) != len(FIELDS):
            raise ValueError(f"invalid result row with {len(values)} fields: {line.strip()}")
        rows.append(dict(zip(FIELDS, values)))
    return rows


def validate(rows):
    scenarios = {row["scenario"] for row in rows}
    unknown = scenarios - {"native", "interp", "aot"}
    if unknown:
        raise ValueError(f"unknown scenarios: {sorted(unknown)}")
    for scenario in scenarios:
        scenario_rows = [row for row in rows if row["scenario"] == scenario]
        repetitions = sorted(int(row["repetition"]) for row in scenario_rows)
        if repetitions != list(range(1, 31)):
            raise ValueError(f"{scenario}: expected repetitions 1..30, got {repetitions}")
        invalid = [row for row in scenario_rows if row["status"] != "ok"]
        if invalid:
            raise ValueError(f"{scenario}: {len(invalid)} invalid runs")


def main():
    parser = argparse.ArgumentParser(description="Extract WasmHome CSV rows from ESP-IDF serial logs.")
    parser.add_argument("logs", nargs="+", type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    rows = []
    for path in args.logs:
        rows.extend(parse_log(path.read_text(encoding="utf-8", errors="replace").splitlines()))
    validate(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

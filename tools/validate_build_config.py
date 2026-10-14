#!/usr/bin/env python3
"""Check E1 effective build evidence without exporting secret sdkconfig values."""
import argparse
import hashlib
import json
import pathlib
import re
import shlex
import subprocess

from embed_artifact import render
from evidence_json import read
from toolchain_provenance import validate_manifest, idf_identity

RUNTIME_COMPONENT = "wasmhome/wasm-micro-runtime"
WAMR_REVISION = "25bd7eb63e828e4bd242cc9b38d260b4b31c6605"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_o3(flags, label, expected="-O3"):
    require(isinstance(flags, list) and all(isinstance(flag, str) for flag in flags), f"missing {label} flags")
    require(not any(flag.startswith("@") for flag in flags), f"{label} response-file optimization cannot be verified")
    optimization = [flag for flag in flags if re.fullmatch(r"-O(?:[0-3sgz]|fast)?|--opt-level=.*", flag)]
    require(expected in optimization and optimization[-1] == expected,
            f"{label} final optimization must be {expected}")


def validate_native_workload(commands, root):
    source = (root / "workload/benchmark.c").resolve()
    matches = [entry for entry in commands if pathlib.Path(entry["file"]).resolve() == source]
    require(len(matches) == 1, "native workload compile command missing/ambiguous")
    flags = [token.strip('"') for token in shlex.split(matches[0]["command"], posix=False)]
    validate_o3(flags, "native workload")


def scalar(text, key):
    values = re.findall(rf"(?m)^\s*{re.escape(key)}:\s*['\"]?([^'\"\r\n]+?)['\"]?\s*$", text)
    require(len(values) == 1, f"missing/ambiguous field: {key}")
    return values[0]


def resolved_runtime(root):
    from runtime_package import verify
    return verify(root)


def validate_toolchain(manifest, check_tools=True):
    require(manifest.get("wamrc_source_revision") == WAMR_REVISION, "wamrc source must match pinned upstream Git SHA")
    require(re.fullmatch(r"wamrc\s+2\.4\.5", manifest.get("wamrc_version", "")), "wamrc version differs from runtime candidate")
    for name in ("wamrc", "clang"):
        require(re.fullmatch(r"[0-9a-f]{64}", manifest.get(f"{name}_sha256", "")), f"invalid {name} executable hash")
        require(bool(manifest.get(f"{name}_path")) and bool(manifest.get(f"{name}_version")), f"missing {name} executable identity")
        if check_tools:
            tool = pathlib.Path(manifest[f"{name}_path"])
            require(tool.is_file() and sha256(tool) == manifest[f"{name}_sha256"], f"{name} executable differs from manifest")
            result = subprocess.run([str(tool), "--version"], check=True, capture_output=True, text=True)
            require((result.stdout + result.stderr).strip() == manifest[f"{name}_version"], f"{name} version differs from manifest")


def validate_artifacts(directory, root, check_tools=True, runtime_identity=None):
    manifest = read(directory / "build-manifest.json")
    require(manifest.get("schema") == "wasmhome-e1-artifacts-v3", "missing artifact manifest schema")
    require(manifest.get("source_sha256") == sha256(root / "workload/benchmark.c"), "workload source changed")
    require(manifest.get("header_sha256") == sha256(root / "workload/benchmark.h"), "workload header changed")
    identity = runtime_identity or resolved_runtime(root)[0]
    require(manifest.get("runtime_identity") == identity, "artifact runtime identity differs from resolved component")
    require(identity["component"] == RUNTIME_COMPONENT and identity["version"] == "2.4.5-wasmhome.3" and identity["repository_revision"] == WAMR_REVISION and identity["aot_version"] == 5, "unexpected runtime candidate")
    require(manifest.get("aot_format_version") == 5, "manifest AOT format must be version 5")
    validate_manifest(manifest, root)
    validate_toolchain(manifest, check_tools)
    require(manifest.get("expected_checksum") == "0xDBB2F0D4", "unexpected checksum contract")
    clang_flags = manifest.get("clang_flags")
    validate_o3(clang_flags, "WASI clang")
    require("--target=wasm32-wasi" in clang_flags and all(flag == "--target=wasm32-wasi" for flag in clang_flags if flag.startswith("--target=")), "clang target must remain wasm32-wasi")
    validate_o3(manifest.get("aot_flags"), "AOT compiler", "--opt-level=3")
    require("--target=xtensa" in manifest.get("aot_flags", []), "AOT target must be Xtensa")
    require("--bounds-checks=1" in manifest.get("aot_flags", []), "AOT bounds checks must be explicit")
    aot_flags = manifest["aot_flags"]
    require("--disable-llvm-lto" in aot_flags, "AOT compiler must explicitly disable LLVM LTO")
    require(all(flag == "--target=xtensa" for flag in aot_flags if flag.startswith("--target=")), "conflicting AOT target flags")
    require(all(flag.endswith("=1") for flag in aot_flags if flag.startswith(("--bounds-checks=", "--stack-bounds-checks=")))
            and "--disable-aux-stack-check" not in aot_flags, "AOT flags disable bounds checks")
    require("-flto" not in manifest.get("clang_flags", []), "LTO needs separate final workload verification")
    names = ("benchmark.wasm", "benchmark.aot", "benchmark.wasm.s", "benchmark_wasm_embedded.c", "benchmark_aot_embedded.c")
    for name in names:
        path = directory / name
        record = manifest.get("files", {}).get(name, {})
        require(path.is_file() and path.stat().st_size > 0, f"missing/nonempty artifact: {name}")
        require(record.get("sha256") == sha256(path) and record.get("bytes") == path.stat().st_size, f"artifact hash/size mismatch: {name}")
    require((directory / "benchmark.wasm").read_bytes()[:8] == b"\0asm\x01\0\0\0", "invalid Wasm header")
    require((directory / "benchmark.aot").read_bytes()[:8] == b"\0aot\x05\0\0\0", "AOT format must be version 5 for WAMR 2.4.5")
    for kind in ("wasm", "aot"):
        expected = render((directory / f"benchmark.{kind}").read_bytes(), f"benchmark_{kind}")
        require((directory / f"benchmark_{kind}_embedded.c").read_text(encoding="ascii") == expected, f"embedded {kind} differs from payload")
    require(re.search(r"(?m)^\s*loop\b", (directory / "benchmark.wasm.s").read_text()), "Wasm assembly has no loop")
    return manifest


def validate_runtime_commands(commands, scenario, directory, checked):
    runtime_commands = [x for x in commands if "wasm-micro-runtime" in x["file"]]
    require(bool(runtime_commands), "runtime compile commands missing")
    require(any(pathlib.Path(x["file"]).name == "wasm_runtime_common.c" for x in runtime_commands), "runtime common compile command missing")
    expected = {"WASM_ENABLE_INTERP": int(scenario == "interp"), "WASM_ENABLE_AOT": int(scenario == "aot"), "WASM_ENABLE_FAST_INTERP": 0,
                "WASM_ENABLE_AOT_VALIDATOR": 1, "WASM_CONFIGURABLE_BOUNDS_CHECKS": 0}
    for entry in runtime_commands:
        source = pathlib.Path(entry["file"]).resolve()
        require(source.is_relative_to(directory) and source in checked, "runtime compile source differs from resolved component")
        for macro, value in expected.items():
            values = re.findall(rf"-D{macro}=(\d+)\b", entry["command"])
            # WAMR's ESP-IDF component omits false feature defines instead of
            # emitting `-DMACRO=0`; an undefined macro evaluates to zero in
            # its `#if MACRO != 0` feature guards. Accept that default only for
            # features expected to be disabled, while still rejecting any
            # conflicting or nonzero explicit definition.
            if value == 0:
                require(not values or set(values) == {"0"}, f"effective runtime macro differs: {macro}")
            else:
                require(set(values) == {str(value)}, f"effective runtime macro differs: {macro}")
    if scenario == "aot":
        require(any(pathlib.Path(x["file"]).name == "aot_validator.c" for x in runtime_commands), "AOT validator source missing from build")


def validate_config(config, scenario):
    require(config.get("IDF_TARGET") == "esp32s3", "target must be esp32s3")
    require(config.get("WASMHOME_RUN_BENCHMARK") is True, "benchmark disabled; diagnostics are not E1")
    require(not config.get("WASMHOME_PLATFORM_SELFTEST") and not config.get("WASMHOME_OBSERVER_SELFTEST"), "selftest is not E1")
    require(config.get("ESP_DEFAULT_CPU_FREQ_MHZ") == 240, "E1 clock must be 240 MHz")
    require(config.get("COMPILER_OPTIMIZATION_PERF") is True, "expected performance optimization")
    if scenario == "interp":
        require(config.get("WAMR_ENABLE_INTERP") is True and config.get("WAMR_INTERP_CLASSIC") is True, "B requires classic interpreter")
        require(not config.get("WAMR_INTERP_FAST") and not config.get("WAMR_ENABLE_AOT"), "B must exclude fast interpreter and AOT")
    if scenario == "aot":
        require(config.get("WAMR_ENABLE_AOT") is True and not config.get("WAMR_ENABLE_INTERP"), "C requires AOT without interpreter")


def validate_symbols(symbols, scenario):
    if scenario == "native":
        require("benchmark_run" in symbols, "native workload missing from linked ELF")
        require(not any(re.match(r"(?:wasm_runtime_|wasm_interp_|aot_)", x) for x in symbols), "native ELF links WAMR execution symbols")
    else:
        require("wasm_runtime_load" in symbols and "wasm_runtime_call_wasm" in symbols, "WAMR execution missing from linked ELF")
        require(f"benchmark_{'wasm' if scenario == 'interp' else 'aot'}" in symbols, "embedded payload missing from linked ELF")
        if scenario == "interp":
            require(not any(x.startswith("aot_") for x in symbols), "B ELF contains AOT implementation")
        else:
            require(not any(x.startswith("wasm_interp_") for x in symbols), "C ELF contains interpreter implementation")
            require("aot_module_validate" in symbols, "C ELF omits AOT validator")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=pathlib.Path)
    parser.add_argument("--scenario", choices=("native", "interp", "aot"))
    parser.add_argument("--nm", type=pathlib.Path)
    parser.add_argument("--runtime-identity", action="store_true", help="print checked resolved runtime identity for the artifact manifest")
    parser.add_argument("--artifact-integrity", action="store_true", help="CMake preflight: check canonical payloads without requiring original compiler executables")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    try:
        root = pathlib.Path(__file__).resolve().parents[1]
        if args.runtime_identity:
            print(json.dumps(resolved_runtime(root)[0]))
            return
        if args.artifact_integrity:
            validate_artifacts(root / "artifacts", root, check_tools=False)
            print("Artifact manifest, runtime identity and payload hashes checked; unsigned compiler receipt remains subject to independent rebuild")
            return
        require(args.build_dir is not None and args.scenario is not None and args.nm is not None, "build-dir, scenario and nm are required for build validation")
        build = args.build_dir.resolve()
        config_path = build / "config/sdkconfig.json"
        config = json.loads(config_path.read_text())
        validate_config(config, args.scenario)
        cache = (build / "CMakeCache.txt").read_text()
        require(re.search(rf"(?m)^EXPERIMENT_SCENARIO:[^=]+={args.scenario}$", cache), "scenario differs in CMake cache")
        commands = json.loads((build / "compile_commands.json").read_text())
        command = next(x["command"] for x in commands if pathlib.Path(x["file"]).name == "main.c")
        require(f"WASMHOME_SCENARIO_{args.scenario.upper()}=1" in command, "main compile scenario differs")
        identity = None
        if args.scenario != "native":
            identity, runtime_directory, checked = resolved_runtime(root)
        if args.scenario == "native":
            validate_native_workload(commands, root)
        if args.scenario != "native":
            validate_runtime_commands(commands, args.scenario, runtime_directory, checked)
            validate_artifacts(root / "artifacts", root, runtime_identity=identity)
        import os
        require(bool(os.environ.get("IDF_PATH")), "IDF_PATH required for pinned build identity")
        actual_idf = idf_identity(pathlib.Path(os.environ["IDF_PATH"]))
        description = json.loads((build / "project_description.json").read_text())
        require(pathlib.Path(description["build_dir"]).resolve() == build, "build description belongs to another directory")
        elf = build / description["app_elf"]
        listing = subprocess.run([str(args.nm), "--defined-only", str(elf)], check=True, capture_output=True, text=True).stdout
        symbols = {line.split()[-1] for line in listing.splitlines() if len(line.split()) >= 3}
        validate_symbols(symbols, args.scenario)
        evidence = {"schema": "wasmhome-e1-build-v1", "scenario": args.scenario, "status": "build_checked_not_device_validated", "elf_sha256": sha256(elf), "effective_config_sha256": sha256(config_path), "compile_commands_sha256": sha256(build / "compile_commands.json"), "pending": ["inspect native/AOT workload disassembly", "execute checksum on ESP32-S3"]}
        evidence["idf_identity"] = actual_idf
        evidence["source_revision"] = __import__("toolchain_provenance").workspace(root)
        evidence["runtime_resolved"] = identity
        evidence["workload_optimization"] = "-O3"
        if args.scenario != "native":
            evidence["artifact_manifest_sha256"] = sha256(root / "artifacts/build-manifest.json")
            evidence["pending"].append("independent rebuild/attestation of unsigned compiler build receipt")
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence))
    except (ValueError, OSError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        parser.exit(1, f"E1 validation failed: {error}\n")


if __name__ == "__main__":
    main()

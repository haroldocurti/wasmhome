#!/usr/bin/env python3
"""Build, embed, and verify the small ABI-v1 service modules for issue #12."""

import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile

from embed_artifact import render
from toolchain_provenance import preflight, sha, workspace
from runtime_package import require

ROOT = pathlib.Path(__file__).absolute().parents[1]
OUT = ROOT / "artifacts"
TELEMETRY_SOURCE = ROOT / "workload/service_telemetry.c"
CONTROL_SOURCE = ROOT / "workload/service_control.c"
MANIFEST = OUT / "service-modules-manifest.json"
MODULES = (
    ("service_telemetry", TELEMETRY_SOURCE, None,
     ("wh_abi_version", "wh_telemetry_tick")),
    ("service_control_v1", CONTROL_SOURCE, 25000,
     ("wh_abi_version", "wh_control_request")),
    ("service_control_v2", CONTROL_SOURCE, 30000,
     ("wh_abi_version", "wh_control_request")),
)
KINDS = ("wasm", "aot")


def artifact_names():
    names = []
    for base, _, _, _ in MODULES:
        for kind in KINDS:
            names.extend((f"{base}_{kind}", f"{base}_{kind}_embedded.c"))
    return names


def verify(root=ROOT):
    root = root.absolute()
    manifest_path = root / "artifacts/service-modules-manifest.json"
    require(manifest_path.is_file(), "service module manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema") == "wasmhome-issue12-service-modules-v1",
            "service module manifest schema differs")
    require(manifest.get("source_revision") == workspace(root),
            "service source/toolchain inputs changed since artifact generation")
    for base, source, threshold, exports in MODULES:
        key = pathlib.Path(source).relative_to(root).as_posix()
        require(manifest.get("sources", {}).get(key) == sha(source),
                f"source hash differs: {key}")
    files = manifest.get("files", {})
    require(set(files) == set(artifact_names()), "service artifact manifest file set differs")
    for name in artifact_names():
        path = root / "artifacts" / name
        require(path.is_file() and path.stat().st_size > 0, f"service artifact missing: {name}")
        content = path.read_bytes()
        record = files[name]
        require(record.get("sha256") == hashlib.sha256(content).hexdigest() and
                record.get("bytes") == len(content), f"service artifact hash/size differs: {name}")
        if name.endswith("_embedded.c"):
            artifact_name = name.removesuffix("_embedded.c")
            symbol = artifact_name
            require(content.decode("ascii") == render(
                (root / "artifacts" / artifact_name).read_bytes(), symbol).encode("ascii"),
                f"embedded array differs from artifact: {name}")
        elif name.endswith("_wasm"):
            require(content[:8] == b"\0asm\x01\0\0\0", f"invalid Wasm package: {name}")
        elif name.endswith("_aot"):
            require(content[:4] == b"\0aot", f"invalid AOT package: {name}")
    return manifest


def build(root, sdk, package, wamrc, receipt, idf):
    root = root.absolute()
    sdk = sdk.absolute()
    clang = sdk / "bin/clang"
    if not clang.is_file():
        clang = sdk / "bin/clang.exe"
    toolchain = preflight(root, sdk, package.absolute(), wamrc.absolute(),
                          receipt.absolute(), idf.absolute())
    require(toolchain["wasi_sdk_identity"].get("version") == "27.0",
            "WASI-SDK 27.0 is required")
    require("clang version 20.1.8-wasi-sdk" in toolchain["clang_version"],
            "WASI-SDK clang 20.1.8 is required")
    require(toolchain["wamrc_version"] == "wamrc 2.4.5",
            "pinned wamrc 2.4.5 is required")

    source_revision = workspace(root)
    with tempfile.TemporaryDirectory(prefix="wasmhome-issue12-modules-") as temp_name:
        stage = pathlib.Path(temp_name)
        commands = {}
        artifacts = {}
        for base, source, threshold, exports in MODULES:
            wasm_path = stage / f"{base}_wasm"
            clang_flags = [
                str(clang), "--target=wasm32-wasi", "-O2", "-nostdlib",
                "-fno-builtin", "-Wl,--no-entry", "-Wl,--export-memory",
                "-Wl,-z,stack-size=8192",
                "-Wl,--initial-memory=65536", "-Wl,--max-memory=131072",
                "-Wl,--strip-all",
            ]
            clang_flags.extend(f"-Wl,--export={name}" for name in exports)
            if threshold is not None:
                clang_flags.append(f"-DWH_CONTROL_THRESHOLD_MC={threshold}")
            clang_flags.extend(("-o", str(wasm_path), str(source)))
            subprocess.run(clang_flags, check=True)
            commands[f"{base}_wasm"] = [
                item.replace(str(stage), "${STAGING_DIR}") for item in clang_flags
            ]
            artifacts[f"{base}_wasm"] = wasm_path.read_bytes()

            aot_path = stage / f"{base}_aot"
            aot_flags = [
                str(wamrc), "--target=xtensa", "--bounds-checks=1",
                "--disable-llvm-lto", "--opt-level=3", "-o", str(aot_path),
                str(wasm_path),
            ]
            subprocess.run(aot_flags, check=True)
            commands[f"{base}_aot"] = [
                item.replace(str(stage), "${STAGING_DIR}") for item in aot_flags
            ]
            artifacts[f"{base}_aot"] = aot_path.read_bytes()

        files = {}
        for name, data in artifacts.items():
            files[name] = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        for name, data in list(artifacts.items()):
            embedded_name = f"{name}_embedded.c"
            embedded = render(data, name).encode("ascii")
            artifacts[embedded_name] = embedded
            files[embedded_name] = dict(sha256=hashlib.sha256(embedded).hexdigest(),
                                        bytes=len(embedded))

        manifest = dict(
            schema="wasmhome-issue12-service-modules-v1",
            status="generated_not_device_validated",
            runtime_identity=toolchain["runtime_identity"],
            source_revision=source_revision,
            toolchain=dict(
                wasi_sdk_identity=toolchain["wasi_sdk_identity"],
                clang_version=toolchain["clang_version"],
                clang_sha256=toolchain["clang_sha256"],
                wamrc_version=toolchain["wamrc_version"],
                wamrc_sha256=toolchain["wamrc_sha256"],
                wamrc_build_receipt_sha256=toolchain["wamrc_build_receipt_sha256"],
            ),
            sources={pathlib.Path(source).relative_to(root).as_posix(): sha(source)
                     for _, source, _, _ in MODULES},
            commands=commands,
            files=files,
            pending=["inspect imports/exports and start metadata", "accept quotas on ESP32-S3"],
        )

        out = root / "artifacts"
        out.mkdir(exist_ok=True)
        for name, data in artifacts.items():
            path = out / name
            path.write_bytes(data)
        (out / "service-modules-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return verify(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="verify committed sources and generated ignored outputs")
    parser.add_argument("--wasi-sdk", type=pathlib.Path)
    parser.add_argument("--wasi-sdk-package", type=pathlib.Path)
    parser.add_argument("--wamrc", type=pathlib.Path)
    parser.add_argument("--wamrc-build-receipt", type=pathlib.Path)
    parser.add_argument("--idf", type=pathlib.Path)
    args = parser.parse_args()
    try:
        if args.verify:
            verify()
        else:
            required = (args.wasi_sdk, args.wasi_sdk_package, args.wamrc,
                        args.wamrc_build_receipt, args.idf)
            require(all(required), "WASI SDK/package, wamrc/receipt and IDF paths are required")
            build(ROOT, args.wasi_sdk, args.wasi_sdk_package, args.wamrc,
                  args.wamrc_build_receipt, args.idf)
        print("Issue #12 service modules: verified/generated")
    except (ValueError, OSError, KeyError, json.JSONDecodeError,
            subprocess.CalledProcessError) as error:
        parser.exit(1, f"Service module operation failed: {error}\n")


if __name__ == "__main__":
    main()

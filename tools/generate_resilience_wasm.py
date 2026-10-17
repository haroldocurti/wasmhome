#!/usr/bin/env python3
"""Generate the closed Wasm/AOT control trap fixture used by issue #22."""

import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile

from embed_artifact import render
from runtime_package import require

ROOT = pathlib.Path(__file__).absolute().parents[1]
SOURCE = ROOT / "workload/resilience_control_traps.wat"
OUT = ROOT / "artifacts"
MANIFEST = OUT / "resilience-wasm-fixture.json"
BASES = ("resilience_control_traps_wasm", "resilience_control_traps_aot")
OUTPUTS = (*BASES, *(f"{name}_embedded.c" for name in BASES))
WABT_VERSION_OUTPUT = "1.0.36 (git~1.0.37)"
WABT_RELEASE_URL = "https://github.com/WebAssembly/wabt/releases/download/1.0.37/wabt-1.0.37-ubuntu-20.04.tar.gz"
WABT_RELEASE_SHA256 = "cfc675dc9b663d9adc8c75d982c1ba29f661448a2e026a67e71fe3f76a3e09f3"
WAT2WASM_SHA256 = "cb9cf721c79bab2b9dee42c1be702ae9ceb2c9d091fcf200a7996a79a24e0608"
WAMRC_SHA256 = "ff1bf482570dbab67521e0bbb0c682fdbd74f5e784f364eb34bfc9de735861fb"
WAMRC_SOURCE_REVISION = "25bd7eb63e828e4bd242cc9b38d260b4b31c6605"


def sha_bytes(content):
    return hashlib.sha256(content).hexdigest()


def sha_file(path):
    return sha_bytes(path.read_bytes())


def verify(root=ROOT):
    root = root.absolute()
    out = root / "artifacts"
    manifest_path = out / "resilience-wasm-fixture.json"
    require(manifest_path.is_file(), "resilience Wasm fixture manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema") == "wasmhome-issue22-trap-fixture-v1",
            "resilience fixture manifest schema differs")
    toolchain = manifest.get("toolchain", {})
    require(toolchain.get("wat2wasm_version") == WABT_VERSION_OUTPUT and
            toolchain.get("wat2wasm_release_url") == WABT_RELEASE_URL and
            toolchain.get("wat2wasm_release_sha256") == WABT_RELEASE_SHA256 and
            toolchain.get("wat2wasm_sha256") == WAT2WASM_SHA256 and
            toolchain.get("wamrc_version") == "wamrc 2.4.5" and
            toolchain.get("wamrc_sha256") == WAMRC_SHA256 and
            toolchain.get("wamrc_source_revision") == WAMRC_SOURCE_REVISION,
            "resilience fixture toolchain pin differs")
    require(manifest.get("source") == {
        "path": "workload/resilience_control_traps.wat",
        "sha256": sha_file(root / "workload/resilience_control_traps.wat"),
    }, "resilience WAT source differs from generated fixture")
    require(set(manifest.get("files", {})) == set(OUTPUTS),
            "resilience fixture manifest file set differs")
    for name in OUTPUTS:
        path = out / name
        require(path.is_file() and path.stat().st_size > 0,
                f"resilience fixture output missing: {name}")
        content = path.read_bytes()
        record = manifest["files"][name]
        require(record == {"sha256": sha_bytes(content), "bytes": len(content)},
                f"resilience fixture hash/size differs: {name}")
        if name.endswith("_embedded.c"):
            artifact_name = name.removesuffix("_embedded.c")
            require(content == render((out / artifact_name).read_bytes(), artifact_name)
                    .encode("ascii"), f"embedded array differs: {name}")
        elif name.endswith("_wasm"):
            require(content[:8] == b"\0asm\x01\0\0\0", "invalid Wasm fixture")
        elif name.endswith("_aot"):
            require(content[:4] == b"\0aot", "invalid AOT fixture")
    return manifest


def generate(wat2wasm, wamrc):
    wat2wasm = wat2wasm.absolute()
    wamrc = wamrc.absolute()
    require(wat2wasm.is_file(), "wat2wasm executable is missing")
    require(wamrc.is_file(), "wamrc executable is missing")
    wat_version = subprocess.run([str(wat2wasm), "--version"], check=True,
                                 text=True, capture_output=True).stdout.strip()
    wamrc_version = subprocess.run([str(wamrc), "--version"], check=True,
                                   text=True, capture_output=True).stdout.strip()
    require(wat_version == WABT_VERSION_OUTPUT,
            "official WABT 1.0.37 Linux release executable is required")
    require(sha_file(wat2wasm) == WAT2WASM_SHA256,
            "wat2wasm binary differs from the official pinned WABT release")
    require(wamrc_version == "wamrc 2.4.5", "pinned wamrc 2.4.5 is required")
    require(sha_file(wamrc) == WAMRC_SHA256,
            "wamrc binary differs from the pinned WAMR compiler build")

    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wasmhome-issue22-fixture-") as temp:
        stage = pathlib.Path(temp)
        wasm = stage / BASES[0]
        aot = stage / BASES[1]
        wat_command = [str(wat2wasm), "--debug-names", str(SOURCE), "-o", str(wasm)]
        subprocess.run(wat_command, check=True)
        aot_command = [str(wamrc), "--target=xtensa", "--bounds-checks=1",
                       "--disable-llvm-lto", "--opt-level=3", "-o", str(aot),
                       str(wasm)]
        subprocess.run(aot_command, check=True)

        artifacts = {BASES[0]: wasm.read_bytes(), BASES[1]: aot.read_bytes()}
        for name, content in tuple(artifacts.items()):
            artifacts[f"{name}_embedded.c"] = render(content, name).encode("ascii")
        for name, content in artifacts.items():
            (OUT / name).write_bytes(content)

    manifest = {
        "schema": "wasmhome-issue22-trap-fixture-v1",
        "status": "generated_not_device_validated",
        "source": {"path": "workload/resilience_control_traps.wat",
                   "sha256": sha_file(SOURCE)},
        "toolchain": {
            "wat2wasm_version": wat_version,
            "wat2wasm_sha256": sha_file(wat2wasm),
            "wat2wasm_release_url": WABT_RELEASE_URL,
            "wat2wasm_release_sha256": WABT_RELEASE_SHA256,
            "wamrc_version": wamrc_version,
            "wamrc_sha256": sha_file(wamrc),
            "wamrc_source_revision": WAMRC_SOURCE_REVISION,
            "bounds_checks": True,
            "target": "xtensa",
        },
        "commands": {
            "wasm": ["${WAT2WASM}", "--debug-names",
                     "workload/resilience_control_traps.wat", "-o",
                     "${STAGING_DIR}/resilience_control_traps_wasm"],
            "aot": ["${WAMRC}", "--target=xtensa", "--bounds-checks=1",
                    "--disable-llvm-lto", "--opt-level=3", "-o",
                    "${STAGING_DIR}/resilience_control_traps_aot",
                    "${STAGING_DIR}/resilience_control_traps_wasm"],
        },
        "files": {name: {"sha256": sha_bytes(content), "bytes": len(content)}
                  for name, content in artifacts.items()},
        "pending": ["execute fixture on ESP32-S3 in each configured module mode"],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return verify(ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="verify generated ignored outputs against their manifest")
    parser.add_argument("--wat2wasm", type=pathlib.Path)
    parser.add_argument("--wamrc", type=pathlib.Path)
    args = parser.parse_args()
    try:
        if args.verify:
            verify()
        else:
            require(args.wat2wasm is not None and args.wamrc is not None,
                    "--wat2wasm and --wamrc are required for generation")
            generate(args.wat2wasm, args.wamrc)
        print("Issue #22 Wasm trap fixture: verified/generated")
    except (ValueError, OSError, KeyError, json.JSONDecodeError,
            subprocess.CalledProcessError) as error:
        parser.exit(1, f"Resilience fixture operation failed: {error}\n")


if __name__ == "__main__":
    main()

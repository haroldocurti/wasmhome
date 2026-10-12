"""Generate a checked artifact directory; publish by recoverable directory rename."""
import argparse
import json
import os
import pathlib
import stat
import subprocess
import uuid

from embed_artifact import render
from evidence_json import read
from runtime_package import require
from toolchain_provenance import preflight, sha, workspace
from validate_build_config import validate_artifacts

ROOT = pathlib.Path(__file__).absolute().parents[1]
NAMES = ("benchmark.wasm", "benchmark.aot", "benchmark.wasm.s", "benchmark_wasm_embedded.c", "benchmark_aot_embedded.c")


def safe_path(root, path):
    root, path = root.absolute(), path.absolute()
    require(path.is_relative_to(root) and path.resolve().is_relative_to(root.resolve()), "artifact path escapes workspace")
    for item in (path, *path.parents):
        if item.is_relative_to(root) and os.path.lexists(item):
            info = item.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT), "artifact path contains symlink/junction/reparse point")
    return path


def safe_directory(root, path):
    safe_path(root, path)
    if path.exists():
        require(path.is_dir(), "artifact destination must be a directory")
        for item in path.rglob("*"):
            safe_path(root, item)


def transaction_paths(root, identifier):
    require(isinstance(identifier, str) and len(identifier) == 32 and all(x in "0123456789abcdef" for x in identifier), "invalid artifact transaction identity")
    return root / f"build-artifacts-stage-{identifier}", root / f"build-artifacts-previous-{identifier}"


def recover(root=ROOT):
    root = root.absolute()
    lock = root / "build-artifacts-publication"
    safe_directory(root, lock)
    journal = read(lock / "transaction.json")
    require(journal.get("schema") == "wasmhome-artifact-publication-v1", "unknown artifact transaction")
    stage, backup = transaction_paths(root, journal["id"])
    target = root / "artifacts"
    for path in (target, stage, backup):
        safe_directory(root, path)
    if backup.exists():
        if target.exists():
            require(not stage.exists(), "both published and staged sets exist; inspect before recovery")
            target.rename(stage)  # Preserve the interrupted new set for inspection.
        backup.rename(target)
    else:
        require(target.exists() or stage.exists(), "transaction has no recoverable artifact directory")
    (lock / "transaction.json").unlink()
    lock.rmdir()
    return "Previous artifact set restored where available; staged data preserved for inspection"


def publish(root, stage, identifier):
    target = root / "artifacts"
    _, backup = transaction_paths(root, identifier)
    for path in (target, stage, backup):
        safe_directory(root, path)
    require(stage.is_dir() and not backup.exists(), "publication stage/backup identity differs")
    if target.exists():
        target.rename(backup)
    try:
        safe_directory(root, stage)
        safe_directory(root, target)
        stage.rename(target)
    except BaseException:
        if backup.exists() and not target.exists():
            safe_directory(root, backup)
            backup.rename(target)
        raise
    return backup if backup.exists() else None


def generate(root, sdk, package, wamrc, receipt, idf):
    root = root.absolute()
    target = root / "artifacts"
    lock = root / "build-artifacts-publication"
    safe_directory(root, target)
    safe_directory(root, lock)
    require(not lock.exists(), "artifact transaction already exists; inspect and use --recover-publication")
    manifest = preflight(root, sdk, package, wamrc, receipt, idf)
    identifier = uuid.uuid4().hex
    stage, backup = transaction_paths(root, identifier)
    for path in (stage, backup):
        safe_directory(root, path)
        require(not path.exists(), "artifact staging/backup already exists")
    lock.mkdir()  # Exclusive lock: a competing writer fails here.
    try:
        with (lock / "transaction.json").open("x", encoding="utf-8", newline="\n") as journal:
            journal.write(json.dumps(dict(schema="wasmhome-artifact-publication-v1", id=identifier)) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
        stage.mkdir()
        clang_flags = ["--target=wasm32-wasi", "-O3", "-nostdlib", "-I", str(root / "workload")]
        link_flags = ["-Wl,--no-entry", "-Wl,--export=benchmark_run", "-Wl,--strip-all", "-Wl,-z,stack-size=8192", "-Wl,--initial-memory=65536"]
        aot_flags = ["--target=xtensa", "--bounds-checks=1", "--disable-llvm-lto", "-O3"]
        source = root / "workload/benchmark.c"
        for arguments in ([manifest["clang_path"], *clang_flags, *link_flags, "-o", str(stage / "benchmark.wasm"), str(source)],
                          [manifest["clang_path"], *clang_flags, "-S", "-o", str(stage / "benchmark.wasm.s"), str(source)],
                          [manifest["wamrc_path"], *aot_flags, "-o", str(stage / "benchmark.aot"), str(stage / "benchmark.wasm")]):
            subprocess.run(arguments, check=True)
            safe_directory(root, stage)
        for kind in ("wasm", "aot"):
            (stage / f"benchmark_{kind}_embedded.c").write_text(render((stage / f"benchmark.{kind}").read_bytes(), f"benchmark_{kind}"), encoding="ascii", newline="\n")
        manifest.update(schema="wasmhome-e1-artifacts-v3", status="generated_not_device_validated", aot_format_version=5,
                        source_sha256=sha(source), header_sha256=sha(root / "workload/benchmark.h"),
                        clang_flags=clang_flags + link_flags, aot_flags=aot_flags, expected_checksum="0xDBB2F0D4",
                        files={name: dict(sha256=sha(stage / name), bytes=(stage / name).stat().st_size) for name in NAMES},
                        pending=["inspect native/AOT disassembly", "execute checksum on ESP32-S3", "accept candidate runtime", "independent rebuild/attestation of unsigned compiler receipt"])
        require(workspace(root) == manifest["source_revision"], "WasmHome source changed during generation")
        (stage / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        validate_artifacts(stage, root)
        backup_path = publish(root, stage, identifier)
    except BaseException:
        # Preserve the failed staging set and journal. The previous canonical
        # set remains intact or was restored; explicit recovery unlocks retry.
        raise
    (lock / "transaction.json").unlink()
    lock.rmdir()
    return dict(directory=str(target), previous_preserved=str(backup_path) if backup_path else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wasi-sdk", type=pathlib.Path)
    parser.add_argument("--wasi-sdk-package", type=pathlib.Path)
    parser.add_argument("--wamrc", type=pathlib.Path)
    parser.add_argument("--wamrc-build-receipt", type=pathlib.Path)
    parser.add_argument("--idf", type=pathlib.Path)
    parser.add_argument("--recover-publication", action="store_true")
    args = parser.parse_args()
    try:
        if args.recover_publication:
            print(recover())
        else:
            require(args.wasi_sdk is not None and args.wamrc is not None, "WASI SDK and wamrc paths are required")
            print(json.dumps(generate(ROOT, args.wasi_sdk, args.wasi_sdk_package, args.wamrc, args.wamrc_build_receipt, args.idf)))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Artifact generation failed: {error}\n")

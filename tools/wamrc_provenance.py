"""Create and validate the reviewable redacted WAMRC build provenance record."""

import argparse
import hashlib
import json
import pathlib
import re

from evidence_json import read
from runtime_package import require, verify as verify_runtime
from toolchain_provenance import receipt_digest, sha


ROOT = pathlib.Path(__file__).resolve().parents[1]
RECORD = pathlib.Path("docs/evidence/issue22-wamrc-build-provenance.json")
SCHEMA = "wasmhome-issue22-wamrc-build-provenance-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOTS = ("llvm_cache", "wamrc_cache", "llvm_commands", "wamrc_commands")
TOOLS = ("c", "cxx", "cmake", "ninja")
COMMANDS = ("llvm_configure", "llvm_build", "wamrc_configure", "wamrc_build")


def redact_paths(value, root, toolchain_root):
    text = str(value)
    replacements = (
        (str(root.resolve()), "${WASMHOME_ROOT}"),
        (str(toolchain_root.resolve()), "${TOOLCHAIN_ROOT}"),
    )
    for prefix, alias in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        text = text.replace(prefix, alias)
    return text


def make_record(receipt, receipt_path, root=ROOT, toolchain_root=None):
    root = root.resolve()
    receipt_path = receipt_path.resolve()
    toolchain_root = (toolchain_root or receipt_path.parent).resolve()
    require(receipt.get("schema") == "wasmhome-wamrc-build-v1",
            "complete WAMRC build receipt required")
    snapshots = receipt.get("snapshots", {})
    require(set(snapshots) == set(SNAPSHOTS), "receipt snapshots are incomplete")
    build_tools = receipt.get("build_tools", {})
    require(set(build_tools) == set(TOOLS), "receipt build tools are incomplete")
    commands = receipt.get("commands", {})
    require(set(commands) == set(COMMANDS), "receipt build commands are incomplete")

    public_receipt = {
        "schema": receipt["schema"],
        "raw_file_sha256": sha(receipt_path),
        "canonical_json_sha256": receipt_digest(receipt),
        "runtime_identity": receipt["runtime_identity"],
        "llvm_source": receipt["llvm_source"],
        "binary": receipt["binary"],
        "fresh_build_directories": receipt["fresh_build_directories"],
        "build_tools": {
            name: {
                "executable": pathlib.Path(record["path"]).name,
                "version": record["version"],
                "sha256": record["sha256"],
            }
            for name, record in build_tools.items()
        },
        "commands": {
            name: [redact_paths(item, root, toolchain_root) for item in command]
            for name, command in commands.items()
        },
        "llvm_cmake_dir": redact_paths(receipt["llvm_cmake_dir"], root, toolchain_root),
        "llvm_source_dir": redact_paths(receipt["llvm_source_dir"], root, toolchain_root),
        "wamrc_source_dir": redact_paths(receipt["wamrc_source_dir"], root, toolchain_root),
        "snapshots": {
            name: {
                "sha256": record["sha256"],
                "utf8_bytes": len(record["text"].encode("utf-8")),
            }
            for name, record in snapshots.items()
        },
        "linked_llvm_libraries": receipt["linked_llvm_libraries"],
    }
    return {
        "schema": SCHEMA,
        "trust_boundary": (
            "Unsigned local build receipt; toolchain identities and recorded "
            "source/build snapshots were checked, but this is not an independent rebuild."
        ),
        "redactions": [
            "Local absolute path prefixes are replaced with WASMHOME_ROOT and TOOLCHAIN_ROOT aliases.",
            "CMake cache and compile_commands text is omitted; its original SHA-256 and UTF-8 byte count are retained.",
            "The raw-file and canonical-JSON digests bind this summary to the complete original receipt.",
        ],
        "receipt": public_receipt,
    }


def load_record(root=ROOT):
    root = root.resolve()
    path = root / RECORD
    require(path.is_file(), "reviewable WAMRC provenance record is missing")
    record = read(path)
    require(record.get("schema") == SCHEMA, "WAMRC provenance record schema differs")
    receipt = record.get("receipt", {})
    require(receipt.get("schema") == "wasmhome-wamrc-build-v1",
            "WAMRC provenance receipt schema differs")
    for name in ("raw_file_sha256", "canonical_json_sha256"):
        require(SHA256_RE.fullmatch(receipt.get(name, "")),
                f"invalid WAMRC provenance digest: {name}")
    require(receipt.get("runtime_identity") == verify_runtime(root)[0],
            "WAMRC provenance runtime identity differs from the pinned runtime")
    require(receipt.get("binary", {}).get("version") == "wamrc 2.4.5" and
            SHA256_RE.fullmatch(receipt["binary"].get("sha256", "")),
            "WAMRC provenance binary identity is invalid")
    require(receipt.get("fresh_build_directories") is True,
            "WAMRC provenance lacks fresh build-directory evidence")
    require(receipt.get("llvm_source", {}).get("clean") is True,
            "WAMRC provenance LLVM source was not clean")
    require(set(receipt.get("build_tools", {})) == set(TOOLS) and
            set(receipt.get("commands", {})) == set(COMMANDS) and
            set(receipt.get("snapshots", {})) == set(SNAPSHOTS),
            "WAMRC provenance build record is incomplete")
    for tool in receipt["build_tools"].values():
        require(tool.get("executable") and tool.get("version") and
                SHA256_RE.fullmatch(tool.get("sha256", "")),
                "WAMRC provenance build tool identity is invalid")
    for snapshot in receipt["snapshots"].values():
        require(SHA256_RE.fullmatch(snapshot.get("sha256", "")) and
                isinstance(snapshot.get("utf8_bytes"), int) and snapshot["utf8_bytes"] > 0,
                "WAMRC provenance snapshot identity is invalid")
    library_names = {item.get("name") for item in receipt.get("linked_llvm_libraries", [])}
    require({"libLLVMCore.a", "libLLVMXtensaCodeGen.a", "libLLVMX86CodeGen.a"} <= library_names,
            "WAMRC provenance is missing required linked LLVM libraries")
    return record, sha(path)


def verify_receipt(receipt_path, receipt, root=ROOT, toolchain_root=None):
    root = root.resolve()
    record, record_sha256 = load_record(root)
    expected = make_record(receipt, receipt_path, root, toolchain_root)
    require(record == expected,
            "reviewable WAMRC provenance record does not match the supplied build receipt")
    return record, record_sha256


def assert_manifest_provenance(manifest, root=ROOT):
    record, record_sha256 = load_record(root)
    receipt = record["receipt"]
    require(manifest.get("runtime_identity") == receipt["runtime_identity"],
            "artifact manifest runtime identity differs from WAMRC provenance")
    require(manifest.get("wamrc_build_receipt_sha256") == receipt["canonical_json_sha256"],
            "artifact manifest canonical WAMRC receipt digest differs")
    require(manifest.get("wamrc_receipt_input_file_sha256") == receipt["raw_file_sha256"],
            "artifact manifest raw WAMRC receipt digest differs")
    require(manifest.get("wamrc_provenance_record_sha256") == record_sha256,
            "artifact manifest redacted WAMRC provenance digest differs")
    toolchain = manifest.get("toolchain", {})
    require(toolchain.get("wamrc_sha256") == receipt["binary"]["sha256"] and
            toolchain.get("wamrc_version") == receipt["binary"]["version"],
            "artifact manifest WAMRC binary identity differs from provenance")
    return record, record_sha256


def write_record(receipt_path, output=ROOT / RECORD, root=ROOT, toolchain_root=None):
    root = root.resolve()
    receipt_path = receipt_path.resolve()
    output = output.resolve()
    require(not output.exists(), "provenance output exists; preserve it")
    receipt = read(receipt_path)
    record = make_record(receipt, receipt_path, root, toolchain_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    load_record(root)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=pathlib.Path, required=True)
    parser.add_argument("--toolchain-root", type=pathlib.Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify:
            receipt = read(args.receipt)
            record, digest = verify_receipt(args.receipt, receipt)
            print(json.dumps({"record_sha256": digest,
                              "receipt_sha256": record["receipt"]["canonical_json_sha256"],
                              "binary_sha256": record["receipt"]["binary"]["sha256"]}, indent=2))
        else:
            record = write_record(args.receipt, toolchain_root=args.toolchain_root)
            print(json.dumps({"record": str(RECORD),
                              "receipt_sha256": record["receipt"]["canonical_json_sha256"],
                              "binary_sha256": record["receipt"]["binary"]["sha256"]}, indent=2))
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"WAMRC provenance operation failed: {error}\n")


if __name__ == "__main__":
    main()

import copy
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from generate_artifacts import generate, publish, recover, safe_directory
from evidence_json import loads
from toolchain_provenance import WAMR_SHA, IDF_SHA, WASI_SHA, WASI_URL, LLVM_SHA, validate_manifest, receipt_digest, check_compiler_receipt, workspace


def receipt_fixture(identity, binary_hash):
    tools = {key: dict(path=f"/fixture/{key}", version="fixture 1", sha256="a" * 64) for key in ("c", "cxx", "cmake", "ninja")}
    common = "CMAKE_BUILD_TYPE:STRING=Release\nCMAKE_GENERATOR:INTERNAL=Ninja\nCMAKE_C_COMPILER:FILEPATH=/fixture/c\nCMAKE_CXX_COMPILER:FILEPATH=/fixture/cxx\nCMAKE_COMMAND:INTERNAL=/fixture/cmake\nCMAKE_MAKE_PROGRAM:FILEPATH=/fixture/ninja\n"
    def snapshot(text):
        return dict(text=text, sha256=hashlib.sha256(text.encode()).hexdigest())
    return dict(schema="wasmhome-wamrc-build-v1", runtime_identity=identity,
                binary=dict(sha256=binary_hash, version="wamrc 2.4.5"), fresh_build_directories=True,
                llvm_source=dict(origin="https://github.com/espressif/llvm-project.git", revision=LLVM_SHA, clean=True, tree="b" * 40),
                build_tools=tools, commands=dict(llvm_configure=["/fixture/cmake", "-S", "/fixture/llvm/llvm", "-B", "/fixture/llvm-build"], wamrc_configure=["/fixture/cmake", "-S", "/fixture/wasm-micro-runtime/wamr-compiler", "-B", "/fixture/wamrc-build"], llvm_build=["/fixture/cmake", "--build", "/fixture/llvm-build"], wamrc_build=["/fixture/cmake", "--build", "/fixture/wamrc-build"]),
                llvm_cmake_dir="/fixture/llvm-build/lib/cmake/llvm", llvm_source_dir="/fixture/llvm/llvm",
                wamrc_source_dir="/fixture/wasm-micro-runtime/wamr-compiler",
                snapshots=dict(llvm_cache=snapshot(common + "CMAKE_CACHEFILE_DIR:INTERNAL=/fixture/llvm-build\nCMAKE_HOME_DIRECTORY:INTERNAL=/fixture/llvm/llvm\nLLVM_TARGETS_TO_BUILD:STRING=X86\nLLVM_EXPERIMENTAL_TARGETS_TO_BUILD:STRING=Xtensa\nLLVM_ENABLE_PROJECTS:STRING=\nLLVM_PARALLEL_LINK_JOBS:STRING=1\n"),
                               wamrc_cache=snapshot(common + "CMAKE_CACHEFILE_DIR:INTERNAL=/fixture/wamrc-build\nCMAKE_HOME_DIRECTORY:INTERNAL=/fixture/wasm-micro-runtime/wamr-compiler\nWAMR_BUILD_WITH_CUSTOM_LLVM:BOOL=ON\nLLVM_DIR:PATH=/fixture/llvm-build/lib/cmake/llvm\n"),
                               llvm_commands=snapshot('[{"file":"fixture.c","command":"fixture"}]'), wamrc_commands=snapshot('[{"file":"fixture.c","command":"fixture"}]')),
                linked_llvm_libraries=[dict(name=name, sha256="c" * 64, bytes=1) for name in ("libLLVMCore.a", "libLLVMXtensaCodeGen.a", "libLLVMX86CodeGen.a")])


class PublicationTests(unittest.TestCase):
    def old(self, root):
        path = root / "artifacts"
        path.mkdir()
        (path / "build-manifest.json").write_bytes(b"old manifest")
        (path / "benchmark.wasm").write_bytes(b"old payload")
        return {p.name: p.read_bytes() for p in path.iterdir()}

    def test_missing_receipt_preflight_preserves_previous_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            previous = self.old(root)
            with patch("toolchain_provenance.workspace", return_value={}):
                with self.assertRaisesRegex(ValueError, "mandatory"):
                    generate(root, root / "sdk", None, root / "wamrc", None, None)
            self.assertEqual({p.name: p.read_bytes() for p in (root / "artifacts").iterdir()}, previous)
            self.assertEqual({p.name for p in root.iterdir()}, {"artifacts"})

    def test_powershell_frontend_requires_origin_proof_before_writes(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(shell, "PowerShell frontend check requires PowerShell")
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            previous = self.old(root)
            scripts = root / "tools"
            scripts.mkdir()
            shutil.copyfile(ROOT / "tools/build-artifacts.ps1", scripts / "build-artifacts.ps1")
            result = subprocess.run([shell, "-NoProfile", "-File", str(scripts / "build-artifacts.ps1")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing origin proof", result.stdout + result.stderr)
            self.assertEqual({p.name: p.read_bytes() for p in (root / "artifacts").iterdir()}, previous)
            self.assertEqual({p.name for p in root.iterdir()}, {"artifacts", "tools"})

    def test_actual_compiler_process_failure_preserves_previous(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            previous = self.old(root)
            # Start a real process that rejects clang's flags. No mock of run.
            manifest = dict(clang_path=sys.executable, wamrc_path=sys.executable)
            with patch("generate_artifacts.preflight", return_value=manifest), self.assertRaises(subprocess.CalledProcessError):
                generate(root, root / "sdk", None, root / "wamrc", None, None)
            self.assertEqual({p.name: p.read_bytes() for p in (root / "artifacts").iterdir()}, previous)
            self.assertTrue((root / "build-artifacts-publication/transaction.json").is_file())
            recover(root)
            self.assertEqual((root / "artifacts/build-manifest.json").read_bytes(), b"old manifest")

    def test_invalid_generated_set_never_replaces_previous(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            previous = self.old(root)
            (root / "workload").mkdir()
            (root / "workload/benchmark.c").write_text("fixture")
            (root / "workload/benchmark.h").write_text("fixture")
            manifest = dict(clang_path="fixture", wamrc_path="fixture", source_revision={})
            def bad_compiler(arguments, **kwargs):
                output = pathlib.Path(arguments[arguments.index("-o") + 1])
                output.write_bytes(b"invalid payload")
            with patch("generate_artifacts.preflight", return_value=manifest), patch("generate_artifacts.workspace", return_value={}), patch("generate_artifacts.subprocess.run", side_effect=bad_compiler), patch("validate_build_config.resolved_runtime", return_value=({}, None, None)), self.assertRaises(ValueError):
                generate(root, root / "sdk", None, root / "wamrc", None, None)
            self.assertEqual({p.name: p.read_bytes() for p in (root / "artifacts").iterdir()}, previous)
            self.assertEqual(len(list(root.glob("build-artifacts-stage-*/build-manifest.json"))), 1)
            recover(root)

    def test_successful_directory_swap_preserves_entire_previous_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            previous = self.old(root)
            identifier = "b" * 32
            stage = root / f"build-artifacts-stage-{identifier}"
            stage.mkdir()
            (stage / "build-manifest.json").write_bytes(b"new manifest")
            backup = publish(root, stage, identifier)
            self.assertEqual({p.name: p.read_bytes() for p in backup.iterdir()}, previous)
            self.assertEqual((root / "artifacts/build-manifest.json").read_bytes(), b"new manifest")

    def test_swap_failure_rolls_back_and_interruption_is_recoverable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.old(root)
            identifier = "a" * 32
            stage = root / f"build-artifacts-stage-{identifier}"
            stage.mkdir()
            (stage / "build-manifest.json").write_bytes(b"new manifest")
            original = pathlib.Path.rename
            def fail_stage(path, target):
                if path == stage:
                    raise OSError("fixture rename failure")
                return original(path, target)
            with patch.object(pathlib.Path, "rename", fail_stage), self.assertRaisesRegex(OSError, "rename failure"):
                publish(root, stage, identifier)
            self.assertEqual((root / "artifacts/build-manifest.json").read_bytes(), b"old manifest")
            (root / "artifacts").rename(root / f"build-artifacts-previous-{identifier}")
            lock = root / "build-artifacts-publication"
            lock.mkdir()
            (lock / "transaction.json").write_text(json.dumps(dict(schema="wasmhome-artifact-publication-v1", id=identifier)))
            recover(root)
            self.assertEqual((root / "artifacts/build-manifest.json").read_bytes(), b"old manifest")
            self.assertTrue(stage.exists())

    def test_windows_junction_target_and_staging_are_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows junction negative requires Windows")
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external:
            root, outside = pathlib.Path(temporary), pathlib.Path(external)
            marker = outside / "marker"
            marker.write_bytes(b"preserved")
            for name in ("artifacts", "build-artifacts-stage-" + "a" * 32):
                junction = root / name
                subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
                try:
                    with self.assertRaises(ValueError):
                        safe_directory(root, junction)
                    if name == "artifacts":
                        with self.assertRaises(ValueError):
                            generate(root, root / "sdk", None, root / "wamrc", None, None)
                    self.assertEqual(marker.read_bytes(), b"preserved")
                finally:
                    os.rmdir(junction)  # Remove only the fixture junction, never its target.


class ProvenanceTests(unittest.TestCase):
    def test_ambiguous_compiler_receipt_json_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            loads('{"snapshots":{"llvm_cache":{},"llvm_cache":{}}}')

    def test_receipt_fields_hashes_and_pins_fail_closed(self):
        identity = dict(component="wasmhome/wasm-micro-runtime")
        receipt = receipt_fixture(identity, "d" * 64)
        check_compiler_receipt(receipt, "d" * 64, identity)
        for field, value in (("schema", "operator-only"), ("runtime_identity", {}), ("fresh_build_directories", False), ("linked_llvm_libraries", []), ("snapshots", {})):
            bad = copy.deepcopy(receipt)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                check_compiler_receipt(bad, "d" * 64, identity)
        with self.assertRaisesRegex(ValueError, "binary differs"):
            check_compiler_receipt(receipt, "e" * 64, identity)
        bad = copy.deepcopy(receipt)
        bad["commands"]["llvm_configure"][2] = "/wrong/source"
        with self.assertRaisesRegex(ValueError, "cache identity"):
            check_compiler_receipt(bad, "d" * 64, identity)
        bad = copy.deepcopy(receipt)
        bad["snapshots"]["llvm_cache"]["text"] = bad["snapshots"]["llvm_cache"]["text"].replace("=Xtensa", "=ARM")
        bad["snapshots"]["llvm_cache"]["sha256"] = hashlib.sha256(bad["snapshots"]["llvm_cache"]["text"].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "X86/Xtensa"):
            check_compiler_receipt(bad, "d" * 64, identity)
        manifest = dict(provenance_trust="unsigned_build_receipt_and_local_inputs_checked_not_independent_rebuild",
                        idf_identity=dict(revision=IDF_SHA, version="5.2.6", origin="https://github.com/espressif/esp-idf.git", tree="a" * 40, submodules=" " + "b" * 40 + " fixture/module"), wasi_sdk_identity=dict(version="27.0", package_sha256=WASI_SHA, package_url=WASI_URL),
                        wamrc_source_revision=WAMR_SHA, runtime_identity=identity, wamrc_sha256="d" * 64,
                        wamrc_build_receipt=receipt, wamrc_build_receipt_sha256=receipt_digest(receipt), wamrc_receipt_input_file_sha256="c" * 64)
        validate_manifest(manifest, ROOT, check_workspace=False)
        for key in ("idf_identity", "wasi_sdk_identity", "wamrc_build_receipt_sha256"):
            bad = copy.deepcopy(manifest)
            bad[key] = {} if key.endswith("identity") else "0" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_manifest(bad, ROOT, check_workspace=False)

    def test_untracked_workspace_blocks_revision_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            (root / "rogue.c").write_text("untracked")
            with self.assertRaisesRegex(ValueError, "including untracked"):
                workspace(root)


if __name__ == "__main__":
    unittest.main()

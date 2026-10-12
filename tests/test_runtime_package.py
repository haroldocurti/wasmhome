import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from runtime_package import acquire, verify, PACKAGE


class RuntimePackageTests(unittest.TestCase):
    def fixture(self, root):
        port = root / "runtime-port"
        port.mkdir()
        metadata = b'version: "2.4.5-wasmhome.1"\ndependencies:\n  idf: "==5.2.6"\n'
        (port / "idf_component.yml").write_bytes(metadata)
        pins = {}
        for key, name in (("upstream", "wamr"), ("app_framework", "app-framework")):
            repo = root / "build-runtime-port/sources" / name
            repo.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(["git", "-c", "core.autocrlf=false", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init")
            for path in ("core/config.h", "core/version.h", "core/iwasm/common/wasm_runtime_common.c") if key == "upstream" else ("LICENSE",):
                file = repo / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"#define AOT_CURRENT_VERSION 5\n" if path == "core/config.h" else b"#define WAMR_VERSION_MAJOR 2\n#define WAMR_VERSION_MINOR 4\n#define WAMR_VERSION_PATCH 5\n" if path == "core/version.h" else b"fixture\n")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
            url = "https://example.invalid/" + name + ".git"
            git("remote", "add", "origin", url)
            pins[key] = dict(url=url, revision=git("rev-parse", "HEAD"))
        lock = dict(schema="wasmhome-runtime-port-v1", component="wasmhome/wasm-micro-runtime", version="2.4.5-wasmhome.1", aot_version=5,
                    patches=[], patched_files={}, metadata_sha256=hashlib.sha256(metadata).hexdigest(), **pins)
        files = {path: (root / "build-runtime-port/sources/wamr" / path).read_bytes() for path in ("core/config.h", "core/version.h", "core/iwasm/common/wasm_runtime_common.c")}
        files["wamr-app-framework/LICENSE"] = b"fixture\n"
        files["idf_component.yml"] = metadata
        package_hash = hashlib.sha256()
        for name, data in sorted(files.items()):
            package_hash.update(name.encode())
            package_hash.update(hashlib.sha256(data).hexdigest().encode())
        lock["package_sha256"] = package_hash.hexdigest()
        (port / "lock.json").write_text(json.dumps(lock))
        return acquire(root)

    def test_fresh_acquisition_repeat_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            identity = self.fixture(root)
            self.assertEqual(acquire(root), identity)
            directory = root / PACKAGE
            path = directory / "core/version.h"
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            verify(root)  # WAMR configure regenerates only this header.
            rogue = directory / "core/rogue.c"
            rogue.write_bytes(b"rogue")
            with self.assertRaisesRegex(ValueError, "extra/untracked"):
                verify(root)
            rogue.unlink()
            path = directory / "core/config.h"
            path.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "core AOT version differs"):
                acquire(root)  # Never silently overwrites a modified package.

    def test_dirty_untracked_source_and_forged_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.fixture(root)
            rogue = root / "build-runtime-port/sources/wamr/untracked.c"
            rogue.write_text("untracked")
            with self.assertRaisesRegex(ValueError, "dirty/untracked"):
                verify(root)
            rogue.unlink()
            receipt = root / PACKAGE / ".wasmhome-runtime.json"
            receipt.write_text("{}")
            with self.assertRaisesRegex(ValueError, "receipt differs"):
                verify(root)

    def test_metadata_origin_revision_and_patch_digest_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.fixture(root)
            port = root / "runtime-port"
            lock_path = port / "lock.json"
            original = lock_path.read_text()
            for field, value, message in (("url", "https://wrong.invalid", "origin differs"), ("revision", "0" * 40, "revision differs")):
                lock = json.loads(original)
                lock["upstream"][field] = value
                lock_path.write_text(json.dumps(lock))
                with self.assertRaisesRegex(ValueError, message):
                    verify(root)
            lock = json.loads(original)
            (port / "broken.patch").write_bytes(b"changed patch")
            lock["patches"] = [dict(path="broken.patch", sha256="0" * 64)]
            lock_path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "patch digest"):
                verify(root)

    def test_ambiguous_json_and_semantic_identity_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            self.fixture(root)
            lock_path = root / "runtime-port/lock.json"
            original = lock_path.read_text()
            lock_path.write_text(original[:-1] + ',"aot_version":4}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                verify(root)
            for key, value in (("component", "espressif/wasm-micro-runtime"), ("version", "2.4.4"), ("aot_version", 4)):
                lock = json.loads(original)
                lock[key] = value
                lock_path.write_text(json.dumps(lock))
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "unexpected"):
                    verify(root)
            lock_path.write_text(original)
            receipt = root / PACKAGE / ".wasmhome-runtime.json"
            receipt.write_text(receipt.read_text().rstrip()[:-1] + ',"aot_version":4}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                verify(root)


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.issue22_source_identity import source_identity
from tools.issue22_profile_manifest import build_manifest


class Issue22SourceIdentityTests(unittest.TestCase):
    def test_source_identity_rejects_edits_untracked_files_and_changed_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*arguments):
                return subprocess.check_output(["git", "-C", str(root), *arguments],
                    text=True, stderr=subprocess.PIPE).strip()

            git("init", "--quiet")
            source = root / "source.c"
            source.write_text("initial source\n")
            git("add", "source.c")
            git("-c", "user.name=Build fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "--quiet", "-m", "Initial fixture")
            identity = source_identity(root)
            self.assertEqual(identity, {"source_commit": git("rev-parse", "HEAD"),
                                        "source_tree": git("rev-parse", "HEAD^{tree}")})
            self.assertEqual(source_identity(root, expected_commit=identity["source_commit"],
                                            expected_tree=identity["source_tree"]), identity)
            source.write_text("changed source\n")
            with self.assertRaisesRegex(ValueError, "must be clean"):
                source_identity(root)
            git("checkout", "--", "source.c")
            untracked = root / "untracked.c"
            untracked.write_text("untracked build input\n")
            with self.assertRaisesRegex(ValueError, "must be clean"):
                source_identity(root)
            untracked.unlink()
            for expected in ({"expected_commit": "b" * 40}, {"expected_tree": "b" * 40}):
                with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, "source changed"):
                    source_identity(root, **expected)

    def test_manifest_derives_source_from_bins_and_rejects_relabeling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            builds = {}
            for name in ("native", "interp", "aot"):
                build = root / name
                (build / "bootloader/config").mkdir(parents=True)
                (build / "partition_table").mkdir()
                (build / "partition_table/partition-table.bin").write_bytes(b"shared table")
                (build / "bootloader/bootloader.bin").write_bytes(b"bootloader")
                (build / "bootloader/config/sdkconfig.h").write_bytes(b"config header")
                config = build / "sdkconfig"
                config.write_bytes(b"sdkconfig")
                (build / "project_description.json").write_text(json.dumps({
                    "target": "esp32s3", "build_dir": str(build),
                    "app_bin": "app.bin", "config_file": str(config)}))
                (build / "app.bin").write_bytes((
                    f"WASMHOME_ISSUE22_BUILD_V2\nprofile={name}\n"
                    f"source_commit={'a' * 40}\nsource_tree={'c' * 40}\n"
                    "functional_timeout_ms=300\nserver_ipv4=192.0.2.8\nserver_port=18445\n"
                    "server_name=wasmhome-issue22.test\nupdate_server_ipv4=192.0.2.8\n"
                    "update_server_port=18445\nupdate_server_name=wasmhome-issue22.test\n"
                    f"ca_sha256={'d' * 64}\nsdkconfig_sha256={'e' * 64}\n"
                    "app_rollback_enabled=1\nEND_WASMHOME_ISSUE22_BUILD_V2\n\0").encode())
                builds[name] = build
            manifest = build_manifest(builds)
            self.assertEqual(manifest["source_commit"], "a" * 40)
            self.assertEqual(manifest["source_tree"], "c" * 40)
            with self.assertRaisesRegex(ValueError, "requested source commit differs"):
                build_manifest(builds, "b" * 40)
            app = builds["aot"] / "app.bin"
            app.write_bytes(app.read_bytes().replace(b"source_tree=" + b"c" * 40,
                                                     b"source_tree=" + b"b" * 40))
            with self.assertRaisesRegex(ValueError, "source identities"):
                build_manifest(builds)


if __name__ == "__main__":
    unittest.main()

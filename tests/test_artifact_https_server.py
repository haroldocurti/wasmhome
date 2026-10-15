import hashlib
import http.client
import json
import pathlib
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "tools"))
from artifact_https_server import ArtifactHTTPServer  # noqa: E402
from signed_artifacts import sign_set  # noqa: E402


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI is required")
class ArtifactHTTPSTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.private = self.root / "signing.key"
        self.public = self.root / "signing.pem"
        self._generate_ec_key(self.private)
        self.private.chmod(0o600)
        with self.public.open("wb") as output:
            subprocess.run(["openssl", "pkey", "-in", str(self.private), "-pubout"],
                           check=True, stdout=output, stderr=subprocess.PIPE)
        self._generate_package()
        self._generate_tls_identity()
        self.log_path = self.root / "server.jsonl"
        self.server = ArtifactHTTPServer(
            ("127.0.0.1", 0), self.package, self.certificate, self.tls_key,
            self.log_path, "localhost", "127.0.0.1", self.public,
            "issue15-fixture", "wamr-2.4.5-wasmhome.3",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.context = ssl.create_default_context(cafile=str(self.ca_certificate))
        self.base = f"https://localhost:{self.server.server_port}/issue15-fixture"

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
        self.temporary.cleanup()

    @staticmethod
    def _generate_ec_key(path):
        subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt",
                        "ec_paramgen_curve:prime256v1", "-out", str(path)],
                       check=True, capture_output=True)

    def _generate_package(self):
        entries = []
        for scenario, magic in ((1, b"\xe9"), (2, b"\x00asm\x01\x00\x00\x00"), (3, b"\x00aot")):
            for label, sequence in (("v1", 1), ("v2", 2)):
                payload_name = f"payload-{scenario}-{label}.bin"
                (self.root / payload_name).write_bytes(magic + f"{scenario}-{label}".encode())
                entries.append({"scenario": scenario, "label": label, "sequence": sequence,
                                "payload": payload_name})
        config = self.root / "input.json"
        config.write_text(json.dumps({
            "schema": "wasmhome-issue15-artifacts-v1", "campaign_id": "issue15-fixture",
            "aot_toolchain_id": "wamr-2.4.5-wasmhome.3", "artifacts": entries,
        }), encoding="utf-8")
        self.package = self.root / "package"
        sign_set(config, self.package, self.private)

    def _generate_tls_identity(self):
        self.ca_key = self.root / "ca.key"
        self.ca_certificate = self.root / "ca.crt"
        self.tls_key = self.root / "server.key"
        self.csr = self.root / "server.csr"
        self.certificate = self.root / "server.crt"
        for path in (self.ca_key, self.tls_key):
            self._generate_ec_key(path)
            path.chmod(0o600)
        subprocess.run([
            "openssl", "req", "-x509", "-key", str(self.ca_key), "-out", str(self.ca_certificate),
            "-days", "1", "-subj", "/CN=WasmHome Test CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        ], check=True, capture_output=True)
        subprocess.run([
            "openssl", "req", "-new", "-key", str(self.tls_key), "-out", str(self.csr),
            "-subj", "/CN=localhost",
        ], check=True, capture_output=True)
        extensions = self.root / "server.ext"
        extensions.write_text(
            "[server]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n",
            encoding="ascii",
        )
        subprocess.run([
            "openssl", "x509", "-req", "-in", str(self.csr), "-CA", str(self.ca_certificate),
            "-CAkey", str(self.ca_key), "-CAcreateserial", "-out", str(self.certificate),
            "-days", "1", "-extfile", str(extensions), "-extensions", "server",
        ], check=True, capture_output=True)

    def test_ca_and_hostname_verified_download_serves_exact_bytes(self):
        path = "/scenario-1/firmware/v1/firmware.bin"
        with urllib.request.urlopen(self.base + path, context=self.context, timeout=5) as response:
            payload = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["Cache-Control"], "public, max-age=31536000, immutable")
        expected = (self.package / "scenario-1/firmware/v1/firmware.bin").read_bytes()
        self.assertEqual(payload, expected)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), hashlib.sha256(expected).hexdigest())

    def test_configured_dns_host_survives_http_server_reverse_dns_assignment(self):
        server = ArtifactHTTPServer(
            ("127.0.0.1", 0), self.package, self.certificate, self.tls_key,
            self.log_path, "configured-artifact-name.test", "127.0.0.1", self.public,
            "issue15-fixture", "wamr-2.4.5-wasmhome.3",
        )
        try:
            self.assertTrue(server.host_header_allowed(
                f"configured-artifact-name.test:{server.server_port}"
            ))
        finally:
            server.server_close()

    def test_cose_media_type_identifies_sign1(self):
        path = "/scenario-1/firmware/v1/manifest.cose"
        with urllib.request.urlopen(self.base + path, context=self.context, timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"],
                             'application/cose; cose-type="cose-sign1"')

    def test_rejects_unknown_paths_and_misdirected_host(self):
        for path, host, status in (
            ("/missing", "localhost", 404),
            ("/scenario-1/firmware", "localhost", 404),
            ("/scenario-1/%2e%2e/index.json", "localhost", 404),
            ("/index.json", "not-localhost", 421),
            ("/index.json", "localhost:9999", 421),
            ("/index.json", "user@localhost", 421),
            ("/scenario-1/firmware/v1/package.json", "localhost", 404),
        ):
            request = urllib.request.Request(self.base + path, headers={"Host": host})
            with self.subTest(path=path, host=host), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, context=self.context, timeout=5)
            self.assertEqual(caught.exception.code, status)
            caught.exception.close()

        connection = http.client.HTTPSConnection(
            "localhost", self.server.server_port, context=self.context, timeout=5,
        )
        connection.request("GET", "https://attacker.invalid/issue15-fixture/index.json",
                           headers={"Host": "localhost"})
        response = connection.getresponse()
        self.assertEqual(response.status, 404)
        response.read()
        connection.request("GET", "//[broken", headers={"Host": "localhost"})
        response = connection.getresponse()
        self.assertEqual(response.status, 404)
        response.read()
        connection.close()

    def test_rejects_unsupported_methods_with_a_framed_405(self):
        for method in ("POST", "PUT"):
            request = urllib.request.Request(self.base + "/index.json", method=method)
            with self.subTest(method=method), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, context=self.context, timeout=5)
            error = caught.exception
            self.assertEqual(error.code, 405)
            self.assertEqual(error.headers["Allow"], "GET, HEAD")
            body = error.read()
            self.assertEqual(int(error.headers["Content-Length"]), len(body))
            self.assertEqual(body, b"method not allowed\n")
            error.close()
        events = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual([event["status"] for event in events], [405, 405])
        self.assertEqual([event["method"] for event in events], ["POST", "PUT"])

    def test_untrusted_ca_is_rejected(self):
        request = urllib.request.Request(self.base + "/index.json")
        with self.assertRaises(urllib.error.URLError) as caught:
            urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=5)
        self.assertIsInstance(caught.exception.reason, ssl.SSLCertVerificationError)

    def test_logs_capture_tls_and_content_hash(self):
        urllib.request.urlopen(self.base + "/index.json", context=self.context, timeout=5).read()
        events = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], 200)
        self.assertTrue(events[0]["complete"])
        self.assertIn(events[0]["tls_protocol"], {"TLSv1.2", "TLSv1.3"})
        self.assertEqual(events[0]["sha256"], hashlib.sha256((self.package / "index.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()

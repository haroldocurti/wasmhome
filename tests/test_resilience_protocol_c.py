import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class FirmwareFaultRequestProtocolTests(unittest.TestCase):
    def test_c_parser_accepts_only_closed_fixture_requests(self):
        with tempfile.TemporaryDirectory(prefix="wasmhome-resilience-parser-") as tmp:
            binary = pathlib.Path(tmp) / "resilience-protocol-test"
            subprocess.run(
                [
                    "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "main"),
                    str(ROOT / "main" / "resilience_test_protocol.c"),
                    str(ROOT / "tests" / "resilience_test_protocol_test.c"),
                    "-o", str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()

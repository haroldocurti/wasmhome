import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UartHeartbeatContractTests(unittest.TestCase):
    def test_common_runtime_emits_one_fixed_non_identifying_line(self):
        platform = (ROOT / "main/platform.c").read_text()
        marker = re.search(r'^#define UART_HEARTBEAT_RECORD "([^"]+)"$',
                           platform, re.MULTILINE)
        self.assertIsNotNone(marker)
        self.assertEqual(marker.group(1), r"WASMHOME_UART_HEARTBEAT,v1\n")

        task = re.search(r"static void uart_heartbeat_task\(void \*argument\)\n\{.*?\n\}",
                         platform, re.DOTALL)
        self.assertIsNotNone(task)
        body = task.group(0)
        self.assertIn("vTaskDelayUntil(&cycle, interval)", body)
        self.assertIn("fputs(UART_HEARTBEAT_RECORD, stdout)", body)
        self.assertIn("fflush(stdout)", body)
        self.assertNotRegex(body, r"\b(?:printf|snprintf)\s*\(")

        app = (ROOT / "main/main.c").read_text()
        self.assertIn("ESP_ERROR_CHECK(wh_platform_start())", app)

    def test_configured_interval_stays_inside_remote_freshness_budget(self):
        kconfig = (ROOT / "main/Kconfig.projbuild").read_text()
        option = re.search(
            r"config WASMHOME_UART_HEARTBEAT_INTERVAL_MS\n"
            r"(?P<body>(?:    .*\n)+)", kconfig)
        self.assertIsNotNone(option)
        self.assertIsNotNone(re.search(r"^    range 500 750$", option.group("body"),
                                       re.MULTILINE))
        self.assertIsNotNone(re.search(r"^    default 500$", option.group("body"),
                                       re.MULTILINE))


if __name__ == "__main__":
    unittest.main()

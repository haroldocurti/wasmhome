import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def extract_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


class ModuleRecoveryFieldMappingTests(unittest.TestCase):
    def test_serial_recovery_fields_match_their_timing_predicates(self):
        source = (ROOT / "main/module_resilience_test.c").read_text(encoding="utf-8")
        function = extract_function(source, "emit_recovery_if_complete(void)")

        telemetry_field = function.index(r'\"telemetry_within_timeout\":%s')
        control_field = function.index(r'\"control_recovery_within_30s\":%s')
        telemetry_value = function.index('telemetry_within_candidate ? "true" : "false"')
        control_value = function.index('within_30s ? "true" : "false"')

        self.assertLess(telemetry_field, control_field)
        self.assertLess(control_field, telemetry_value)
        self.assertLess(telemetry_value, control_value)

    def test_environment_acceptance_failure_clears_unaccepted_attempt_id(self):
        source = (ROOT / "main/module_resilience_test.c").read_text(encoding="utf-8")
        function = extract_function(source, "handle_environment_fixture(")
        self.assertLess(function.index("if (!trial->accepted)"),
                        function.index("wh_module_manager_clear_resilience_attempt_id"))
        self.assertLess(function.index("wh_module_manager_clear_resilience_attempt_id"),
                        function.index("emit_request_rejected"))

    def test_accepted_environment_failures_recover_only_after_health_check(self):
        source = (ROOT / "main/module_resilience_test.c").read_text(encoding="utf-8")
        function = extract_function(source, "recover_environment_attempt_after_health_check(")
        self.assertLess(function.index("wh_module_manager_health_check_active_control"),
                        function.index("mark_recovered_after_health_check"))
        handler = extract_function(source, "handle_environment_fixture(")
        queue_failure = handler.index("if (queue_err != ESP_OK)")
        queue_recovery = handler.index("recover_environment_attempt_after_health_check(trial)",
                                       queue_failure)
        self.assertLess(queue_failure, queue_recovery)
        unexpected_failure = handler.index("if (!result.expected_rejection)")
        unexpected_recovery = handler.index("recover_environment_attempt_after_health_check(trial)",
                                            unexpected_failure)
        self.assertLess(unexpected_failure, unexpected_recovery)

    def test_serial_evidence_failure_does_not_skip_environment_recovery(self):
        source = (ROOT / "main/module_resilience_test.c").read_text(encoding="utf-8")
        handler = extract_function(source, "handle_environment_fixture(")
        rejected_emit = handler.index('emit_event_for_pending("update_rejected"')
        recovery = handler.index("recover_environment_attempt_after_health_check(trial)",
                                 rejected_emit)
        self.assertLess(rejected_emit, recovery)
        self.assertIn("ENV rejection is durable but serial evidence failed",
                      handler[rejected_emit:recovery])
        self.assertNotIn("return;", handler[rejected_emit:recovery])


if __name__ == "__main__":
    unittest.main()

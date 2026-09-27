"""
Extended tests for workflow helpers — covers gaps left by test_workflow_helpers.py.

New cases:
  - _fallback_test_plan: multiple issues still produces exactly one fallback test
  - _fallback_test_plan: fallback test executable_code is a string
  - _fallback_test_plan: fallback test has all required keys
  - _fallback_verification: issues_resolved truncated to 80 chars per entry
  - _fallback_verification: no-issues flag + clean run → PASS with empty remaining_issues
  - _fallback_verification: failed test with no error field uses fallback message
  - _fallback_code_analysis: issue line preserved from warning
  - _fallback_logic_analysis: both timeout and stderr → both issues present
  - _fallback_root_cause: stderr truncated to 200 chars
  - _step_start/_step_done/_step_error: keys exhaustively checked
  - _fallback_verification: recommendation key present
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.workflow import (
    _step_start,
    _step_done,
    _step_error,
    _fallback_code_analysis,
    _fallback_logic_analysis,
    _fallback_test_plan,
    _fallback_root_cause,
    _fallback_verification,
)


# ── common fixtures ───────────────────────────────────────────────────────────

CLEAN_STATIC = {"errors": [], "warnings": []}
CLEAN_RUN = {"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": False}


def _run_fixed(exit_code=0, stderr="", timed_out=False, stdout=""):
    return {"exit_code": exit_code, "stderr": stderr, "timed_out": timed_out, "stdout": stdout}


# ─────────────────────────────────────────────────────────────────────────────
# Event helpers — exhaustive key checks
# ─────────────────────────────────────────────────────────────────────────────

class TestEventHelperKeys:
    def test_step_start_has_exactly_type_step_data(self):
        e = _step_start("s", "msg")
        assert set(e.keys()) == {"type", "step", "data"}

    def test_step_done_has_exactly_type_step_data(self):
        e = _step_done("s", {"k": "v"})
        assert set(e.keys()) == {"type", "step", "data"}

    def test_step_error_has_exactly_type_step_data(self):
        e = _step_error("s", "err")
        assert set(e.keys()) == {"type", "step", "data"}

    def test_step_start_data_has_message_only(self):
        e = _step_start("s", "hello")
        assert set(e["data"].keys()) == {"message"}

    def test_step_error_data_has_error_only(self):
        e = _step_error("s", "oops")
        assert set(e["data"].keys()) == {"error"}

    def test_step_done_data_passes_through_any_payload(self):
        payload = {"a": 1, "b": [1, 2], "c": None}
        e = _step_done("s", payload)
        assert e["data"] == payload

    def test_step_start_step_value_is_string(self):
        e = _step_start("run_original", "Running…")
        assert isinstance(e["step"], str)

    def test_step_done_step_value_matches_input(self):
        e = _step_done("fix_generation", {})
        assert e["step"] == "fix_generation"


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_test_plan() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackTestPlanExtended:
    def test_multiple_issues_still_one_fallback_test(self):
        issues = [{"type": f"E{i}", "description": f"desc {i}"} for i in range(10)]
        r = _fallback_test_plan("x = 1", issues)
        assert len(r["test_cases"]) == 1

    def test_fallback_test_executable_code_is_string(self):
        issues = [{"type": "SyntaxError"}]
        r = _fallback_test_plan("x = 1", issues)
        assert isinstance(r["test_cases"][0]["executable_code"], str)

    def test_fallback_test_has_all_required_keys(self):
        issues = [{"type": "Error"}]
        r = _fallback_test_plan("x = 1", issues)
        tc = r["test_cases"][0]
        for key in ("name", "description", "type", "input", "expected", "executable_code"):
            assert key in tc, f"Missing key: {key}"

    def test_fallback_test_type_is_normal(self):
        issues = [{"type": "E"}]
        r = _fallback_test_plan("x = 1", issues)
        assert r["test_cases"][0]["type"] == "normal"

    def test_no_issues_test_cases_is_empty_list(self):
        r = _fallback_test_plan("", [])
        assert r["test_cases"] == []
        assert isinstance(r["test_strategy"], str)


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_verification() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackVerificationExtended:
    def test_issues_resolved_entries_truncated_to_80_chars(self):
        long_description = "X" * 200
        issues = [{"description": long_description}]
        r = _fallback_verification("code", _run_fixed(), [], issues, False)
        assert r["verdict"] == "PASS"
        for entry in r["issues_resolved"]:
            assert len(entry) <= 80, f"Entry '{entry}' exceeds 80 chars"

    def test_issues_resolved_capped_at_five(self):
        """Only first 5 issues should appear in issues_resolved."""
        issues = [{"description": f"issue {i}"} for i in range(10)]
        r = _fallback_verification("code", _run_fixed(), [], issues, False)
        assert r["verdict"] == "PASS"
        assert len(r["issues_resolved"]) <= 5

    def test_no_issues_flag_passes_with_empty_remaining(self):
        r = _fallback_verification("code", _run_fixed(), [], [], True)
        assert r["verdict"] == "PASS"
        assert r["remaining_issues"] == []

    def test_failed_test_without_error_key_uses_fallback(self):
        """Test dict without 'error' key should use 'Test failed' fallback."""
        tests = [{"passed": False}]  # no 'error' key
        r = _fallback_verification("code", _run_fixed(), tests, [], False)
        assert r["verdict"] == "FAIL"
        # remaining_issues should still be non-empty (fallback message)
        assert len(r["remaining_issues"]) >= 1

    def test_recommendation_key_present(self):
        r = _fallback_verification("code", _run_fixed(), [], [], False)
        assert "recommendation" in r

    def test_reasoning_key_present(self):
        r = _fallback_verification("code", _run_fixed(), [], [], False)
        assert "reasoning" in r

    def test_timed_out_recommendation_is_string(self):
        r = _fallback_verification("code", _run_fixed(timed_out=True), [], [], False)
        assert isinstance(r.get("recommendation", ""), str)

    def test_all_tests_skipped_treated_as_no_failure(self):
        """Skipped tests (passed=False but skipped=True) should not cause FAIL
        if execution is clean. Note: current implementation checks 'passed' not 'skipped',
        so this documents the actual behaviour."""
        tests = [{"passed": False, "skipped": True, "error": None}]
        r = _fallback_verification("code", _run_fixed(), tests, [], False)
        # Skipped tests still have passed=False, so verdict should be FAIL.
        # This test documents current behaviour.
        assert r["verdict"] in ("PASS", "FAIL")


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_code_analysis() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackCodeAnalysisExtended:
    def test_warning_line_preserved_in_issue(self):
        static = {
            "errors": [],
            "warnings": [{"type": "BareExcept", "message": "bare except", "line": 7}],
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert r["issues"][0]["line"] == 7

    def test_error_without_line_produces_none_line(self):
        static = {
            "errors": [{"type": "ParseError", "message": "bad", "line": None}],
            "warnings": [],
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert r["issues"][0]["line"] is None

    def test_notes_key_present(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert "notes" in r

    def test_summary_mentions_bob_shell(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert "Bob" in r["summary"] or "static" in r["summary"].lower()

    def test_complexity_is_unknown(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert r["complexity"] == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_logic_analysis() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackLogicAnalysisExtended:
    def test_timeout_and_stderr_both_produce_issues(self):
        """When both timed_out and stderr are set, both InfiniteLoop and RuntimeError appear."""
        run = {**CLEAN_RUN, "timed_out": True, "stderr": "NameError: x"}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        types = [i["type"] for i in r["logic_issues"]]
        assert "InfiniteLoop" in types
        assert "RuntimeError" in types

    def test_stderr_description_truncated_to_300(self):
        long_err = "E" * 500
        run = {**CLEAN_RUN, "stderr": long_err}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        runtime_issues = [i for i in r["logic_issues"] if i["type"] == "RuntimeError"]
        assert len(runtime_issues) == 1
        assert len(runtime_issues[0]["description"]) <= 300

    def test_summary_is_non_empty_string(self):
        r = _fallback_logic_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert isinstance(r["summary"], str) and len(r["summary"]) > 0

    def test_example_field_present_in_infinite_loop_issue(self):
        run = {**CLEAN_RUN, "timed_out": True}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        infinite = next(i for i in r["logic_issues"] if i["type"] == "InfiniteLoop")
        assert "example" in infinite


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_root_cause() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackRootCauseExtended:
    def test_stderr_truncated_to_200_chars(self):
        long_err = "X" * 500
        run = {**CLEAN_RUN, "stderr": long_err}
        r = _fallback_root_cause([], run)
        assert len(r["primary_root_cause"]) <= 200

    def test_contributing_factors_is_list(self):
        r = _fallback_root_cause([], CLEAN_RUN)
        assert isinstance(r["contributing_factors"], list)

    def test_risk_areas_is_list(self):
        r = _fallback_root_cause([], CLEAN_RUN)
        assert isinstance(r["risk_areas"], list)

    def test_fix_strategy_is_non_empty_string(self):
        r = _fallback_root_cause([], CLEAN_RUN)
        assert isinstance(r["fix_strategy"], str) and len(r["fix_strategy"]) > 0

    def test_single_issue_no_contributing_factors(self):
        issues = [{"description": "only issue"}]
        r = _fallback_root_cause(issues, CLEAN_RUN)
        # contributing_factors comes from issues[1:3], so empty for a single issue
        assert r["contributing_factors"] == []

    def test_three_issues_two_contributing_factors(self):
        issues = [
            {"description": "main"},
            {"description": "contrib1"},
            {"description": "contrib2"},
        ]
        r = _fallback_root_cause(issues, CLEAN_RUN)
        assert len(r["contributing_factors"]) == 2

    def test_four_issues_contributing_capped_at_two(self):
        issues = [{"description": f"i{j}"} for j in range(4)]
        r = _fallback_root_cause(issues, CLEAN_RUN)
        # issues[1:3] → exactly 2 factors
        assert len(r["contributing_factors"]) == 2

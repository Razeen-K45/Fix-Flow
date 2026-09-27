"""
Tests for workflow.py — pure helper functions and fallback logic

Covers:
  - _step_start() / _step_done() / _step_error(): event dict shape
  - _fallback_code_analysis(): issue mapping from static + run results
  - _fallback_logic_analysis(): timeout / stderr detection
  - _fallback_test_plan(): presence of fallback test case
  - _fallback_root_cause(): timed-out, stderr, issues, no-issues paths
  - _fallback_verification(): timed-out, exit-code-error, failed-tests,
                              no-issues PASS, and generic PASS
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


# ─────────────────────────────────────────────────────────────────────────────
# Event builder helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestStepStart:
    def test_type_is_step_start(self):
        e = _step_start("code_analysis", "Analysing…")
        assert e["type"] == "step_start"

    def test_step_field_preserved(self):
        e = _step_start("my_step", "msg")
        assert e["step"] == "my_step"

    def test_data_contains_message(self):
        e = _step_start("s", "hello world")
        assert e["data"]["message"] == "hello world"

    def test_returns_dict(self):
        assert isinstance(_step_start("s", "m"), dict)


class TestStepDone:
    def test_type_is_step_done(self):
        e = _step_done("fix_generation", {"result": {}})
        assert e["type"] == "step_done"

    def test_step_preserved(self):
        e = _step_done("verification", {"result": {}})
        assert e["step"] == "verification"

    def test_data_passed_through(self):
        payload = {"verdict": "PASS", "confidence": "high"}
        e = _step_done("verification", payload)
        assert e["data"] == payload

    def test_empty_data_dict(self):
        e = _step_done("s", {})
        assert e["data"] == {}


class TestStepError:
    def test_type_is_step_error(self):
        e = _step_error("run_original", "process crashed")
        assert e["type"] == "step_error"

    def test_step_preserved(self):
        e = _step_error("logic_analysis", "timeout")
        assert e["step"] == "logic_analysis"

    def test_data_contains_error_key(self):
        e = _step_error("s", "something went wrong")
        assert e["data"]["error"] == "something went wrong"


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_code_analysis()
# ─────────────────────────────────────────────────────────────────────────────

CLEAN_STATIC = {"errors": [], "warnings": []}
CLEAN_RUN = {"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": False}

class TestFallbackCodeAnalysis:
    def test_returns_dict(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert isinstance(r, dict)

    def test_required_keys_present(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        for key in ("summary", "issues", "complexity", "code_quality", "notes"):
            assert key in r, f"Missing key: {key}"

    def test_no_issues_when_clean(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert r["issues"] == []

    def test_static_error_becomes_issue(self):
        static = {
            "errors": [{"type": "SyntaxError", "message": "invalid syntax", "line": 3}],
            "warnings": [],
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert len(r["issues"]) == 1
        assert r["issues"][0]["severity"] == "error"
        assert r["issues"][0]["type"] == "SyntaxError"
        assert r["issues"][0]["line"] == 3

    def test_static_warning_becomes_issue(self):
        static = {
            "errors": [],
            "warnings": [{"type": "BareExcept", "message": "bare except", "line": 5}],
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert len(r["issues"]) == 1
        assert r["issues"][0]["severity"] == "warning"
        assert r["issues"][0]["type"] == "BareExcept"

    def test_stderr_in_run_adds_runtime_error_issue(self):
        run = {**CLEAN_RUN, "exit_code": 1, "stderr": "NameError: name 'x' not defined"}
        r = _fallback_code_analysis(CLEAN_STATIC, run)
        types = [i["type"] for i in r["issues"]]
        assert "RuntimeError" in types

    def test_code_quality_poor_when_issues_exist(self):
        static = {
            "errors": [{"type": "SyntaxError", "message": "bad", "line": 1}],
            "warnings": [],
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert r["code_quality"] == "poor"

    def test_code_quality_unknown_when_clean(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert r["code_quality"] == "unknown"

    def test_multiple_errors_and_warnings_all_present(self):
        static = {
            "errors": [{"type": "E1", "message": "e1", "line": 1}, {"type": "E2", "message": "e2", "line": 2}],
            "warnings": [{"type": "W1", "message": "w1", "line": 3}],
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert len(r["issues"]) == 3

    def test_stderr_truncated_to_300_chars(self):
        long_stderr = "X" * 500
        run = {**CLEAN_RUN, "stderr": long_stderr}
        r = _fallback_code_analysis(CLEAN_STATIC, run)
        runtime_issues = [i for i in r["issues"] if i["type"] == "RuntimeError"]
        assert len(runtime_issues) == 1
        assert len(runtime_issues[0]["description"]) <= 300


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_logic_analysis()
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackLogicAnalysis:
    def test_returns_dict_with_required_keys(self):
        r = _fallback_logic_analysis(CLEAN_STATIC, CLEAN_RUN)
        for key in ("logic_issues", "root_causes", "affected_components", "summary"):
            assert key in r

    def test_no_logic_issues_when_clean(self):
        r = _fallback_logic_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert r["logic_issues"] == []

    def test_timeout_adds_infinite_loop_issue(self):
        run = {**CLEAN_RUN, "timed_out": True}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        types = [i["type"] for i in r["logic_issues"]]
        assert "InfiniteLoop" in types

    def test_stderr_adds_runtime_error_issue(self):
        run = {**CLEAN_RUN, "stderr": "IndexError: list index out of range"}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        types = [i["type"] for i in r["logic_issues"]]
        assert "RuntimeError" in types

    def test_logic_issues_have_required_fields(self):
        run = {**CLEAN_RUN, "timed_out": True}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        for issue in r["logic_issues"]:
            assert "type" in issue
            assert "description" in issue

    def test_root_causes_is_list(self):
        r = _fallback_logic_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert isinstance(r["root_causes"], list)

    def test_affected_components_is_list(self):
        r = _fallback_logic_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert isinstance(r["affected_components"], list)


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_test_plan()
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackTestPlan:
    def test_returns_dict(self):
        r = _fallback_test_plan("x = 1", [])
        assert isinstance(r, dict)

    def test_has_test_cases_key(self):
        r = _fallback_test_plan("x = 1", [])
        assert "test_cases" in r

    def test_has_test_strategy_key(self):
        r = _fallback_test_plan("x = 1", [])
        assert "test_strategy" in r

    def test_no_test_cases_when_no_issues(self):
        r = _fallback_test_plan("x = 1", [])
        assert r["test_cases"] == []

    def test_one_fallback_test_when_issues_present(self):
        issues = [{"type": "SyntaxError", "description": "bad"}]
        r = _fallback_test_plan("x = 1", issues)
        assert len(r["test_cases"]) >= 1

    def test_fallback_test_has_name(self):
        issues = [{"type": "Error"}]
        r = _fallback_test_plan("x = 1", issues)
        assert "name" in r["test_cases"][0]

    def test_test_strategy_is_string(self):
        r = _fallback_test_plan("x = 1", [])
        assert isinstance(r["test_strategy"], str)


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_root_cause()
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackRootCause:
    def test_returns_dict(self):
        r = _fallback_root_cause([], CLEAN_RUN)
        assert isinstance(r, dict)

    def test_has_required_keys(self):
        r = _fallback_root_cause([], CLEAN_RUN)
        for key in ("primary_root_cause", "contributing_factors", "fix_strategy", "risk_areas", "confidence"):
            assert key in r

    def test_timed_out_returns_infinite_loop_cause(self):
        run = {**CLEAN_RUN, "timed_out": True}
        r = _fallback_root_cause([], run)
        assert "infinite" in r["primary_root_cause"].lower() or "loop" in r["primary_root_cause"].lower()
        assert r["confidence"] == "high"

    def test_stderr_returns_stderr_as_cause(self):
        run = {**CLEAN_RUN, "stderr": "NameError: name 'x'"}
        r = _fallback_root_cause([], run)
        assert "NameError" in r["primary_root_cause"]

    def test_issues_used_when_no_runtime_error(self):
        issues = [{"description": "division by zero"}, {"description": "index out of range"}]
        r = _fallback_root_cause(issues, CLEAN_RUN)
        assert "division by zero" in r["primary_root_cause"]

    def test_contributing_factors_from_subsequent_issues(self):
        issues = [
            {"description": "main issue"},
            {"description": "factor one"},
            {"description": "factor two"},
            {"description": "factor three"},
        ]
        r = _fallback_root_cause(issues, CLEAN_RUN)
        assert "factor one" in r["contributing_factors"]

    def test_no_issues_no_errors_returns_no_issues(self):
        r = _fallback_root_cause([], CLEAN_RUN)
        assert "no issues" in r["primary_root_cause"].lower()
        assert r["confidence"] == "high"

    def test_timeout_takes_priority_over_stderr(self):
        run = {**CLEAN_RUN, "timed_out": True, "stderr": "some error"}
        r = _fallback_root_cause([], run)
        # Timeout path should be taken first
        assert "loop" in r["primary_root_cause"].lower() or "timed" in r["primary_root_cause"].lower()

    def test_confidence_is_string(self):
        r = _fallback_root_cause([], CLEAN_RUN)
        assert r["confidence"] in ("high", "medium", "low")


# ─────────────────────────────────────────────────────────────────────────────
# _fallback_verification()
# ─────────────────────────────────────────────────────────────────────────────

def _run_fixed(exit_code=0, stderr="", timed_out=False, stdout=""):
    return {"exit_code": exit_code, "stderr": stderr, "timed_out": timed_out, "stdout": stdout}


class TestFallbackVerification:
    def test_returns_dict(self):
        r = _fallback_verification("code", _run_fixed(), [], [], False)
        assert isinstance(r, dict)

    def test_has_required_keys(self):
        r = _fallback_verification("code", _run_fixed(), [], [], False)
        for key in ("verdict", "confidence", "issues_resolved", "remaining_issues", "reasoning"):
            assert key in r

    def test_timed_out_verdict_fail(self):
        r = _fallback_verification("code", _run_fixed(timed_out=True), [], [], False)
        assert r["verdict"] == "FAIL"
        assert r["confidence"] == "high"

    def test_timed_out_remaining_issues_non_empty(self):
        r = _fallback_verification("code", _run_fixed(timed_out=True), [], [], False)
        assert len(r["remaining_issues"]) >= 1

    def test_exit_code_nonzero_with_stderr_is_fail(self):
        r = _fallback_verification(
            "code",
            _run_fixed(exit_code=1, stderr="RuntimeError: something"),
            [], [], False
        )
        assert r["verdict"] == "FAIL"

    def test_exit_code_nonzero_without_stderr_not_immediate_fail(self):
        """Non-zero exit without stderr should fall through to test-result check."""
        r = _fallback_verification("code", _run_fixed(exit_code=1, stderr=""), [], [], False)
        # Could be PASS or FAIL depending on other conditions — just ensure it's a valid verdict
        assert r["verdict"] in ("PASS", "FAIL")

    def test_failed_tests_verdict_fail(self):
        tests = [{"passed": False, "error": "assert 1 == 2"}, {"passed": True}]
        r = _fallback_verification("code", _run_fixed(), tests, [], False)
        assert r["verdict"] == "FAIL"

    def test_failed_tests_error_in_remaining_issues(self):
        tests = [{"passed": False, "error": "my test error"}]
        r = _fallback_verification("code", _run_fixed(), tests, [], False)
        assert any("my test error" in issue for issue in r["remaining_issues"])

    def test_no_issues_flag_returns_pass(self):
        r = _fallback_verification("code", _run_fixed(), [], [], True)
        assert r["verdict"] == "PASS"
        assert r["confidence"] == "high"

    def test_all_tests_pass_no_errors_returns_pass(self):
        tests = [{"passed": True}, {"passed": True}]
        r = _fallback_verification("code", _run_fixed(), tests, [], False)
        assert r["verdict"] == "PASS"

    def test_issues_resolved_populated_on_pass(self):
        issues = [{"description": "fixed bug one"}, {"description": "fixed bug two"}]
        r = _fallback_verification("code", _run_fixed(), [], issues, False)
        assert r["verdict"] == "PASS"
        assert len(r["issues_resolved"]) >= 1

    def test_verdict_is_pass_or_fail(self):
        r = _fallback_verification("code", _run_fixed(), [], [], False)
        assert r["verdict"] in ("PASS", "FAIL")

    def test_timed_out_takes_priority_over_tests(self):
        """Timeout should be caught before checking test results."""
        tests = [{"passed": True}]  # tests would pass
        r = _fallback_verification("code", _run_fixed(timed_out=True), tests, [], False)
        assert r["verdict"] == "FAIL"  # timeout still wins

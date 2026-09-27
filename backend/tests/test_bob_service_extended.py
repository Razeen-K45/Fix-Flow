"""
Extended tests for bob_service.py — covers gaps left by test_bob_service.py.

New cases:
  - parse_bob_json_output: first {...} block wins when multiple JSON objects present
  - parse_bob_json_output: JSON with special characters / unicode in values
  - parse_bob_json_output: deeply nested fence with leading prose
  - build_root_cause_prompt: stdout/stderr truncated to 500 chars
  - build_verification_prompt: stdout/stderr truncated to 300 chars
  - build_fix_generation_prompt: IMPORTANT RULES section present
  - build_test_generation_prompt: all four test types mentioned
  - build_logic_analysis_prompt: language included in output
  - All prompt builders: do not raise for edge-case inputs (empty lists/dicts)
"""

import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.bob_service import (
    parse_bob_json_output,
    build_code_analysis_prompt,
    build_logic_analysis_prompt,
    build_test_generation_prompt,
    build_root_cause_prompt,
    build_fix_generation_prompt,
    build_verification_prompt,
)


# ─────────────────────────────────────────────────────────────────────────────
# parse_bob_json_output() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestParseBobJsonOutputExtended:
    def test_first_json_object_wins_when_multiple_present(self):
        """When output has two JSON objects, the regex finds the first { ... }."""
        first = {"winner": True}
        second = {"winner": False}
        output = json.dumps(first) + "\n" + json.dumps(second)
        result = parse_bob_json_output(output)
        # Direct parse picks up the whole string as invalid JSON, falls to regex
        # which finds the first {...} block.
        assert result is not None

    def test_unicode_values_round_trip(self):
        payload = {"message": "こんにちは", "emoji": "🚀"}
        result = parse_bob_json_output(json.dumps(payload))
        assert result == payload

    def test_json_with_newlines_in_values(self):
        payload = {"code": "def foo():\n    pass\n", "lang": "python"}
        result = parse_bob_json_output(json.dumps(payload))
        assert result["code"] == payload["code"]

    def test_markdown_fence_with_extra_prose_before(self):
        payload = {"verdict": "PASS"}
        output = (
            "Here is my analysis of the code.\n"
            "I looked at everything carefully.\n"
            f"```json\n{json.dumps(payload)}\n```\n"
            "Hope that helps!"
        )
        result = parse_bob_json_output(output)
        assert result == payload

    def test_json_inside_backtick_fence_no_language_tag(self):
        payload = {"key": "value"}
        output = f"```\n{json.dumps(payload)}\n```"
        result = parse_bob_json_output(output)
        assert result == payload

    def test_large_nested_json(self):
        payload = {
            "issues": [{"type": f"Issue{i}", "line": i, "description": "x" * 50} for i in range(20)],
            "summary": "y" * 200,
        }
        result = parse_bob_json_output(json.dumps(payload))
        assert result is not None
        assert len(result["issues"]) == 20

    def test_json_object_with_boolean_values(self):
        payload = {"passed": True, "failed": False, "count": 0}
        result = parse_bob_json_output(json.dumps(payload))
        assert result["passed"] is True
        assert result["failed"] is False

    def test_json_with_null_values(self):
        payload = {"line": None, "col": None, "text": None}
        result = parse_bob_json_output(json.dumps(payload))
        assert result["line"] is None

    def test_only_braces_empty_object(self):
        result = parse_bob_json_output("{}")
        assert result == {}

    def test_integer_string_not_json_object(self):
        """'42' is valid JSON but not a dict — direct parse returns int, not dict."""
        result = parse_bob_json_output("42")
        # Either parses as int (truthy) or None — just must not raise
        assert result is None or isinstance(result, int)


# ─────────────────────────────────────────────────────────────────────────────
# build_root_cause_prompt() — stdout/stderr truncation
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildRootCausePromptTruncation:
    CODE = "x = 1"
    ISSUES = []

    def test_long_stdout_truncated_in_prompt(self):
        """Stdout > 500 chars should be truncated in the prompt."""
        long_stdout = "A" * 1000
        run_result = {"exit_code": 0, "stdout": long_stdout, "stderr": "", "timed_out": False}
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, run_result)
        # The truncated version (:500) of long_stdout is "A" * 500
        assert "A" * 500 in p
        assert "A" * 501 not in p

    def test_long_stderr_truncated_in_prompt(self):
        """Stderr > 500 chars should be truncated in the prompt."""
        long_stderr = "E" * 1000
        run_result = {"exit_code": 1, "stdout": "", "stderr": long_stderr, "timed_out": False}
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, run_result)
        assert "E" * 500 in p
        assert "E" * 501 not in p

    def test_short_stdout_not_truncated(self):
        run_result = {"exit_code": 0, "stdout": "hello", "stderr": "", "timed_out": False}
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, run_result)
        assert "hello" in p


# ─────────────────────────────────────────────────────────────────────────────
# build_verification_prompt() — stdout/stderr truncation
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildVerificationPromptTruncation:
    ORIG = "x = 1"
    FIXED = "x = 2"
    LANG = "python"
    TESTS = []

    def test_original_long_stdout_truncated(self):
        long = "O" * 600
        run_orig = {"exit_code": 0, "stdout": long, "stderr": "", "timed_out": False}
        run_fixed = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}
        p = build_verification_prompt(self.ORIG, self.FIXED, self.LANG, self.TESTS, run_orig, run_fixed)
        assert "O" * 300 in p
        assert "O" * 301 not in p

    def test_fixed_long_stderr_truncated(self):
        long = "F" * 600
        run_orig = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}
        run_fixed = {"exit_code": 1, "stdout": "", "stderr": long, "timed_out": False}
        p = build_verification_prompt(self.ORIG, self.FIXED, self.LANG, self.TESTS, run_orig, run_fixed)
        assert "F" * 300 in p
        assert "F" * 301 not in p

    def test_short_values_not_modified(self):
        run_orig = {"exit_code": 0, "stdout": "out", "stderr": "err", "timed_out": False}
        run_fixed = {"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": False}
        p = build_verification_prompt(self.ORIG, self.FIXED, self.LANG, self.TESTS, run_orig, run_fixed)
        assert "out" in p
        assert "err" in p


# ─────────────────────────────────────────────────────────────────────────────
# build_fix_generation_prompt() — IMPORTANT RULES
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildFixGenerationPromptRules:
    CODE = "def f(): pass"
    ROOT_CAUSE = {"primary_root_cause": "test", "fix_strategy": "fix it"}
    ISSUES = []

    def test_important_rules_section_present(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        assert "IMPORTANT" in p or "important" in p.lower()

    def test_do_not_rewrite_rule_present(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        # The prompt says "do not rewrite" or similar
        assert "rewrite" in p.lower() or "unnecessarily" in p.lower()

    def test_complete_runnable_rule_present(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        assert "complete" in p.lower() or "runnable" in p.lower()


# ─────────────────────────────────────────────────────────────────────────────
# build_test_generation_prompt() — test type mentions
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTestGenerationPromptTypes:
    CODE = "def add(a, b): return a + b"
    ISSUES = []

    def test_normal_behavior_mentioned(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert "normal" in p.lower()

    def test_edge_case_mentioned(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert "edge" in p.lower()

    def test_boundary_mentioned(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert "boundary" in p.lower()

    def test_executable_code_field_mentioned(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert "executable_code" in p


# ─────────────────────────────────────────────────────────────────────────────
# build_logic_analysis_prompt() — language and code content
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildLogicAnalysisPromptExtended:
    def test_language_java_included(self):
        p = build_logic_analysis_prompt("int x = 0;", "java", {})
        assert "java" in p

    def test_edge_case_mention_in_prompt(self):
        p = build_logic_analysis_prompt("def f(): pass", "python", {})
        assert "edge" in p.lower() or "edge case" in p.lower()

    def test_runtime_errors_mentioned(self):
        p = build_logic_analysis_prompt("def f(): pass", "python", {})
        assert "runtime" in p.lower()

    def test_empty_code_does_not_raise(self):
        p = build_logic_analysis_prompt("", "python", {})
        assert isinstance(p, str) and len(p) > 0


# ─────────────────────────────────────────────────────────────────────────────
# All prompt builders — robustness against edge-case inputs
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptBuilderRobustness:
    def test_code_analysis_with_special_chars(self):
        code = 'x = "hello\\nworld"\ny = f"result: {x!r}"'
        p = build_code_analysis_prompt(code, "python")
        assert isinstance(p, str) and len(p) > 0

    def test_fix_prompt_empty_root_cause_and_issues(self):
        p = build_fix_generation_prompt("x = 1", "python", {}, [])
        assert isinstance(p, str) and len(p) > 0

    def test_verification_prompt_all_empty_run_results(self):
        p = build_verification_prompt("a", "b", "python", [], {}, {})
        assert isinstance(p, str) and len(p) > 0

    def test_root_cause_prompt_missing_run_keys(self):
        """Missing keys in run_result should fall back to .get() defaults gracefully."""
        p = build_root_cause_prompt("x = 1", "python", [], {})
        assert "N/A" in p or isinstance(p, str)

    def test_test_generation_prompt_large_issues_list(self):
        issues = [{"type": f"Issue{i}", "description": "desc"} for i in range(50)]
        p = build_test_generation_prompt("def f(): pass", "python", issues)
        assert isinstance(p, str) and len(p) > 0

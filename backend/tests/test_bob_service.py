"""
Tests for bob_service.py

Covers:
  - parse_bob_json_output(): clean JSON, markdown-fenced JSON,
    JSON embedded in prose, invalid input, None/empty
  - build_code_analysis_prompt(): structure & required keys
  - build_logic_analysis_prompt(): static issues injection
  - build_test_generation_prompt(): issues list injection
  - build_root_cause_prompt(): run_result fields injection
  - build_fix_generation_prompt(): root cause and issues injection
  - build_verification_prompt(): both code blocks and execution results
"""

import pytest
import json
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
# parse_bob_json_output()
# ─────────────────────────────────────────────────────────────────────────────

class TestParseBobJsonOutput:

    # ── happy paths ──────────────────────────────────────────────────────────

    def test_clean_json_object(self):
        data = {"verdict": "PASS", "confidence": "high"}
        result = parse_bob_json_output(json.dumps(data))
        assert result == data

    def test_clean_json_with_array(self):
        data = {"issues": [{"line": 1, "type": "SyntaxError"}]}
        result = parse_bob_json_output(json.dumps(data))
        assert result["issues"][0]["type"] == "SyntaxError"

    def test_markdown_json_fence(self):
        payload = {"status": "ok"}
        output = f"```json\n{json.dumps(payload)}\n```"
        result = parse_bob_json_output(output)
        assert result == payload

    def test_markdown_generic_fence(self):
        payload = {"key": "value"}
        output = f"```\n{json.dumps(payload)}\n```"
        result = parse_bob_json_output(output)
        assert result == payload

    def test_json_embedded_in_prose(self):
        payload = {"root_cause": "null pointer"}
        output = f'Here is my analysis:\n{json.dumps(payload)}\nEnd of analysis.'
        result = parse_bob_json_output(output)
        assert result == payload

    def test_json_with_leading_trailing_whitespace(self):
        payload = {"a": 1}
        result = parse_bob_json_output(f"  \n{json.dumps(payload)}\n  ")
        assert result == payload

    def test_nested_json_object(self):
        payload = {"outer": {"inner": [1, 2, 3]}}
        result = parse_bob_json_output(json.dumps(payload))
        assert result["outer"]["inner"] == [1, 2, 3]

    # ── failure paths ─────────────────────────────────────────────────────────

    def test_empty_string_returns_none(self):
        assert parse_bob_json_output("") is None

    def test_none_input_returns_none(self):
        assert parse_bob_json_output(None) is None

    def test_plain_text_no_json_returns_none(self):
        assert parse_bob_json_output("This is just plain text, no JSON here.") is None

    def test_broken_json_returns_none(self):
        assert parse_bob_json_output('{"key": "value"') is None  # unclosed

    def test_json_array_at_top_level(self):
        """Top-level arrays are valid JSON but the regex may or may not catch them."""
        output = '[1, 2, 3]'
        # Direct json.loads should succeed; result may be a list or None depending on regex
        result = parse_bob_json_output(output)
        # Either it parses as a list or returns None — both are acceptable
        assert result is None or isinstance(result, list)

    def test_markdown_fence_with_invalid_content(self):
        output = "```json\nNOT JSON\n```"
        result = parse_bob_json_output(output)
        assert result is None

    def test_whitespace_only_returns_none(self):
        assert parse_bob_json_output("   \n\t  ") is None


# ─────────────────────────────────────────────────────────────────────────────
# build_code_analysis_prompt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCodeAnalysisPrompt:
    SAMPLE_CODE = "x = 1\nprint(x)"

    def test_returns_string(self):
        p = build_code_analysis_prompt(self.SAMPLE_CODE, "python")
        assert isinstance(p, str) and len(p) > 0

    def test_code_included_in_prompt(self):
        p = build_code_analysis_prompt(self.SAMPLE_CODE, "python")
        assert "x = 1" in p
        assert "print(x)" in p

    def test_language_included(self):
        p = build_code_analysis_prompt(self.SAMPLE_CODE, "python")
        assert "python" in p

    def test_json_keys_mentioned(self):
        p = build_code_analysis_prompt(self.SAMPLE_CODE, "python")
        for key in ("summary", "issues", "complexity", "code_quality"):
            assert key in p, f"Expected key '{key}' in prompt"

    def test_json_only_instruction(self):
        p = build_code_analysis_prompt(self.SAMPLE_CODE, "python")
        assert "JSON" in p or "json" in p

    def test_different_language(self):
        p = build_code_analysis_prompt("int x = 0;", "java")
        assert "java" in p
        assert "int x = 0;" in p

    def test_prompt_is_non_empty_for_empty_code(self):
        """Even with empty code the prompt structure should remain intact."""
        p = build_code_analysis_prompt("", "python")
        assert "summary" in p


# ─────────────────────────────────────────────────────────────────────────────
# build_logic_analysis_prompt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildLogicAnalysisPrompt:
    SAMPLE_CODE = "def divide(a, b):\n    return a / b"
    STATIC = {
        "errors": [{"type": "SyntaxError", "message": "bad syntax", "line": 1}],
        "warnings": [{"type": "BareExcept", "message": "bare except", "line": 3}],
    }

    def test_returns_string(self):
        p = build_logic_analysis_prompt(self.SAMPLE_CODE, "python", self.STATIC)
        assert isinstance(p, str) and len(p) > 0

    def test_code_included(self):
        p = build_logic_analysis_prompt(self.SAMPLE_CODE, "python", self.STATIC)
        assert "divide" in p

    def test_static_issues_serialised(self):
        p = build_logic_analysis_prompt(self.SAMPLE_CODE, "python", self.STATIC)
        assert "SyntaxError" in p
        assert "BareExcept" in p

    def test_json_keys_mentioned(self):
        p = build_logic_analysis_prompt(self.SAMPLE_CODE, "python", self.STATIC)
        for key in ("logic_issues", "root_causes", "affected_components", "summary"):
            assert key in p

    def test_empty_static_analysis(self):
        p = build_logic_analysis_prompt(self.SAMPLE_CODE, "python", {})
        assert "logic_issues" in p


# ─────────────────────────────────────────────────────────────────────────────
# build_test_generation_prompt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTestGenerationPrompt:
    CODE = "def add(a, b):\n    return a + b"
    ISSUES = [
        {"type": "LogicError", "description": "division by zero"},
        {"type": "TypeError", "description": "wrong type"},
    ]

    def test_returns_string(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert isinstance(p, str)

    def test_code_included(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert "def add" in p

    def test_issues_serialised(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert "LogicError" in p
        assert "TypeError" in p

    def test_json_keys_present(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        for key in ("test_cases", "test_strategy", "name", "executable_code"):
            assert key in p

    def test_empty_issues(self):
        p = build_test_generation_prompt(self.CODE, "python", [])
        assert "test_cases" in p


# ─────────────────────────────────────────────────────────────────────────────
# build_root_cause_prompt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildRootCausePrompt:
    CODE = "x = 1 / 0"
    ISSUES = [{"type": "ZeroDivisionError", "description": "division by zero"}]
    RUN_RESULT = {
        "exit_code": 1,
        "stdout": "",
        "stderr": "ZeroDivisionError: division by zero",
        "timed_out": False,
    }

    def test_returns_string(self):
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, self.RUN_RESULT)
        assert isinstance(p, str)

    def test_code_included(self):
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, self.RUN_RESULT)
        assert "x = 1 / 0" in p

    def test_run_result_fields_included(self):
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, self.RUN_RESULT)
        assert "ZeroDivisionError" in p
        assert "1" in p  # exit_code

    def test_timed_out_flag_included(self):
        run = {**self.RUN_RESULT, "timed_out": True}
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, run)
        assert "True" in p

    def test_json_keys_mentioned(self):
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, self.RUN_RESULT)
        for key in ("primary_root_cause", "fix_strategy", "confidence"):
            assert key in p

    def test_empty_run_result(self):
        """Should not raise even with missing keys in run_result."""
        p = build_root_cause_prompt(self.CODE, "python", [], {})
        assert isinstance(p, str)


# ─────────────────────────────────────────────────────────────────────────────
# build_fix_generation_prompt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildFixGenerationPrompt:
    CODE = "def foo():\n    return 1/0"
    ROOT_CAUSE = {
        "primary_root_cause": "division by zero",
        "fix_strategy": "guard divisor",
        "confidence": "high",
    }
    ISSUES = [{"type": "ZeroDivisionError", "line": 2}]

    def test_returns_string(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        assert isinstance(p, str)

    def test_original_code_in_prompt(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        assert "def foo" in p

    def test_root_cause_serialised(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        assert "division by zero" in p
        assert "guard divisor" in p

    def test_issues_serialised(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        assert "ZeroDivisionError" in p

    def test_json_keys_present(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.ROOT_CAUSE, self.ISSUES)
        for key in ("fixed_code", "changes_made", "explanation", "confidence"):
            assert key in p

    def test_language_included(self):
        p = build_fix_generation_prompt(self.CODE, "java", self.ROOT_CAUSE, self.ISSUES)
        assert "java" in p


# ─────────────────────────────────────────────────────────────────────────────
# build_verification_prompt()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildVerificationPrompt:
    ORIG_CODE = "def bad(): return 1/0"
    FIXED_CODE = "def bad(): return 0"
    LANG = "python"
    TEST_RESULTS = [{"name": "t1", "passed": True}]
    RUN_ORIG = {"exit_code": 1, "stdout": "", "stderr": "ZeroDivisionError", "timed_out": False}
    RUN_FIXED = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}

    def test_returns_string(self):
        p = build_verification_prompt(
            self.ORIG_CODE, self.FIXED_CODE, self.LANG,
            self.TEST_RESULTS, self.RUN_ORIG, self.RUN_FIXED
        )
        assert isinstance(p, str)

    def test_both_code_blocks_present(self):
        p = build_verification_prompt(
            self.ORIG_CODE, self.FIXED_CODE, self.LANG,
            self.TEST_RESULTS, self.RUN_ORIG, self.RUN_FIXED
        )
        assert "def bad(): return 1/0" in p
        assert "def bad(): return 0" in p

    def test_run_results_embedded(self):
        p = build_verification_prompt(
            self.ORIG_CODE, self.FIXED_CODE, self.LANG,
            self.TEST_RESULTS, self.RUN_ORIG, self.RUN_FIXED
        )
        assert "ZeroDivisionError" in p

    def test_test_results_embedded(self):
        p = build_verification_prompt(
            self.ORIG_CODE, self.FIXED_CODE, self.LANG,
            self.TEST_RESULTS, self.RUN_ORIG, self.RUN_FIXED
        )
        assert "t1" in p

    def test_verdict_key_in_prompt(self):
        p = build_verification_prompt(
            self.ORIG_CODE, self.FIXED_CODE, self.LANG,
            self.TEST_RESULTS, self.RUN_ORIG, self.RUN_FIXED
        )
        assert "verdict" in p
        assert "PASS" in p or "FAIL" in p

    def test_language_included(self):
        p = build_verification_prompt(
            self.ORIG_CODE, self.FIXED_CODE, "java",
            self.TEST_RESULTS, self.RUN_ORIG, self.RUN_FIXED
        )
        assert "java" in p

    def test_empty_test_results(self):
        p = build_verification_prompt(
            self.ORIG_CODE, self.FIXED_CODE, self.LANG,
            [], self.RUN_ORIG, self.RUN_FIXED
        )
        assert "verdict" in p

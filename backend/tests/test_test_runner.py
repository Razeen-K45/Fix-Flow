"""
Tests for TestRunner (services/test_runner.py)

Covers:
  - _build_test_script(): script construction and namespace injection
  - _extract_error(): last-line extraction from stderr
  - TestRunner.run_tests(): empty list, skipped, passing, failing,
    assertion error, runtime error, and result shape
  - TestRunner._run_single_test(): placeholder skipping, comment-only skip
"""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.test_runner import TestRunner, _build_test_script, _extract_error


# ── helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def make_tc(name="test_one", description="desc", executable_code="assert True"):
    return {"name": name, "description": description, "executable_code": executable_code}


# ─────────────────────────────────────────────────────────────────────────────
# _build_test_script()
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTestScript:
    def test_returns_string(self):
        script = _build_test_script("x = 1", "assert x == 1")
        assert isinstance(script, str)

    def test_user_code_embedded(self):
        script = _build_test_script("MY_SENTINEL = 42", "assert MY_SENTINEL == 42")
        assert "MY_SENTINEL" in script

    def test_test_code_embedded(self):
        test_code = "assert result == 'ok'"
        script = _build_test_script("result = 'ok'", test_code)
        assert "result" in script

    def test_generated_script_is_runnable_and_passes(self):
        """The script for correct code + passing assert should exit 0."""
        import subprocess
        import tempfile

        script = _build_test_script("x = 7", "assert x == 7")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True, timeout=10
            )
            assert result.returncode == 0
        finally:
            os.unlink(tmp)

    def test_generated_script_fails_on_bad_assert(self):
        """An assert that will fail should produce exit code 1."""
        import subprocess
        import tempfile

        script = _build_test_script("x = 1", "assert x == 99")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True, timeout=10
            )
            assert result.returncode != 0
            assert "AssertionError" in result.stderr
        finally:
            os.unlink(tmp)

    def test_user_code_error_reported_to_stderr(self):
        """User code that raises should write USER CODE ERROR to stderr."""
        import subprocess
        import tempfile

        script = _build_test_script("raise RuntimeError('oops')", "assert True")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True, timeout=10
            )
            assert result.returncode != 0
            assert "USER CODE ERROR" in result.stderr
        finally:
            os.unlink(tmp)

    def test_function_defined_in_user_code_accessible_in_test(self):
        """Functions defined in user_code must be callable from test_code."""
        import subprocess
        import tempfile

        user_code = "def add(a, b):\n    return a + b"
        test_code = "assert add(2, 3) == 5"
        script = _build_test_script(user_code, test_code)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True, timeout=10
            )
            assert result.returncode == 0, result.stderr
        finally:
            os.unlink(tmp)


# ─────────────────────────────────────────────────────────────────────────────
# _extract_error()
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractError:
    def test_empty_string_returns_empty(self):
        assert _extract_error("") == ""

    def test_whitespace_only_returns_empty(self):
        assert _extract_error("   \n  \n  ") == ""

    def test_single_line(self):
        assert _extract_error("ValueError: bad input") == "ValueError: bad input"

    def test_returns_last_non_empty_line(self):
        stderr = "Traceback (most recent call last):\n  File 'x.py', line 1\nZeroDivisionError: division by zero"
        assert _extract_error(stderr) == "ZeroDivisionError: division by zero"

    def test_trailing_newlines_stripped(self):
        result = _extract_error("AssertionError: x != y\n\n")
        assert result == "AssertionError: x != y"

    def test_multiline_stderr(self):
        stderr = "line1\nline2\nfinal error"
        assert _extract_error(stderr) == "final error"


# ─────────────────────────────────────────────────────────────────────────────
# TestRunner.run_tests() — high-level
# ─────────────────────────────────────────────────────────────────────────────

class TestRunTestsHighLevel:
    def setup_method(self):
        self.runner = TestRunner()

    def test_empty_test_cases_returns_empty_list(self):
        results = run(self.runner.run_tests("x = 1", []))
        assert results == []

    def test_single_passing_test(self):
        tc = make_tc(executable_code="assert 1 + 1 == 2")
        results = run(self.runner.run_tests("", [tc]))
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["error"] is None

    def test_single_failing_test_assert(self):
        tc = make_tc(executable_code="assert 1 == 2, 'one is not two'")
        results = run(self.runner.run_tests("", [tc]))
        assert results[0]["passed"] is False
        assert results[0]["error"] is not None

    def test_result_shape_all_keys_present(self):
        tc = make_tc(executable_code="assert True")
        result = run(self.runner.run_tests("x = 1", [tc]))[0]
        for key in ("name", "description", "passed", "error", "stdout", "stderr", "skipped", "skip_reason"):
            assert key in result, f"Missing key: {key}"

    def test_name_and_description_preserved(self):
        tc = make_tc(name="my_test", description="checks stuff", executable_code="assert True")
        result = run(self.runner.run_tests("", [tc]))[0]
        assert result["name"] == "my_test"
        assert result["description"] == "checks stuff"

    def test_multiple_tests_all_run(self):
        tcs = [
            make_tc(name="t1", executable_code="assert True"),
            make_tc(name="t2", executable_code="assert 2 * 2 == 4"),
            make_tc(name="t3", executable_code="assert 'hello'.upper() == 'HELLO'"),
        ]
        results = run(self.runner.run_tests("", tcs))
        assert len(results) == 3
        assert all(r["passed"] for r in results)

    def test_one_failing_one_passing(self):
        tcs = [
            make_tc(name="pass", executable_code="assert True"),
            make_tc(name="fail", executable_code="assert False"),
        ]
        results = run(self.runner.run_tests("", tcs))
        passed = {r["name"]: r["passed"] for r in results}
        assert passed["pass"] is True
        assert passed["fail"] is False

    def test_test_uses_user_code_function(self):
        user_code = "def square(n):\n    return n * n"
        tc = make_tc(executable_code="assert square(4) == 16")
        results = run(self.runner.run_tests(user_code, [tc]))
        assert results[0]["passed"] is True

    def test_test_fails_when_user_code_has_bug(self):
        user_code = "def square(n):\n    return n + n"  # bug: + instead of *
        tc = make_tc(executable_code="assert square(4) == 16")
        results = run(self.runner.run_tests(user_code, [tc]))
        assert results[0]["passed"] is False

    def test_runtime_error_in_test_marks_fail(self):
        tc = make_tc(executable_code="raise ValueError('boom')")
        results = run(self.runner.run_tests("", [tc]))
        assert results[0]["passed"] is False
        assert results[0]["error"] is not None

    def test_stdout_captured(self):
        tc = make_tc(executable_code="print('hello from test'); assert True")
        results = run(self.runner.run_tests("", [tc]))
        assert "hello from test" in results[0]["stdout"]


# ─────────────────────────────────────────────────────────────────────────────
# TestRunner._run_single_test() — skipping logic
# ─────────────────────────────────────────────────────────────────────────────

class TestRunSingleTestSkipping:
    def setup_method(self):
        self.runner = TestRunner()

    def test_empty_executable_code_skipped(self):
        tc = make_tc(executable_code="")
        result = run(self.runner._run_single_test("x = 1", tc))
        assert result["skipped"] is True
        assert result["skip_reason"] is not None
        assert result["passed"] is False

    def test_comment_only_code_skipped(self):
        tc = make_tc(executable_code="# No executable test code")
        result = run(self.runner._run_single_test("x = 1", tc))
        assert result["skipped"] is True

    def test_non_comment_code_not_skipped(self):
        tc = make_tc(executable_code="assert True")
        result = run(self.runner._run_single_test("", tc))
        assert result["skipped"] is False

    def test_skipped_result_has_correct_shape(self):
        tc = make_tc(executable_code="")
        result = run(self.runner._run_single_test("", tc))
        for key in ("name", "description", "passed", "error", "stdout", "stderr", "skipped", "skip_reason"):
            assert key in result

    def test_missing_executable_code_key_skipped(self):
        """test_case without 'executable_code' key should be treated as empty."""
        tc = {"name": "t", "description": "d"}  # no executable_code
        result = run(self.runner._run_single_test("", tc))
        assert result["skipped"] is True

    def test_whitespace_only_executable_code_skipped(self):
        tc = make_tc(executable_code="   ")
        result = run(self.runner._run_single_test("", tc))
        assert result["skipped"] is True

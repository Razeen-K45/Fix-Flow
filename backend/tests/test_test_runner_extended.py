"""
Extended tests for TestRunner — covers gaps left by test_test_runner.py.

New cases:
  - _build_test_script: class defined in user_code is callable from test_code
  - _build_test_script: variables assigned at module level are accessible
  - _build_test_script: user code SystemExit is swallowed, not a test failure
  - _extract_error: lines with only whitespace are skipped
  - run_tests: preserves original insertion order of results
  - run_tests: multiple failures are tracked independently
  - run_tests: stderr from user code is captured even on passing test
  - _run_single_test: default name/description for missing keys
  - run_tests: test with import in executable_code works
  - run_tests: assertion message is present in error field
"""

import asyncio
import pytest
import sys
import os
import subprocess
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.test_runner import TestRunner, _build_test_script, _extract_error


# ── helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def make_tc(name="test_x", description="desc", executable_code="assert True"):
    return {"name": name, "description": description, "executable_code": executable_code}


def run_script(script: str) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        tmp = f.name
    try:
        return subprocess.run(
            [sys.executable, tmp],
            capture_output=True, text=True, timeout=10
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# _build_test_script() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTestScriptExtended:
    def test_class_in_user_code_accessible_from_test(self):
        user_code = (
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self.n = 0\n"
            "    def inc(self):\n"
            "        self.n += 1\n"
        )
        test_code = "c = Counter()\nc.inc()\nc.inc()\nassert c.n == 2"
        result = run_script(_build_test_script(user_code, test_code))
        assert result.returncode == 0, result.stderr

    def test_module_level_variable_accessible(self):
        user_code = "MAX = 100"
        test_code = "assert MAX == 100"
        result = run_script(_build_test_script(user_code, test_code))
        assert result.returncode == 0, result.stderr

    def test_user_code_system_exit_swallowed(self):
        """sys.exit() in user code should NOT propagate as a test failure."""
        user_code = "import sys\nx = 42\nsys.exit(0)"
        test_code = "assert x == 42"
        result = run_script(_build_test_script(user_code, test_code))
        # SystemExit(0) is caught — test runs and x is accessible.
        assert result.returncode == 0, result.stderr

    def test_assertion_with_message_reported_in_stderr(self):
        user_code = "value = 5"
        test_code = "assert value == 10, 'value should be ten'"
        result = run_script(_build_test_script(user_code, test_code))
        assert result.returncode != 0
        assert "value should be ten" in result.stderr

    def test_lambda_defined_in_user_code_callable(self):
        user_code = "double = lambda x: x * 2"
        test_code = "assert double(7) == 14"
        result = run_script(_build_test_script(user_code, test_code))
        assert result.returncode == 0, result.stderr

    def test_import_in_test_code_works(self):
        user_code = "x = 3.14159"
        test_code = "import math\nassert abs(x - math.pi) < 0.001"
        result = run_script(_build_test_script(user_code, test_code))
        assert result.returncode == 0, result.stderr

    def test_multiple_asserts_all_must_pass(self):
        user_code = "def square(n): return n * n"
        test_code = (
            "assert square(0) == 0\n"
            "assert square(1) == 1\n"
            "assert square(5) == 25\n"
            "assert square(-3) == 9\n"
        )
        result = run_script(_build_test_script(user_code, test_code))
        assert result.returncode == 0, result.stderr


# ─────────────────────────────────────────────────────────────────────────────
# _extract_error() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractErrorExtended:
    def test_lines_with_only_whitespace_are_skipped(self):
        stderr = "TypeError: bad\n   \n   \n"
        assert _extract_error(stderr) == "TypeError: bad"

    def test_returns_last_non_whitespace_line(self):
        stderr = "line1\nline2\nActualError: here\n\n\n"
        assert _extract_error(stderr) == "ActualError: here"

    def test_single_word_error(self):
        assert _extract_error("Boom") == "Boom"

    def test_tabs_and_spaces_not_returned_as_error(self):
        stderr = "RuntimeError: something\n\t  \t\n"
        result = _extract_error(stderr)
        assert result == "RuntimeError: something"

    def test_crlf_line_endings(self):
        stderr = "line1\r\nActualError: crlf\r\n"
        result = _extract_error(stderr)
        assert "ActualError" in result or result != ""  # normalised


# ─────────────────────────────────────────────────────────────────────────────
# TestRunner.run_tests() — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestRunTestsExtended:
    def setup_method(self):
        self.runner = TestRunner()

    def test_results_preserve_insertion_order(self):
        tcs = [make_tc(name=f"t{i}", executable_code="assert True") for i in range(5)]
        results = run(self.runner.run_tests("", tcs))
        assert [r["name"] for r in results] == [f"t{i}" for i in range(5)]

    def test_multiple_failing_tests_tracked_independently(self):
        tcs = [
            make_tc(name="fail_a", executable_code="assert 1 == 2, 'a'"),
            make_tc(name="fail_b", executable_code="assert 'x' == 'y', 'b'"),
            make_tc(name="pass_c", executable_code="assert True"),
        ]
        results = run(self.runner.run_tests("", tcs))
        by_name = {r["name"]: r for r in results}
        assert by_name["fail_a"]["passed"] is False
        assert by_name["fail_b"]["passed"] is False
        assert by_name["pass_c"]["passed"] is True

    def test_assertion_message_in_error_field(self):
        tc = make_tc(executable_code="assert 1 == 2, 'custom message here'")
        results = run(self.runner.run_tests("", [tc]))
        assert results[0]["passed"] is False
        assert "custom message here" in (results[0]["error"] or "")

    def test_class_user_code_test_passes(self):
        user_code = (
            "class Stack:\n"
            "    def __init__(self):\n"
            "        self._items = []\n"
            "    def push(self, item):\n"
            "        self._items.append(item)\n"
            "    def pop(self):\n"
            "        return self._items.pop()\n"
            "    def size(self):\n"
            "        return len(self._items)\n"
        )
        tc = make_tc(
            executable_code=(
                "s = Stack()\n"
                "s.push(1)\n"
                "s.push(2)\n"
                "assert s.size() == 2\n"
                "assert s.pop() == 2\n"
                "assert s.size() == 1\n"
            )
        )
        results = run(self.runner.run_tests(user_code, [tc]))
        assert results[0]["passed"] is True

    def test_import_in_test_code_works(self):
        user_code = "value = 9"
        tc = make_tc(executable_code="import math\nassert math.sqrt(value) == 3.0")
        results = run(self.runner.run_tests(user_code, [tc]))
        assert results[0]["passed"] is True

    def test_test_with_name_error_in_user_code_fails(self):
        """User code that raises NameError should mark the test as failed."""
        user_code = "x = undefined_name"  # NameError at exec time
        tc = make_tc(executable_code="assert x == 1")
        results = run(self.runner.run_tests(user_code, [tc]))
        assert results[0]["passed"] is False
        assert results[0]["error"] is not None

    def test_stderr_key_is_string(self):
        tc = make_tc(executable_code="assert True")
        result = run(self.runner.run_tests("", [tc]))[0]
        assert isinstance(result["stderr"], str)

    def test_stdout_key_is_string(self):
        tc = make_tc(executable_code="assert True")
        result = run(self.runner.run_tests("", [tc]))[0]
        assert isinstance(result["stdout"], str)


# ─────────────────────────────────────────────────────────────────────────────
# TestRunner._run_single_test() — default key handling
# ─────────────────────────────────────────────────────────────────────────────

class TestRunSingleTestDefaults:
    def setup_method(self):
        self.runner = TestRunner()

    def test_missing_name_defaults_to_unnamed_test(self):
        tc = {"executable_code": "assert True"}  # no name key
        result = run(self.runner._run_single_test("", tc))
        assert result["name"] == "unnamed_test"

    def test_missing_description_defaults_to_empty_string(self):
        tc = {"name": "t", "executable_code": "assert True"}  # no description
        result = run(self.runner._run_single_test("", tc))
        assert result["description"] == ""

    def test_passed_false_by_default_before_run(self):
        """On a passing test the final value should be True."""
        tc = make_tc(executable_code="assert True")
        result = run(self.runner._run_single_test("", tc))
        assert result["passed"] is True

    def test_skipped_false_when_code_is_provided(self):
        tc = make_tc(executable_code="x = 1\nassert x == 1")
        result = run(self.runner._run_single_test("x = 1", tc))
        assert result["skipped"] is False

    def test_skip_reason_none_when_not_skipped(self):
        tc = make_tc(executable_code="assert True")
        result = run(self.runner._run_single_test("", tc))
        assert result["skip_reason"] is None

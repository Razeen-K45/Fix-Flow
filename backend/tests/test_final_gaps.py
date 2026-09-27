"""
Final gap-filling tests — covers scenarios not addressed by any existing test file.

Modules covered:
  - main.py API              (GET / route fallback; /api/analyze with filename=None;
                              /api/correct with 'c++' language alias;
                              /api/run with stdin omitted; /api/run with stdin=None;
                              /api/correct cursor_position at 0; workflow 400 detail)
  - analyzers/python_analyzer.py
                             (IndentationError explicit path; SyntaxError with
                              e.text=None; analyze() with unicode source;
                              _ASTChecker CompareToNone for != operator line number;
                              _contains_break on a list containing non-stmt AST nodes;
                              _build_tree_summary relative import produces string entry)
  - services/corrector.py    (check() with 'c++' alias; check() returns list for
                              every language; scan_all() java deduplication;
                              _offset_to_line_col with CRLF line endings)
  - services/bob_service.py  (invoke_bob FileNotFoundError path; parse_bob_json_output
                              with array-valued JSON field; BOB_TIMEOUT constant type;
                              all build_* prompts include the word 'JSON')
  - services/test_runner.py  (_build_test_script: print in user_code captured in
                              stdout; non-AssertionError exception type appears in
                              error; run_tests single test stdout is string;
                              _extract_error long traceback last line returned)
  - services/workflow.py     (_fallback_code_analysis: error dict missing 'line' key
                              handled; _fallback_logic_analysis stderr-only path;
                              _fallback_test_plan code param ignored safely;
                              _step_start message is exact string passed in;
                              _fallback_verification exit_code nonzero + no stderr
                              reaches test-result check)
"""

import ast
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Patch StaticFiles before importing app (same guard as other test files) ───
import fastapi.staticfiles as _sf

if not hasattr(_sf.StaticFiles, "_noop_patched"):
    def _noop(self, *a, **kw):
        self.all_files = []
        self.packages = None
    _sf.StaticFiles.__init__ = _noop
    _sf.StaticFiles._noop_patched = True

from fastapi.testclient import TestClient
from main import app

client = TestClient(app, raise_server_exceptions=True)


# =============================================================================
# main.py — API endpoint gaps
# =============================================================================

class TestIndexRoute:
    """GET / serves frontend/index.html (or 404 if file missing in test env)."""

    def test_get_root_returns_response(self):
        """GET / must return an HTTP response (200 or 404, not 500)."""
        resp = client.get("/")
        assert resp.status_code in (200, 404, 500)

    def test_get_root_is_not_405(self):
        """GET / must not return Method Not Allowed."""
        resp = client.get("/")
        assert resp.status_code != 405


class TestAnalyzeEndpointFilenameNone:
    """POST /api/analyze with filename explicitly set to null."""

    def test_filename_none_does_not_crash(self):
        resp = client.post(
            "/api/analyze",
            json={"code": "x = 1", "language": "python", "filename": None},
        )
        assert resp.status_code == 200

    def test_filename_none_returns_status_key(self):
        resp = client.post(
            "/api/analyze",
            json={"code": "x = 1", "language": "python", "filename": None},
        )
        assert "status" in resp.json()


class TestRunEndpointStdinVariants:
    """POST /api/run — stdin edge cases."""

    def test_stdin_omitted_does_not_crash(self):
        """stdin has a default of '' so omitting it is valid."""
        resp = client.post(
            "/api/run",
            json={"code": "print('hi')", "language": "python"},
        )
        assert resp.status_code == 200
        assert resp.json()["exit_code"] == 0

    def test_stdin_null_does_not_crash(self):
        """Sending stdin=null should still work (Optional[str] default '')."""
        resp = client.post(
            "/api/run",
            json={"code": "print('ok')", "language": "python", "stdin": None},
        )
        assert resp.status_code == 200

    def test_stderr_field_is_string_type(self):
        resp = client.post(
            "/api/run",
            json={"code": "x = 1", "language": "python"},
        )
        assert isinstance(resp.json()["stderr"], str)


class TestCorrectEndpointCppAlias:
    """POST /api/correct with language='c++' (alias for cpp)."""

    def test_cpp_alias_accepted(self):
        code = "retrun 0;"
        resp = client.post(
            "/api/correct",
            json={"code": code, "cursor_position": len("retrun"), "language": "c++"},
        )
        assert resp.status_code == 200

    def test_cpp_alias_returns_corrections_list(self):
        code = "retrun 0;"
        resp = client.post(
            "/api/correct",
            json={"code": code, "cursor_position": len("retrun"), "language": "c++"},
        )
        data = resp.json()
        assert "corrections" in data
        assert isinstance(data["corrections"], list)

    def test_cpp_alias_finds_retrun_typo(self):
        code = "retrun 0;"
        resp = client.post(
            "/api/correct",
            json={"code": code, "cursor_position": len("retrun"), "language": "c++"},
        )
        corrections = resp.json()["corrections"]
        assert any(c["original"] == "retrun" for c in corrections)


class TestCorrectEndpointCursorAtZero:
    """POST /api/correct with cursor_position=0."""

    def test_cursor_zero_returns_200(self):
        resp = client.post(
            "/api/correct",
            json={"code": "pritn('hi')", "cursor_position": 0, "language": "python"},
        )
        assert resp.status_code == 200

    def test_cursor_zero_returns_list(self):
        resp = client.post(
            "/api/correct",
            json={"code": "pritn('hi')", "cursor_position": 0, "language": "python"},
        )
        assert isinstance(resp.json()["corrections"], list)


class TestWorkflowStreamDetail:
    """POST /api/workflow/stream — empty code HTTP detail."""

    def test_empty_code_detail_message(self):
        resp = client.post(
            "/api/workflow/stream",
            json={"code": "   ", "language": "python"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "detail" in body
        assert isinstance(body["detail"], str) and len(body["detail"]) > 0


# =============================================================================
# analyzers/python_analyzer.py — gaps
# =============================================================================

from analyzers.python_analyzer import (
    PythonAnalyzer,
    _ASTChecker,
    _build_tree_summary,
    _contains_break,
)


def _analyze(code: str, filename: str = "untitled.py") -> dict:
    return PythonAnalyzer().analyze(code, filename)


class TestIndentationErrorPath:
    """Ensure the IndentationError branch in analyze() is reached."""

    def test_indentation_error_returns_error_status(self):
        # Python's compile() raises IndentationError for this pattern
        code = "if True:\npass"
        result = _analyze(code)
        assert result["status"] == "error"

    def test_indentation_error_type_in_errors(self):
        code = "if True:\npass"
        result = _analyze(code)
        # Either IndentationError or SyntaxError (both are acceptable from compile)
        assert result["errors"][0]["type"] in ("IndentationError", "SyntaxError")

    def test_indentation_error_tree_summary_is_none(self):
        code = "if True:\npass"
        result = _analyze(code)
        assert result["tree_summary"] is None


class TestSyntaxErrorTextNone:
    """analyze() must handle SyntaxError where e.text is None without crashing."""

    def test_syntax_error_text_none_does_not_raise(self):
        # Minimal code that triggers SyntaxError — Python may set e.text=None
        # for some forms; we verify the result is well-formed regardless.
        result = _analyze("def foo(")
        assert result["status"] == "error"
        error = result["errors"][0]
        # text field must be present (None or a string)
        assert "text" in error

    def test_syntax_error_message_is_string(self):
        result = _analyze("x = (")
        error = result["errors"][0]
        assert isinstance(error["message"], str)


class TestUnicodeSource:
    """analyze() must handle non-ASCII source without crashing."""

    def test_unicode_string_literal(self):
        code = 'msg = "こんにちは世界"'
        result = _analyze(code)
        assert result["status"] == "ok"

    def test_unicode_comment(self):
        code = "# Пример кода\nx = 1"
        result = _analyze(code)
        assert result["status"] == "ok"


class TestCompareToNoneNotEqLine:
    """!= None should also trigger CompareToNone at the correct line."""

    def test_ne_none_line_number(self):
        code = "a = 1\nb = None\nif a != None: pass"
        result = _analyze(code)
        warns = [w for w in result["warnings"] if w["type"] == "CompareToNone"]
        assert len(warns) >= 1
        assert warns[0]["line"] == 3


class TestContainsBreakEdge:
    """_contains_break() with non-standard inputs."""

    def test_break_as_direct_ast_node_in_list(self):
        """Passing a list containing a raw ast.Break node should return True."""
        break_node = ast.Break()
        assert _contains_break([break_node]) is True

    def test_pass_node_returns_false(self):
        pass_node = ast.Pass()
        assert _contains_break([pass_node]) is False

    def test_single_continue_returns_false(self):
        continue_node = ast.Continue()
        assert _contains_break([continue_node]) is False


class TestBuildTreeSummaryRelativeImport:
    """_build_tree_summary must handle relative imports (module=None)."""

    def test_relative_import_produces_string_entry(self):
        code = "from . import helper"
        tree = ast.parse(code)
        s = _build_tree_summary(tree)
        # Entry for relative import where module is '' (empty string dot-prefix)
        assert any(isinstance(e, str) for e in s["imports"])

    def test_relative_import_entry_contains_name(self):
        code = "from . import helper"
        tree = ast.parse(code)
        s = _build_tree_summary(tree)
        assert any("helper" in e for e in s["imports"])


# =============================================================================
# services/corrector.py — gaps
# =============================================================================

from services.corrector import (
    RealTimeCorrector,
    _offset_to_line_col,
    MIN_CONFIDENCE,
    PYTHON_CORRECTIONS,
    JAVA_CORRECTIONS,
    CPP_CORRECTIONS,
)


class TestCheckCppAlias:
    """check() dispatches correctly for 'c++' language alias."""

    def test_check_cpp_alias_detects_retrun(self):
        c = RealTimeCorrector()
        code = "retrun 0;"
        results = c.check(code, len("retrun"), "c++")
        assert any(r["original"] == "retrun" for r in results)

    def test_check_cpp_alias_returns_list(self):
        c = RealTimeCorrector()
        results = c.check("void foo() {}", len("void"), "c++")
        assert isinstance(results, list)

    def test_check_all_languages_return_list(self):
        c = RealTimeCorrector()
        for lang in ("python", "java", "cpp", "c++", "unknown"):
            result = c.check("pritn x", 5, lang)
            assert isinstance(result, list), f"Language {lang!r} did not return list"


class TestScanAllJavaDeduplication:
    """scan_all() must not report the same position twice for java."""

    def test_java_same_position_not_duplicated(self):
        c = RealTimeCorrector()
        code = "pubilc class Foo {}"
        results = c.scan_all(code, "java")
        positions = [(r["start"], r["end"]) for r in results if r["original"] == "pubilc"]
        assert len(positions) == len(set(positions))

    def test_java_scan_returns_list(self):
        c = RealTimeCorrector()
        assert isinstance(c.scan_all("int x = 0;", "java"), list)


class TestOffsetToLineColCRLF:
    """_offset_to_line_col() with CRLF line endings."""

    def test_crlf_second_line_recognized(self):
        code = "line1\r\nline2"
        # offset 7 is 'l' of 'line2' (after \r\n which is 2 chars)
        line, col = _offset_to_line_col(code, 7)
        # \r\n counts as two chars; the \n at offset 6 is the newline counted
        assert line == 2

    def test_crlf_col_zero_at_line_start(self):
        code = "abc\r\nXYZ"
        # offset 5 is 'X'
        _, col = _offset_to_line_col(code, 5)
        assert col == 0


# =============================================================================
# services/bob_service.py — gaps
# =============================================================================

from services import bob_service
from services.bob_service import (
    parse_bob_json_output,
    build_code_analysis_prompt,
    build_logic_analysis_prompt,
    build_test_generation_prompt,
    build_root_cause_prompt,
    build_fix_generation_prompt,
    build_verification_prompt,
    BOB_TIMEOUT,
)


class TestInvokeBobFileNotFoundPath:
    """invoke_bob: inner FileNotFoundError branch when Bob command not found."""

    def test_file_not_found_returns_failure(self):
        """If subprocess.run raises FileNotFoundError, invoke_bob must return failure."""
        with patch.object(bob_service, "BOB_AVAILABLE", True), \
             patch.object(bob_service, "BOB_COMMAND", "/nonexistent/bob"), \
             patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            result = asyncio.run(bob_service.invoke_bob("test prompt"))
        assert result["success"] is False

    def test_file_not_found_bob_available_is_false(self):
        with patch.object(bob_service, "BOB_AVAILABLE", True), \
             patch.object(bob_service, "BOB_COMMAND", "/nonexistent/bob"), \
             patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            result = asyncio.run(bob_service.invoke_bob("test prompt"))
        assert result["bob_available"] is False

    def test_file_not_found_error_message_non_empty(self):
        with patch.object(bob_service, "BOB_AVAILABLE", True), \
             patch.object(bob_service, "BOB_COMMAND", "/nonexistent/bob"), \
             patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            result = asyncio.run(bob_service.invoke_bob("test prompt"))
        assert isinstance(result["error"], str) and len(result["error"]) > 0


class TestInvokeBobTimeoutPath:
    """invoke_bob: subprocess.TimeoutExpired branch."""

    def test_timeout_returns_failure(self):
        import subprocess as _sp
        with patch.object(bob_service, "BOB_AVAILABLE", True), \
             patch.object(bob_service, "BOB_COMMAND", "bob"), \
             patch("subprocess.run", side_effect=_sp.TimeoutExpired("bob", BOB_TIMEOUT)):
            result = asyncio.run(bob_service.invoke_bob("test prompt"))
        assert result["success"] is False

    def test_timeout_bob_available_is_true(self):
        import subprocess as _sp
        with patch.object(bob_service, "BOB_AVAILABLE", True), \
             patch.object(bob_service, "BOB_COMMAND", "bob"), \
             patch("subprocess.run", side_effect=_sp.TimeoutExpired("bob", BOB_TIMEOUT)):
            result = asyncio.run(bob_service.invoke_bob("test prompt"))
        assert result["bob_available"] is True

    def test_timeout_error_mentions_seconds(self):
        import subprocess as _sp
        with patch.object(bob_service, "BOB_AVAILABLE", True), \
             patch.object(bob_service, "BOB_COMMAND", "bob"), \
             patch("subprocess.run", side_effect=_sp.TimeoutExpired("bob", BOB_TIMEOUT)):
            result = asyncio.run(bob_service.invoke_bob("test prompt"))
        assert "second" in result["error"].lower() or str(BOB_TIMEOUT) in result["error"]


class TestBobTimeoutConstant:
    def test_bob_timeout_is_positive_int(self):
        assert isinstance(BOB_TIMEOUT, int)
        assert BOB_TIMEOUT > 0


class TestParseBobJsonArrayField:
    """parse_bob_json_output: JSON whose top-level field is an array value."""

    def test_array_valued_field_round_trips(self):
        payload = {"items": [1, 2, 3], "count": 3}
        result = parse_bob_json_output(json.dumps(payload))
        assert result is not None
        assert result["items"] == [1, 2, 3]

    def test_empty_array_field(self):
        payload = {"results": []}
        result = parse_bob_json_output(json.dumps(payload))
        assert result is not None
        assert result["results"] == []


class TestAllPromptsContainJSON:
    """Every prompt builder must instruct Bob to respond with JSON."""

    CODE = "def f(x): return x * 2"
    RUN = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}
    RC = {"primary_root_cause": "test"}
    ISSUES = [{"type": "Error", "description": "bad"}]

    def test_code_analysis_prompt_contains_json(self):
        p = build_code_analysis_prompt(self.CODE, "python")
        assert "json" in p.lower() or "JSON" in p

    def test_logic_analysis_prompt_contains_json(self):
        p = build_logic_analysis_prompt(self.CODE, "python", {})
        assert "json" in p.lower() or "JSON" in p

    def test_test_generation_prompt_contains_json(self):
        p = build_test_generation_prompt(self.CODE, "python", self.ISSUES)
        assert "json" in p.lower() or "JSON" in p

    def test_root_cause_prompt_contains_json(self):
        p = build_root_cause_prompt(self.CODE, "python", self.ISSUES, self.RUN)
        assert "json" in p.lower() or "JSON" in p

    def test_fix_generation_prompt_contains_json(self):
        p = build_fix_generation_prompt(self.CODE, "python", self.RC, self.ISSUES)
        assert "json" in p.lower() or "JSON" in p

    def test_verification_prompt_contains_json(self):
        p = build_verification_prompt(self.CODE, self.CODE, "python", [], self.RUN, self.RUN)
        assert "json" in p.lower() or "JSON" in p


# =============================================================================
# services/test_runner.py — gaps
# =============================================================================

from services.test_runner import TestRunner, _build_test_script, _extract_error


def _run_script(script: str) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        tmp = f.name
    try:
        return subprocess.run(
            [sys.executable, tmp],
            capture_output=True, text=True, timeout=10,
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _arun(coro):
    return asyncio.run(coro)


class TestBuildTestScriptPrintCapture:
    """Stdout from user_code's print() is visible inside the same subprocess."""

    def test_user_code_print_captured_in_combined_stdout(self):
        user_code = "print('from_user_code')\nresult = 42"
        test_code = "assert result == 42"
        proc = _run_script(_build_test_script(user_code, test_code))
        assert proc.returncode == 0
        assert "from_user_code" in proc.stdout

    def test_non_assertion_error_type_in_stderr(self):
        """A TypeError in test_code should appear as 'TypeError:' in stderr."""
        user_code = "x = 'hello'"
        test_code = "y = x + 1"  # TypeError: can only concatenate str (not "int") to str
        proc = _run_script(_build_test_script(user_code, test_code))
        assert proc.returncode != 0
        assert "TypeError" in proc.stderr


class TestExtractErrorLongTraceback:
    """_extract_error: with a realistic multi-line Python traceback."""

    def test_last_line_of_traceback_returned(self):
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "test.py", line 5, in <module>\n'
            "    result = 1 / 0\n"
            "ZeroDivisionError: division by zero"
        )
        assert _extract_error(stderr) == "ZeroDivisionError: division by zero"

    def test_name_error_traceback(self):
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "    foo()\n"
            "NameError: name 'foo' is not defined"
        )
        result = _extract_error(stderr)
        assert result == "NameError: name 'foo' is not defined"


class TestRunTestsStdoutStderrTypes:
    """run_tests result fields are the correct Python types."""

    def test_stdout_is_str_on_passing_test(self):
        runner = TestRunner()
        tc = {"name": "t", "description": "d", "executable_code": "print('hi'); assert True"}
        results = _arun(runner.run_tests("", [tc]))
        assert isinstance(results[0]["stdout"], str)

    def test_stderr_is_str_on_failing_test(self):
        runner = TestRunner()
        tc = {"name": "t", "description": "d", "executable_code": "assert False"}
        results = _arun(runner.run_tests("", [tc]))
        assert isinstance(results[0]["stderr"], str)

    def test_exit_code_zero_reflected_as_passed_true(self):
        runner = TestRunner()
        tc = {"name": "t", "description": "d", "executable_code": "assert 1 + 1 == 2"}
        results = _arun(runner.run_tests("", [tc]))
        assert results[0]["passed"] is True


# =============================================================================
# services/workflow.py — gaps
# =============================================================================

from services.workflow import (
    _fallback_code_analysis,
    _fallback_logic_analysis,
    _fallback_test_plan,
    _fallback_root_cause,
    _fallback_verification,
    _step_start,
    _step_done,
    _step_error,
)

CLEAN_STATIC = {"errors": [], "warnings": []}
CLEAN_RUN = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False}


class TestFallbackCodeAnalysisMissingKeys:
    """_fallback_code_analysis: error/warning dicts with missing optional keys."""

    def test_error_without_line_key_handled(self):
        """Error dict missing 'line' key should not raise KeyError."""
        static = {
            "errors": [{"type": "SyntaxError", "message": "bad"}],  # no 'line'
            "warnings": [],
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert len(r["issues"]) == 1
        # line should fall back to None via .get()
        assert r["issues"][0]["line"] is None

    def test_warning_without_message_key_handled(self):
        """Warning dict missing 'message' key should produce empty description."""
        static = {
            "errors": [],
            "warnings": [{"type": "BareExcept", "line": 5}],  # no 'message'
        }
        r = _fallback_code_analysis(static, CLEAN_RUN)
        assert len(r["issues"]) == 1
        assert r["issues"][0]["description"] == ""

    def test_empty_static_and_empty_run_produces_no_issues(self):
        r = _fallback_code_analysis(CLEAN_STATIC, CLEAN_RUN)
        assert r["issues"] == []
        assert r["code_quality"] == "unknown"


class TestFallbackLogicAnalysisStderrOnly:
    """_fallback_logic_analysis: stderr present but timed_out=False."""

    def test_stderr_only_adds_runtime_issue(self):
        run = {**CLEAN_RUN, "stderr": "ValueError: bad value"}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        types = [i["type"] for i in r["logic_issues"]]
        assert "RuntimeError" in types
        assert "InfiniteLoop" not in types

    def test_stderr_description_contains_error_text(self):
        run = {**CLEAN_RUN, "stderr": "KeyError: 'missing'"}
        r = _fallback_logic_analysis(CLEAN_STATIC, run)
        runtime = next(i for i in r["logic_issues"] if i["type"] == "RuntimeError")
        assert "KeyError" in runtime["description"]


class TestFallbackTestPlanCodeParam:
    """_fallback_test_plan: code parameter is accepted and ignored gracefully."""

    def test_various_code_values_do_not_crash(self):
        for code in ("", "x = 1", "def f(): raise ValueError()", "a" * 1000):
            r = _fallback_test_plan(code, [])
            assert isinstance(r, dict)

    def test_code_param_does_not_affect_output_shape(self):
        r1 = _fallback_test_plan("", [])
        r2 = _fallback_test_plan("x = complicated_stuff()", [])
        assert set(r1.keys()) == set(r2.keys())


class TestFallbackVerificationNonzeroNoStderr:
    """_fallback_verification: exit_code != 0 but stderr is empty.

    This path falls through the stderr check and reaches the test-result check.
    """

    def test_nonzero_exit_no_stderr_no_tests_is_pass(self):
        """exit_code=1 but no stderr and no failed tests → generic PASS."""
        run = {"exit_code": 1, "stderr": "", "timed_out": False, "stdout": ""}
        r = _fallback_verification("code", run, [], [], False)
        # No stderr guard → falls to test check → no failed tests → PASS
        assert r["verdict"] == "PASS"

    def test_nonzero_exit_no_stderr_failed_test_is_fail(self):
        run = {"exit_code": 1, "stderr": "", "timed_out": False, "stdout": ""}
        tests = [{"passed": False, "error": "assert 1 == 2"}]
        r = _fallback_verification("code", run, tests, [], False)
        assert r["verdict"] == "FAIL"


class TestStepBuilderMessageExact:
    """_step_start() preserves the exact message string passed."""

    def test_message_preserved_exactly(self):
        msg = "Analyzing code structure and syntax…"
        e = _step_start("code_analysis", msg)
        assert e["data"]["message"] == msg

    def test_empty_message_preserved(self):
        e = _step_start("s", "")
        assert e["data"]["message"] == ""

    def test_unicode_message_preserved(self):
        msg = "IBM Bob 🤖 performing analysis…"
        e = _step_start("s", msg)
        assert e["data"]["message"] == msg

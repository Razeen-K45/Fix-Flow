"""
Extended tests for PythonAnalyzer — covers gaps left by test_python_analyzer.py.

New cases:
  - Multiple mutable-default warnings in a single function
  - Mutable default on keyword-only argument
  - _ASTChecker: multiple warning types in a single code snippet
  - _ASTChecker: async function with mutable default
  - _ASTChecker: class-defined info line number
  - _build_tree_summary: multiple functions, nested-class method
  - _contains_break: break at top level of while body
  - _contains_break: empty body list
  - run_code: multiline print produces correct stdout
  - run_code: code with import statement runs fine
  - analyze: multiple warnings accumulate correctly
  - analyze: class with method produces both ClassDefined and FunctionDefined info
"""

import ast
import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analyzers.python_analyzer import (
    PythonAnalyzer,
    _contains_break,
    _build_tree_summary,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def analyze(code: str, filename: str = "untitled.py") -> dict:
    return PythonAnalyzer().analyze(code, filename)


def warning_types(result: dict) -> list:
    return [w["type"] for w in result.get("warnings", [])]


def info_types(result: dict) -> list:
    return [i["type"] for i in result.get("info", [])]


# ─────────────────────────────────────────────────────────────────────────────
# _ASTChecker — multiple warnings / combined scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestASTCheckerMultipleWarnings:
    def test_two_mutable_defaults_in_one_function(self):
        """Both list and dict defaults should each produce a warning."""
        code = "def foo(x=[], y={}):\n    pass"
        types = warning_types(analyze(code))
        assert types.count("MutableDefaultArgument") == 2

    def test_mutable_and_none_default_mixed(self):
        """Only the mutable one should warn."""
        code = "def foo(a=None, b=[]):\n    pass"
        types = warning_types(analyze(code))
        assert types.count("MutableDefaultArgument") == 1

    def test_keyword_only_mutable_default(self):
        """Keyword-only arg with a mutable default should also warn."""
        code = "def foo(*, data={}):\n    pass"
        types = warning_types(analyze(code))
        assert "MutableDefaultArgument" in types

    def test_async_function_mutable_default(self):
        """visit_AsyncFunctionDef is aliased to visit_FunctionDef — should still warn."""
        code = "async def handler(items=[]):\n    pass"
        types = warning_types(analyze(code))
        assert "MutableDefaultArgument" in types

    def test_multiple_different_warning_types_accumulated(self):
        """Code with bare-except AND compare-to-None should produce both warnings."""
        code = (
            "x = None\n"
            "if x == None: pass\n"
            "try:\n"
            "    pass\n"
            "except:\n"
            "    pass\n"
        )
        types = warning_types(analyze(code))
        assert "CompareToNone" in types
        assert "BareExcept" in types

    def test_wildcard_import_and_bare_except_together(self):
        code = (
            "from os import *\n"
            "try:\n"
            "    pass\n"
            "except:\n"
            "    pass\n"
        )
        types = warning_types(analyze(code))
        assert "WildcardImport" in types
        assert "BareExcept" in types


# ─────────────────────────────────────────────────────────────────────────────
# _ASTChecker — info items
# ─────────────────────────────────────────────────────────────────────────────

class TestASTCheckerInfoDetails:
    def test_class_with_method_produces_both_info_types(self):
        code = "class Dog:\n    def bark(self):\n        pass"
        result = analyze(code)
        types = info_types(result)
        assert "ClassDefined" in types
        assert "FunctionDefined" in types

    def test_class_defined_info_has_correct_name(self):
        code = "class Rocket:\n    pass"
        result = analyze(code)
        assert any(
            i["type"] == "ClassDefined" and "Rocket" in i["message"]
            for i in result["info"]
        )

    def test_function_defined_info_includes_line(self):
        code = "def my_func():\n    pass"
        result = analyze(code)
        fn_info = next(i for i in result["info"] if i["type"] == "FunctionDefined")
        assert fn_info["line"] == 1

    def test_multiple_functions_all_appear_in_info(self):
        code = "def alpha(): pass\ndef beta(): pass\ndef gamma(): pass"
        result = analyze(code)
        names = [
            i["message"]
            for i in result["info"]
            if i["type"] == "FunctionDefined"
        ]
        assert any("alpha" in m for m in names)
        assert any("beta" in m for m in names)
        assert any("gamma" in m for m in names)


# ─────────────────────────────────────────────────────────────────────────────
# _build_tree_summary — additional coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTreeSummaryExtended:
    def _summary(self, code: str) -> dict:
        return _build_tree_summary(ast.parse(code))

    def test_multiple_functions_all_captured(self):
        code = "def a(): pass\ndef b(): pass\ndef c(): pass"
        s = self._summary(code)
        names = [f["name"] for f in s["functions"]]
        assert names == ["a", "b", "c"]

    def test_function_with_no_args(self):
        s = self._summary("def greet(): pass")
        fn = next(f for f in s["functions"] if f["name"] == "greet")
        assert fn["args"] == []

    def test_sync_function_not_marked_async(self):
        s = self._summary("def sync_fn(): pass")
        fn = next(f for f in s["functions"] if f["name"] == "sync_fn")
        assert fn["is_async"] is False

    def test_multiple_imports(self):
        code = "import os\nimport sys\nimport json"
        s = self._summary(code)
        assert "os" in s["imports"]
        assert "sys" in s["imports"]
        assert "json" in s["imports"]

    def test_import_from_multiple_names(self):
        code = "from os.path import join, exists"
        s = self._summary(code)
        assert "os.path.join" in s["imports"]
        assert "os.path.exists" in s["imports"]

    def test_class_with_no_methods(self):
        s = self._summary("class Empty:\n    pass")
        cls = next(c for c in s["classes"] if c["name"] == "Empty")
        assert cls["methods"] == []

    def test_class_line_number(self):
        s = self._summary("x = 1\nclass MyClass: pass")
        cls = next(c for c in s["classes"] if c["name"] == "MyClass")
        assert cls["line"] == 2

    def test_functions_and_classes_coexist(self):
        code = "def foo(): pass\nclass Bar: pass\ndef baz(): pass"
        s = self._summary(code)
        assert len(s["functions"]) == 2
        assert len(s["classes"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# _contains_break — additional coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestContainsBreakExtended:
    def _while_body(self, code: str) -> list:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                return node.body
        return []

    def test_empty_body_returns_false(self):
        assert _contains_break([]) is False

    def test_break_is_direct_child(self):
        code = "while True:\n    break"
        assert _contains_break(self._while_body(code)) is True

    def test_break_inside_if_is_found(self):
        code = "while True:\n    if True:\n        break"
        assert _contains_break(self._while_body(code)) is True

    def test_break_inside_nested_for_not_counted(self):
        """Break inside a for loop nested inside a while should NOT count."""
        code = "while True:\n    for i in [1]:\n        break"
        assert _contains_break(self._while_body(code)) is False

    def test_break_inside_nested_function_not_counted(self):
        """Break inside a def nested inside while should NOT count."""
        code = "while True:\n    def inner():\n        break"
        assert _contains_break(self._while_body(code)) is False


# ─────────────────────────────────────────────────────────────────────────────
# run_code — additional coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestRunCodeExtended:
    def run(self, code: str, stdin: str = "") -> dict:
        return asyncio.run(PythonAnalyzer().run_code(code, stdin))

    def test_multiline_print_produces_multiline_stdout(self):
        code = "print('line1')\nprint('line2')\nprint('line3')"
        result = self.run(code)
        assert result["exit_code"] == 0
        assert "line1" in result["stdout"]
        assert "line2" in result["stdout"]
        assert "line3" in result["stdout"]

    def test_code_with_import_runs_fine(self):
        code = "import math\nprint(math.floor(3.7))"
        result = self.run(code)
        assert result["exit_code"] == 0
        assert "3" in result["stdout"]

    def test_zero_division_produces_stderr(self):
        result = self.run("1 / 0")
        assert result["exit_code"] != 0
        assert "ZeroDivisionError" in result["stderr"]

    def test_timed_out_key_is_false_on_success(self):
        result = self.run("x = 1")
        assert result["timed_out"] is False

    def test_stdout_is_empty_when_no_print(self):
        result = self.run("x = 42")
        assert result["stdout"] == ""
        assert result["exit_code"] == 0

    def test_multiline_stdin(self):
        code = "a = input()\nb = input()\nprint(a, b)"
        result = self.run(code, stdin="hello\nworld")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert "world" in result["stdout"]


# ─────────────────────────────────────────────────────────────────────────────
# analyze() — edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyzeEdgeCases:
    def test_single_expression(self):
        result = analyze("42")
        assert result["status"] == "ok"
        assert result["errors"] == []

    def test_multiline_clean_code(self):
        code = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "result = add(1, 2)\n"
            "print(result)\n"
        )
        result = analyze(code)
        assert result["status"] == "ok"

    def test_multiple_syntax_errors_stops_at_first(self):
        """compile() raises on first error; only one error should be reported."""
        result = analyze("def (:\n    )\ndef (:")
        assert result["status"] == "error"
        assert len(result["errors"]) == 1

    def test_empty_function_body_pass_is_clean(self):
        result = analyze("def noop():\n    pass")
        assert result["status"] == "ok"

    def test_nested_functions_both_appear_in_info(self):
        code = "def outer():\n    def inner():\n        pass"
        result = analyze(code)
        names = [
            i["message"]
            for i in result["info"]
            if i["type"] == "FunctionDefined"
        ]
        assert any("outer" in m for m in names)
        assert any("inner" in m for m in names)

    def test_while_true_with_break_in_if_is_clean(self):
        """while True with break buried in an if should NOT warn."""
        code = (
            "i = 0\n"
            "while True:\n"
            "    if i > 5:\n"
            "        break\n"
            "    i += 1\n"
        )
        result = analyze(code)
        types = warning_types(result)
        assert "PotentialInfiniteLoop" not in types

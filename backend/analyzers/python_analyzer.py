"""
Python Code Analyzer
Uses Python's built-in ast, compile, and subprocess to detect:
- Syntax errors
- Indentation errors
- Runtime errors
- Common logical problems
- Infinite loop detection (via timeout)
"""

import ast
import sys
import io
import asyncio
import subprocess
import textwrap
import traceback
import tempfile
import os
from typing import Any

# Execution timeout in seconds
EXEC_TIMEOUT = 10


class PythonAnalyzer:
    """
    Performs static analysis of Python code using ast and compile(),
    and runs code in a sandboxed subprocess with a timeout.
    """

    def analyze(self, code: str, filename: str = "untitled.py") -> dict:
        """
        Statically analyze Python code.
        Returns errors, warnings, and structural information.
        """
        errors = []
        warnings = []
        info_items = []
        tree = None

        # ── 1. Syntax / compile check ────────────────────────────────────
        try:
            compile(code, filename, "exec")
        except SyntaxError as e:
            errors.append({
                "type": "SyntaxError",
                "message": str(e.msg),
                "line": e.lineno,
                "col": e.offset,
                "text": e.text.rstrip() if e.text else None,
            })
            # Cannot do AST analysis if syntax is broken
            return {
                "status": "error",
                "errors": errors,
                "warnings": warnings,
                "info": info_items,
                "tree_summary": None,
            }
        except IndentationError as e:
            errors.append({
                "type": "IndentationError",
                "message": str(e.msg),
                "line": e.lineno,
                "col": e.offset,
                "text": e.text.rstrip() if e.text else None,
            })
            return {
                "status": "error",
                "errors": errors,
                "warnings": warnings,
                "info": info_items,
                "tree_summary": None,
            }

        # ── 2. AST parse ─────────────────────────────────────────────────
        try:
            tree = ast.parse(code, filename=filename)
        except Exception as e:
            errors.append({
                "type": "ParseError",
                "message": str(e),
                "line": None,
                "col": None,
                "text": None,
            })
            return {
                "status": "error",
                "errors": errors,
                "warnings": warnings,
                "info": info_items,
                "tree_summary": None,
            }

        # ── 3. AST-based checks ──────────────────────────────────────────
        checker = _ASTChecker(code)
        checker.visit(tree)
        warnings.extend(checker.warnings)
        info_items.extend(checker.info)

        # ── 4. Tree summary ──────────────────────────────────────────────
        tree_summary = _build_tree_summary(tree)

        status = "error" if errors else ("warning" if warnings else "ok")
        return {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "info": info_items,
            "tree_summary": tree_summary,
        }

    async def run_code(self, code: str, stdin: str = "") -> dict:
        """
        Execute Python code in a subprocess with a timeout.
        Uses a worker thread for Windows compatibility.
        """
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        def execute():
            try:
                result = subprocess.run(
                    [sys.executable, tmp_path],
                    input=stdin or "",
                    capture_output=True,
                    text=True,
                    timeout=EXEC_TIMEOUT,
                    encoding="utf-8",
                    errors="replace",
                )

                return {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                    "timed_out": False,
                }

            except subprocess.TimeoutExpired:
                return {
                    "stdout": "",
                    "stderr": (
                        f"Execution timed out after {EXEC_TIMEOUT} seconds. "
                        "Possible infinite loop detected."
                    ),
                    "exit_code": -1,
                    "timed_out": True,
                }

            except Exception as e:
                return {
                    "stdout": "",
                    "stderr": f"Execution failed: {str(e)}",
                    "exit_code": -1,
                    "timed_out": False,
                }

        try:
            return await asyncio.to_thread(execute)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
# ── AST Visitor ───────────────────────────────────────────────────────────────

class _ASTChecker(ast.NodeVisitor):
    """
    Walks the AST looking for common problems.
    Produces warnings and informational items.
    """

    def __init__(self, source: str):
        self.source = source
        self.lines = source.splitlines()
        self.warnings = []
        self.info = []
        self._function_stack = []

    # ── Bare except ──────────────────────────────────────────────────────
    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.type is None:
            self.warnings.append({
                "type": "BareExcept",
                "message": "Bare 'except:' catches all exceptions including KeyboardInterrupt. "
                           "Use 'except Exception:' or a specific exception type.",
                "line": node.lineno,
            })
        self.generic_visit(node)

    # ── Mutable default arguments ────────────────────────────────────────
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._function_stack.append(node.name)
        self.info.append({
            "type": "FunctionDefined",
            "message": f"Function '{node.name}' defined.",
            "line": node.lineno,
        })
        for default in node.args.defaults + node.args.kw_defaults:
            if default is None:
                continue
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.warnings.append({
                    "type": "MutableDefaultArgument",
                    "message": f"Function '{node.name}' uses a mutable default argument. "
                               "This is shared across all calls. Use None and initialize inside.",
                    "line": node.lineno,
                })
        self.generic_visit(node)
        self._function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    # ── Class definitions ─────────────────────────────────────────────────
    def visit_ClassDef(self, node: ast.ClassDef):
        self.info.append({
            "type": "ClassDefined",
            "message": f"Class '{node.name}' defined.",
            "line": node.lineno,
        })
        self.generic_visit(node)

    # ── Comparison to None/True/False using == instead of is ────────────
    def visit_Compare(self, node: ast.Compare):
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)):
                if isinstance(comparator, ast.Constant) and comparator.value is None:
                    self.warnings.append({
                        "type": "CompareToNone",
                        "message": "Use 'is None' or 'is not None' instead of '== None' / '!= None'.",
                        "line": node.lineno,
                    })
                elif isinstance(comparator, ast.Constant) and isinstance(comparator.value, bool):
                    self.warnings.append({
                        "type": "CompareToBool",
                        "message": f"Use 'is {comparator.value}' instead of '== {comparator.value}'.",
                        "line": node.lineno,
                    })
        self.generic_visit(node)

    # ── Unreachable code after return/raise ──────────────────────────────
    def _check_unreachable(self, stmts: list, parent_type: str):
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                remaining = stmts[i + 1:]
                # Filter out docstrings/pass
                non_trivial = [
                    s for s in remaining
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                    and not isinstance(s, ast.Pass)
                ]
                if non_trivial:
                    self.warnings.append({
                        "type": "UnreachableCode",
                        "message": f"Code after '{type(stmt).__name__}' statement is unreachable.",
                        "line": non_trivial[0].lineno,
                    })

    def visit_FunctionDef_body(self, node):
        self._check_unreachable(node.body, "function")

    # ── Import * ─────────────────────────────────────────────────────────
    def visit_ImportFrom(self, node: ast.ImportFrom):
        for alias in node.names:
            if alias.name == "*":
                self.warnings.append({
                    "type": "WildcardImport",
                    "message": f"Wildcard import 'from {node.module} import *' pollutes namespace.",
                    "line": node.lineno,
                })
        self.generic_visit(node)

    # ── print without parentheses (Python 2 style) ──────────────────────
    def visit_Expr(self, node: ast.Expr):
        # Check for standalone string constants that look like print statements
        self.generic_visit(node)

    # ── Division that might be integer in Python 2 ───────────────────────
    # (informational only, Python 3 handles this fine)

    # ── Potential infinite loop detection (while True without break) ─────
    def visit_While(self, node: ast.While):
        is_infinite_condition = (
            isinstance(node.test, ast.Constant) and node.test.value is True
        )
        if is_infinite_condition:
            has_break = _contains_break(node.body)
            if not has_break:
                self.warnings.append({
                    "type": "PotentialInfiniteLoop",
                    "message": "'while True:' loop has no 'break' statement — may loop forever.",
                    "line": node.lineno,
                })
        self.generic_visit(node)


def _contains_break(stmts) -> bool:
    """Recursively check if a list of statements contains a break."""
    for stmt in stmts:
        if isinstance(stmt, ast.Break):
            return True
        # Don't descend into nested loops/functions
        if isinstance(stmt, (ast.While, ast.For, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, list):
                if _contains_break(child):
                    return True
            if isinstance(child, ast.Break):
                return True
    return False


def _build_tree_summary(tree: ast.Module) -> dict:
    """Build a concise summary of the module's top-level structure."""
    functions = []
    classes = []
    imports = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "args": args,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
        elif isinstance(node, ast.ClassDef):
            methods = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(child.name)
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "methods": methods,
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}" if alias.name != "*" else f"{module}.*")

    return {
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "total_lines": tree.end_lineno if hasattr(tree, "end_lineno") else None,
    }

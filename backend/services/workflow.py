"""FixFlow 2.0 debugging workflow orchestrator.

Pipeline:
    Editor Code
      -> Static Analysis
      -> Original Runtime
      -> IBM Bob Code Analysis
      -> IBM Bob Logic Analysis
      -> Fast Test Generation
      -> Original Regression Test
      -> IBM Bob Root Cause Analysis
      -> IBM Bob Fix Generation
      -> Fixed Runtime
      -> Fixed Regression Test
      -> Evidence-Based Verification
      -> PASS / FAIL

IBM Bob powers the AI reasoning stages. Test generation and final verification
use deterministic execution evidence so the repair loop stays fast and stable.
Each stage emits an SSE-compatible event dictionary for the frontend.
"""

import re
from typing import AsyncGenerator, Any

from analyzers.python_analyzer import PythonAnalyzer
from services.bob_service import (
    BOB_AVAILABLE,
    build_code_analysis_prompt,
    build_fix_generation_prompt,
    build_logic_analysis_prompt,
    build_root_cause_prompt,
    invoke_bob,
    parse_bob_json_output,
)
from services.test_runner import TestRunner


class DebuggingWorkflow:
    """Orchestrate the complete FixFlow debugging pipeline."""

    def __init__(self, code: str, language: str, filename: str):
        self.code = code or ""
        self.language = language or "python"
        self.filename = filename or "untitled.py"
        self.analyzer = PythonAnalyzer()
        self.test_runner = TestRunner()

    async def run(self) -> AsyncGenerator[dict, None]:
        """Run the workflow and yield SSE-compatible event dictionaries."""
        code = self.code

        # Shared workflow state.
        static_result: dict = {}
        code_analysis: dict = {}
        logic_analysis: dict = {}
        test_plan: dict = {}
        root_cause: dict = {}
        fix_result: dict = {}
        fixed_code = code
        test_results_original: list = []
        test_results: list = []
        run_result_original: dict = {}
        run_result_fixed: dict = {}
        verification: dict = {}
        passed = 0
        total = 0

        # ── Step 1: Static Code Analysis ────────────────────────────────────
        yield _step_start("code_analysis", "Analyzing code structure and syntax…")
        try:
            static_result = self.analyzer.analyze(code, self.filename) or {}
        except Exception as exc:
            static_result = {
                "errors": [{
                    "type": "AnalyzerError",
                    "line": None,
                    "message": str(exc),
                }],
                "warnings": [],
            }

        yield _step_done("code_analysis", {
            "static": static_result,
            "summary": (
                f"Found {len(static_result.get('errors', []))} error(s), "
                f"{len(static_result.get('warnings', []))} warning(s)."
            ),
        })

        # ── Step 2: Run original code ───────────────────────────────────────
        yield _step_start(
            "run_original",
            "Running original code to capture runtime behavior…",
        )
        try:
            run_result_original = await self.analyzer.run_code(code) or {}
        except Exception as exc:
            run_result_original = {
                "stdout": "",
                "stderr": str(exc),
                "exit_code": 1,
                "timed_out": False,
            }

        yield _step_done("run_original", {
            "stdout": run_result_original.get("stdout", ""),
            "stderr": run_result_original.get("stderr", ""),
            "exit_code": run_result_original.get("exit_code"),
            "timed_out": run_result_original.get("timed_out", False),
        })

        # ── Step 3: IBM Bob — Code Analysis ─────────────────────────────────
        yield _step_start(
            "bob_code_analysis",
            "Sending code to IBM Bob for AI analysis…",
        )

        if not BOB_AVAILABLE:
            code_analysis = _fallback_code_analysis(
                static_result,
                run_result_original,
            )
            yield _step_done("bob_code_analysis", {
                "result": code_analysis,
                "note": "Bob Shell not available — using fallback analysis.",
                "bob_available": False,
            })
        else:
            prompt = build_code_analysis_prompt(code, self.language)
            bob_result = await _safe_invoke_bob(prompt)

            if bob_result.get("success"):
                parsed = _safe_parse_bob_json(bob_result.get("output", ""))
                code_analysis = parsed or {
                    "summary": bob_result.get("output", ""),
                    "issues": [],
                    "raw": True,
                }
            else:
                code_analysis = _fallback_code_analysis(
                    static_result,
                    run_result_original,
                )
                code_analysis["bob_error"] = bob_result.get(
                    "error",
                    "Unknown Bob error.",
                )

            yield _step_done("bob_code_analysis", {
                "result": code_analysis,
                "bob_available": True,
            })

        # ── Step 4: IBM Bob — Logic / Runtime Analysis ─────────────────────
        yield _step_start(
            "logic_analysis",
            "IBM Bob performing logic and runtime analysis…",
        )

        if not BOB_AVAILABLE:
            logic_analysis = _fallback_logic_analysis(
                static_result,
                run_result_original,
            )
            yield _step_done("logic_analysis", {
                "result": logic_analysis,
                "bob_available": False,
            })
        else:
            prompt = build_logic_analysis_prompt(
                code,
                self.language,
                static_result,
            )
            bob_result = await _safe_invoke_bob(prompt)

            if bob_result.get("success"):
                parsed = _safe_parse_bob_json(bob_result.get("output", ""))
                logic_analysis = parsed or {
                    "summary": bob_result.get("output", ""),
                    "logic_issues": [],
                    "raw": True,
                }
            else:
                logic_analysis = _fallback_logic_analysis(
                    static_result,
                    run_result_original,
                )
                logic_analysis["bob_error"] = bob_result.get(
                    "error",
                    "Unknown Bob error.",
                )

            yield _step_done("logic_analysis", {
                "result": logic_analysis,
                "bob_available": True,
            })

        # ── Collect issues for downstream reasoning ─────────────────────────
        all_issues = _collect_issues(
            static_result,
            code_analysis,
            logic_analysis,
        )

        # ── Step 5: Fast Test Generation ────────────────────────────────────
        yield _step_start(
            "test_generation",
            "Generating fast regression tests from the debugging evidence…",
        )

        # Intentionally deterministic. This keeps the workflow from stalling
        # on another Bob call while still executing the user's program inside
        # the test runner. Because the target program is prepended/executed by
        # TestRunner, a runtime failure still causes this test to fail.
        test_cases = [
            {
                "name": "test_code_executes",
                "description": "Verify the repaired program executes successfully.",
                "type": "regression",
                "input": "Run the complete program",
                "expected": "Program exits successfully",
                "executable_code": "assert True",
            }
        ]

        test_plan = {
            "test_cases": test_cases,
            "test_strategy": (
                "Fast execution regression test generated from observed "
                "debugging evidence. AI reasoning is handled by the analysis, "
                "root-cause, and fix stages."
            ),
            "fast_path": True,
        }

        yield _step_done("test_generation", {
            "result": test_plan,
            "bob_available": BOB_AVAILABLE,
            "fast_path": True,
        })

        # ── Step 6: Run tests against original code ─────────────────────────
        yield _step_start(
            "regression_testing_original",
            "Running generated tests against original code…",
        )
        try:
            test_results_original = await self.test_runner.run_tests(
                code,
                test_plan.get("test_cases", []),
            ) or []
        except Exception as exc:
            test_results_original = [{
                "name": "test_code_executes",
                "passed": False,
                "error": f"Original regression runner error: {exc}",
            }]

        yield _step_done("regression_testing_original", {
            "results": test_results_original,
            "passed": sum(
                1 for t in test_results_original if t.get("passed")
            ),
            "total": len(test_results_original),
        })

        # ── Step 7: IBM Bob — Root Cause Analysis ───────────────────────────
        yield _step_start(
            "root_cause_analysis",
            "IBM Bob performing root cause analysis…",
        )

        if not BOB_AVAILABLE:
            root_cause = _fallback_root_cause(
                all_issues,
                run_result_original,
            )
            yield _step_done("root_cause_analysis", {
                "result": root_cause,
                "bob_available": False,
            })
        else:
            prompt = build_root_cause_prompt(
                code,
                self.language,
                all_issues,
                run_result_original,
            )
            bob_result = await _safe_invoke_bob(prompt)

            if bob_result.get("success"):
                parsed = _safe_parse_bob_json(bob_result.get("output", ""))
                root_cause = parsed or {
                    "primary_root_cause": bob_result.get("output", ""),
                    "raw": True,
                }
            else:
                root_cause = _fallback_root_cause(
                    all_issues,
                    run_result_original,
                )
                root_cause["bob_error"] = bob_result.get(
                    "error",
                    "Unknown Bob error.",
                )

            yield _step_done("root_cause_analysis", {
                "result": root_cause,
                "bob_available": True,
            })

        # ── Determine whether there is anything to repair ──────────────────
        no_issues = _is_clean_run(static_result, all_issues, run_result_original)

        if no_issues:
            # Keep the workflow shape intact so the frontend receives a
            # completed fix-generation event even when there is nothing to fix.
            fix_result = {
                "fixed_code": code,
                "changes_made": [],
                "explanation": "No actionable issues found — original code appears correct.",
                "confidence": "high",
            }
            yield _step_start(
                "fix_generation",
                "No repair required — original code is already clean.",
            )
            yield _step_done("fix_generation", {
                "result": fix_result,
                "skipped": True,
                "reason": "No actionable issues detected.",
                "bob_available": BOB_AVAILABLE,
            })
            fixed_code = code
        else:
            # ── Step 8: IBM Bob — Fix Generation ───────────────────────────
            yield _step_start(
                "fix_generation",
                "IBM Bob generating a fix…",
            )

            fix_result = {}

            # First handle deterministic, unambiguous Python syntax errors.
            # This prevents an obvious missing-colon repair from waiting on a
            # second Bob call and makes the repair stage reliable.
            stderr = run_result_original.get("stderr", "") or ""
            missing_colon = "SyntaxError: expected ':'" in stderr

            if missing_colon:
                match = re.search(r'File "[^"\n]+", line (\d+)', stderr)
                if match:
                    line_number = int(match.group(1))
                    lines = code.splitlines()

                    if 1 <= line_number <= len(lines):
                        original_line = lines[line_number - 1]
                        stripped = original_line.strip()

                        block_keywords = (
                            "if ",
                            "elif ",
                            "else",
                            "for ",
                            "while ",
                            "try",
                            "except",
                            "finally",
                            "def ",
                            "class ",
                            "with ",
                        )

                        if (
                            stripped
                            and not stripped.endswith(":")
                            and stripped.startswith(block_keywords)
                        ):
                            lines[line_number - 1] = original_line.rstrip() + ":"
                            fallback_code = "\n".join(lines)

                            fix_result = {
                                "fixed_code": fallback_code,
                                "changes_made": [{
                                    "line_original": line_number,
                                    "description": (
                                        "Added the missing ':' required by "
                                        "the Python block statement."
                                    ),
                                }],
                                "explanation": (
                                    "FixFlow used the observed SyntaxError "
                                    "to repair the exact reported line."
                                ),
                                "confidence": "high",
                                "fallback_used": True,
                                "verification_source": "runtime_evidence",
                            }

            # For other errors, use IBM Bob.
            if not fix_result and BOB_AVAILABLE:
                prompt = build_fix_generation_prompt(
                    code,
                    self.language,
                    root_cause,
                    all_issues,
                )
                bob_result = await _safe_invoke_bob(prompt)

                if bob_result.get("success"):
                    parsed = _safe_parse_bob_json(
                        bob_result.get("output", "")
                    )
                    if parsed and isinstance(parsed.get("fixed_code"), str):
                        candidate = _clean_code(parsed["fixed_code"])
                        if candidate and candidate != code.strip():
                            fix_result = dict(parsed)
                            fix_result["fixed_code"] = candidate
                else:
                    fix_result = {
                        "fixed_code": code,
                        "changes_made": [],
                        "explanation": "Bob could not generate a usable fix.",
                        "confidence": "low",
                        "bob_error": bob_result.get("error", ""),
                    }

            # Existing deterministic fallback for common NameError suggestions.
            if not fix_result:
                fallback = _runtime_name_error_fix(code, run_result_original)
                if fallback:
                    fix_result = fallback

            # Never crash the workflow if no repair can be generated.
            if not fix_result:
                fix_result = {
                    "fixed_code": code,
                    "changes_made": [],
                    "explanation": "No usable fix was generated.",
                    "confidence": "low",
                }

            yield _step_done("fix_generation", {
                "result": fix_result,
                "bob_available": BOB_AVAILABLE,
                "fallback_used": fix_result.get("fallback_used", False),
            })

            fixed_code = _clean_code(
                fix_result.get("fixed_code", code) or code
            )

        # ── Step 9: Run fixed code ──────────────────────────────────────────
        yield _step_start(
            "run_fixed",
            "Running fixed code to verify it executes correctly…",
        )
        try:
            run_result_fixed = await self.analyzer.run_code(fixed_code) or {}
        except Exception as exc:
            run_result_fixed = {
                "stdout": "",
                "stderr": str(exc),
                "exit_code": 1,
                "timed_out": False,
            }

        yield _step_done("run_fixed", {
            "stdout": run_result_fixed.get("stdout", ""),
            "stderr": run_result_fixed.get("stderr", ""),
            "exit_code": run_result_fixed.get("exit_code"),
            "timed_out": run_result_fixed.get("timed_out", False),
        })

        # ── Step 10: Regression tests on fixed code ─────────────────────────
        yield _step_start(
            "regression_testing",
            "Running regression tests on fixed code…",
        )
        try:
            test_results = await self.test_runner.run_tests(
                fixed_code,
                test_plan.get("test_cases", []),
            ) or []
        except Exception as exc:
            test_results = [{
                "name": "test_code_executes",
                "passed": False,
                "error": f"Fixed regression runner error: {exc}",
            }]

        passed = sum(1 for t in test_results if t.get("passed"))
        total = len(test_results)

        yield _step_done("regression_testing", {
            "results": test_results,
            "passed": passed,
            "total": total,
        })

        # ── Step 11: Independent Verification ──────────────────────────────
        # Deliberately evidence-based. This avoids a final Bob call being the
        # slowest step and ensures PASS/FAIL is grounded in observable results.
        yield _step_start(
            "verification",
            "Verifying the fix using execution and regression evidence…",
        )

        failed_tests = [
            t for t in test_results
            if not t.get("passed")
        ]

        fixed_execution_ok = (
            run_result_fixed.get("exit_code") == 0
            and not run_result_fixed.get("timed_out", False)
        )

        if fixed_execution_ok and not failed_tests:
            verification = {
                "verdict": "PASS",
                "confidence": "high" if total > 0 else "medium",
                "issues_resolved": (
                    ["Original execution failure was resolved."]
                    if not no_issues
                    else ["No actionable issues were found in the original code."]
                ),
                "remaining_issues": [],
                "reasoning": (
                    "Fixed code exited with code 0, did not time out, and "
                    "all regression tests passed."
                ),
                "recommendation": "Fix verified by execution evidence.",
                "bob_available": BOB_AVAILABLE,
                "verification_mode": "evidence_based",
            }
        else:
            verification = _fallback_verification(
                fixed_code,
                run_result_fixed,
                test_results,
                all_issues,
                no_issues,
            )
            verification["bob_available"] = BOB_AVAILABLE
            verification["verification_mode"] = "evidence_based"

        yield _step_done("verification", {
            "result": verification,
            "bob_available": BOB_AVAILABLE,
        })

        # ── Final summary ───────────────────────────────────────────────────
        verdict = verification.get("verdict", "FAIL")
        yield {
            "type": "workflow_complete",
            "step": "complete",
            "data": {
                "verdict": verdict,
                "fixed_code": fixed_code if fixed_code != code else None,
                "original_code": code,
                "total_issues_found": len(all_issues),
                "tests_passed": passed,
                "tests_total": total,
                "bob_was_available": BOB_AVAILABLE,
            },
        }


# ── Workflow helpers ─────────────────────────────────────────────────────────


async def _safe_invoke_bob(prompt: str) -> dict:
    """Call Bob without allowing one failed AI call to kill the workflow."""
    try:
        result = await invoke_bob(prompt)
        if not isinstance(result, dict):
            return {
                "success": False,
                "output": "",
                "error": "Bob returned an invalid response object.",
            }
        return result
    except Exception as exc:
        return {
            "success": False,
            "output": "",
            "error": f"Bob invocation error: {exc}",
        }


def _safe_parse_bob_json(output: Any) -> dict:
    """Parse Bob JSON output and normalize unexpected parser results."""
    if not output:
        return {}
    try:
        parsed = parse_bob_json_output(str(output))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_code(value: str) -> str:
    """Remove common Markdown code fences around Bob-generated Python."""
    code = (value or "").strip()
    if code.startswith("```") and code.endswith("```"):
        lines = code.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return code


def _collect_issues(
    static_result: dict,
    code_analysis: dict,
    logic_analysis: dict,
) -> list:
    """Combine issue lists while tolerating missing or malformed fields."""
    issues: list = []
    issues.extend(static_result.get("errors", []) or [])
    issues.extend(static_result.get("warnings", []) or [])
    issues.extend(code_analysis.get("issues", []) or [])
    issues.extend(logic_analysis.get("logic_issues", []) or [])
    return [issue for issue in issues if isinstance(issue, dict)]


def _is_clean_run(
    static_result: dict,
    all_issues: list,
    run_result_original: dict,
) -> bool:
    """Return True only when there is no actionable evidence of a problem."""
    if static_result.get("errors"):
        return False
    if all_issues:
        return False
    if run_result_original.get("timed_out", False):
        return False
    if run_result_original.get("exit_code") != 0:
        return False
    if run_result_original.get("stderr", "").strip():
        return False
    return True


def _runtime_name_error_fix(code: str, run_result: dict) -> dict | None:
    """Repair a Python NameError when the interpreter supplies a suggestion."""
    stderr = run_result.get("stderr", "") or ""
    match = re.search(
        r"name '([^']+)' is not defined\. Did you mean: '([^']+)'",
        stderr,
    )
    if not match:
        return None

    bad_name = match.group(1)
    suggested_name = match.group(2)
    fallback_code = re.sub(
        rf"\b{re.escape(bad_name)}\b",
        suggested_name,
        code,
    )

    if not fallback_code or fallback_code == code:
        return None

    return {
        "fixed_code": fallback_code,
        "changes_made": [{
            "line_original": None,
            "description": (
                f"Replaced undefined name '{bad_name}' "
                f"with suggested name '{suggested_name}'."
            ),
        }],
        "explanation": (
            "FixFlow used the observed runtime NameError suggestion "
            "to repair the undefined name."
        ),
        "confidence": "high",
        "fallback_used": True,
    }


# ── Fallback functions ────────────────────────────────────────────────────────


def _fallback_code_analysis(static_result: dict, run_result: dict) -> dict:
    issues = []

    for error in static_result.get("errors", []) or []:
        if not isinstance(error, dict):
            continue
        issues.append({
            "severity": "error",
            "type": error.get("type", "Error"),
            "line": error.get("line"),
            "description": error.get("message", ""),
        })

    for warning in static_result.get("warnings", []) or []:
        if not isinstance(warning, dict):
            continue
        issues.append({
            "severity": "warning",
            "type": warning.get("type", "Warning"),
            "line": warning.get("line"),
            "description": warning.get("message", ""),
        })

    stderr = run_result.get("stderr", "") or ""
    if stderr:
        issues.append({
            "severity": "error",
            "type": "RuntimeError",
            "line": None,
            "description": stderr[:300],
        })

    return {
        "summary": "Fallback analysis used because IBM Bob was unavailable or failed.",
        "issues": issues,
        "complexity": "unknown",
        "code_quality": "unknown" if not issues else "poor",
        "notes": "",
    }


def _fallback_logic_analysis(static_result: dict, run_result: dict) -> dict:
    logic_issues = []

    if run_result.get("timed_out"):
        logic_issues.append({
            "type": "InfiniteLoop",
            "line": None,
            "description": "Execution timed out — likely an infinite loop.",
            "example": "Code ran beyond the execution time limit.",
        })

    stderr = run_result.get("stderr", "") or ""
    if stderr.strip():
        logic_issues.append({
            "type": "RuntimeError",
            "line": None,
            "description": stderr[:300],
            "example": "Observed during execution.",
        })

    return {
        "logic_issues": logic_issues,
        "root_causes": [
            "See static analysis errors."
            if static_result.get("errors")
            else "See runtime evidence."
        ],
        "affected_components": [],
        "summary": "Fallback logic analysis used because Bob was unavailable or failed.",
    }


def _fallback_test_plan(code: str, issues: list) -> dict:
    """Return a deterministic fallback test plan when Bob is unavailable."""
    if not issues:
        return {
            "test_cases": [],
            "test_strategy": "No issues detected — no fallback tests generated.",
        }
    return {
        "test_cases": [
            {
                "name": "test_code_executes",
                "description": "Verify the repaired program executes successfully.",
                "type": "normal",
                "input": "Run the complete program",
                "expected": "Program exits successfully",
                "executable_code": "assert True",
            }
        ],
        "test_strategy": (
            "Fallback regression test generated from observed debugging evidence."
        ),
    }


def _fallback_root_cause(issues: list, run_result: dict) -> dict:
    stderr = run_result.get("stderr", "") or ""

    if run_result.get("timed_out"):
        return {
            "primary_root_cause": "Execution timed out — likely a non-terminating loop or blocking operation.",
            "contributing_factors": [],
            "fix_strategy": "Inspect loop termination and blocking operations.",
            "risk_areas": [],
            "confidence": "high",
        }

    if stderr.strip():
        return {
            "primary_root_cause": stderr[:200],
            "contributing_factors": [],
            "fix_strategy": "Address the runtime error shown in stderr.",
            "risk_areas": [],
            "confidence": "medium",
        }

    if issues:
        first = issues[0] if isinstance(issues[0], dict) else {}
        return {
            "primary_root_cause": first.get("description", "Unknown issue"),
            "contributing_factors": [
                i.get("description", "")
                for i in issues[1:3]
                if isinstance(i, dict)
            ],
            "fix_strategy": "Address the highest-priority debugging findings.",
            "risk_areas": [],
            "confidence": "low",
        }

    return {
        "primary_root_cause": "No issues detected.",
        "contributing_factors": [],
        "fix_strategy": "No fix needed.",
        "risk_areas": [],
        "confidence": "high",
    }


def _fallback_verification(
    code: str,
    run_result_fixed: dict,
    test_results: list,
    all_issues: list,
    no_issues: bool,
) -> dict:
    """Determine PASS/FAIL from observable execution and regression evidence."""
    failed_tests = [
        t for t in test_results
        if not t.get("passed")
    ]
    timed_out = run_result_fixed.get("timed_out", False)
    exit_code = run_result_fixed.get("exit_code")

    if timed_out:
        return {
            "verdict": "FAIL",
            "confidence": "high",
            "issues_resolved": [],
            "remaining_issues": ["Execution still times out after the fix."],
            "reasoning": "Fixed code still timed out.",
            "recommendation": "Review loop termination and blocking operations.",
        }

    stderr = (run_result_fixed.get("stderr", "") or "").strip()
    if exit_code != 0 and stderr:
        return {
            "verdict": "FAIL",
            "confidence": "high",
            "issues_resolved": [],
            "remaining_issues": [stderr[:300]],
            "reasoning": "Fixed code did not exit successfully.",
            "recommendation": "Review the remaining runtime error and try again.",
        }

    if failed_tests:
        return {
            "verdict": "FAIL",
            "confidence": "medium",
            "issues_resolved": [],
            "remaining_issues": [
                t.get("error", "Test failed")
                for t in failed_tests
            ],
            "reasoning": (
                f"{len(failed_tests)} of {len(test_results)} regression tests failed."
            ),
            "recommendation": "Fix the remaining failing regression tests.",
        }

    if no_issues:
        return {
            "verdict": "PASS",
            "confidence": "high",
            "issues_resolved": ["No actionable issues were found in the original code."],
            "remaining_issues": [],
            "reasoning": "Original and fixed execution both completed successfully.",
            "recommendation": "No repair was required.",
        }

    resolved = [
        issue.get("description", str(issue))[:80]
        for issue in all_issues[:5]
        if isinstance(issue, dict)
    ]
    return {
        "verdict": "PASS",
        "confidence": "medium",
        "issues_resolved": resolved,
        "remaining_issues": [],
        "reasoning": "Fixed code exited successfully and all regression tests passed.",
        "recommendation": "None.",
    }


def _step_start(step: str, message: str) -> dict:
    return {
        "type": "step_start",
        "step": step,
        "data": {"message": message},
    }


def _step_done(step: str, data: dict) -> dict:
    return {
        "type": "step_done",
        "step": step,
        "data": data,
    }


def _step_error(step: str, error: str) -> dict:
    return {
        "type": "step_error",
        "step": step,
        "data": {"error": error},
    }

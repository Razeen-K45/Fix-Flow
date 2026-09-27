"""
IBM Bob Shell Integration Service
Invokes Bob Shell (bob -p "...") as a subprocess to perform AI-powered
analysis, debugging, test generation, fix generation, and verification.

Bob Shell CLI: https://bob.ibm.com
Authentication: Set BOBSHELL_API_KEY environment variable.
Usage: bob -p "prompt" --hide-intermediary-output
"""

import asyncio
import os
import shutil
import json
import re
import textwrap
import subprocess
from typing import Optional

# Execution timeout for a single Bob Shell invocation
BOB_TIMEOUT = 10  # seconds

# Whether Bob Shell is available on this system
BOB_COMMAND = shutil.which("bob.cmd") or shutil.which("bob")
BOB_AVAILABLE = BOB_COMMAND is not None


async def invoke_bob(prompt: str, working_dir: Optional[str] = None) -> dict:
    """
    Invoke the current Bob Shell CLI in headless mode.

    Uses a worker thread instead of asyncio.create_subprocess_exec
    because Windows/Python 3.14 can raise NotImplementedError for
    asyncio subprocess transports.
    """

    if not BOB_AVAILABLE:
        return {
            "success": False,
            "output": "",
            "error": (
                "Bob Shell is not installed or not on PATH. "
                "Install Bob Shell and configure BOB_API_KEY."
            ),
            "bob_available": False,
        }

    cmd = [
        BOB_COMMAND,
        "run",
        prompt,
    ]
    print("DEBUG BOB PROMPT:", prompt[:500])

    env = os.environ.copy()

    def execute_bob():
        try:
            result = subprocess.run(
                cmd,
                cwd=working_dir or os.getcwd(),
                env=env,
                capture_output=True,
                text=True,
                timeout=BOB_TIMEOUT,
                encoding="utf-8",
                errors="replace",
            )

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()

            if result.returncode != 0:
                return {
                    "success": False,
                    "output": stdout,
                    "error": (
                        stderr
                        or f"Bob Shell exited with code {result.returncode}."
                    ),
                    "bob_available": True,
                }

            return {
                "success": True,
                "output": stdout,
                "error": "",
                "bob_available": True,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Bob Shell timed out after {BOB_TIMEOUT} seconds.",
                "bob_available": True,
            }

        except FileNotFoundError:
            return {
                "success": False,
                "output": "",
                "error": "bob command not found. Is Bob Shell installed and on PATH?",
                "bob_available": False,
            }

        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"Unexpected error invoking Bob Shell: {e}",
                "bob_available": True,
            }

    return await asyncio.to_thread(execute_bob)


# ── Structured Bob prompts for each workflow step ─────────────────────────────

def build_code_analysis_prompt(code: str, language: str) -> str:
    return textwrap.dedent(f"""
    You are a senior software engineer performing code analysis.

    Analyze the following {language} code and provide a structured analysis.

    Return your response as a JSON object with this exact structure:
    {{
      "summary": "brief one-sentence summary of what the code does",
      "issues": [
        {{
          "severity": "error|warning|info",
          "type": "SyntaxError|LogicError|RuntimeError|StyleIssue|etc",
          "line": <line_number_or_null>,
          "description": "clear description of the issue"
        }}
      ],
      "complexity": "low|medium|high",
      "code_quality": "good|acceptable|poor",
      "notes": "any additional observations"
    }}

    CODE TO ANALYZE:
    ```{language}
    {code}
    ```

    Respond with ONLY the JSON object, no other text.
    """).strip()


def build_logic_analysis_prompt(code: str, language: str, static_analysis: dict) -> str:
    issues_text = json.dumps(static_analysis.get("errors", []) + static_analysis.get("warnings", []), indent=2)
    return textwrap.dedent(f"""
    You are a senior software engineer performing deep logic analysis.

    Analyze the logic and runtime behavior of the following {language} code.
    Static analysis has already found these issues:
    {issues_text}

    Now perform logic analysis and identify:
    - Algorithmic errors
    - Edge cases not handled
    - Incorrect assumptions
    - Data flow problems
    - Potential runtime errors (index out of range, type errors, division by zero, etc.)

    Return your response as a JSON object:
    {{
      "logic_issues": [
        {{
          "type": "string",
          "line": <number_or_null>,
          "description": "clear description",
          "example": "what input would trigger this"
        }}
      ],
      "root_causes": ["list of root causes of the main problems"],
      "affected_components": ["list of functions/classes/sections affected"],
      "summary": "overall logic assessment"
    }}

    CODE:
    ```{language}
    {code}
    ```

    Respond with ONLY the JSON object.
    """).strip()


def build_test_generation_prompt(code: str, language: str, issues: list) -> str:
    issues_text = json.dumps(issues, indent=2)
    return textwrap.dedent(f"""
    You are a senior software engineer writing test cases.

    Generate test cases for the following {language} code.
    Known issues to test against:
    {issues_text}

    Generate tests that:
    1. Test normal expected behavior
    2. Test edge cases
    3. Specifically target the known issues above
    4. Test boundary conditions

    Return your response as a JSON object:
    {{
      "test_cases": [
        {{
          "name": "test name",
          "description": "what is being tested",
          "type": "normal|edge_case|regression|boundary",
          "input": "description of input or test setup",
          "expected": "expected output or behavior",
          "executable_code": "complete runnable Python test code using assert statements"
        }}
      ],
      "test_strategy": "brief explanation of the testing approach"
    }}

    CODE UNDER TEST:
    ```{language}
    {code}
    ```

    Respond with ONLY the JSON object.
    """).strip()


def build_root_cause_prompt(code: str, language: str, all_issues: list, run_result: dict) -> str:
    return textwrap.dedent(f"""
    You are a senior software engineer performing root cause analysis.

    Analyze the following {language} code and determine the root causes of its problems.

    Static/Logic Issues Found:
    {json.dumps(all_issues, indent=2)}

    Execution Result:
    - Exit Code: {run_result.get('exit_code', 'N/A')}
    - Stdout: {run_result.get('stdout', '')[:500]}
    - Stderr: {run_result.get('stderr', '')[:500]}
    - Timed Out: {run_result.get('timed_out', False)}

    Perform root cause analysis and return a JSON object:
    {{
      "primary_root_cause": "the single most important root cause",
      "contributing_factors": ["list of contributing factors"],
      "fix_strategy": "recommended approach to fix the code",
      "risk_areas": ["areas of code that need careful attention during fix"],
      "confidence": "high|medium|low"
    }}

    CODE:
    ```{language}
    {code}
    ```

    Respond with ONLY the JSON object.
    """).strip()


def build_fix_generation_prompt(code: str, language: str, root_cause: dict, all_issues: list) -> str:
    return textwrap.dedent(f"""
    You are a senior software engineer generating a bug fix.

    Fix the following {language} code based on the root cause analysis.

    Root Cause Analysis:
    {json.dumps(root_cause, indent=2)}

    Issues to Fix:
    {json.dumps(all_issues, indent=2)}

    IMPORTANT RULES:
    - Fix ONLY the identified issues, do not rewrite the entire code unnecessarily
    - Preserve the original code structure and intent
    - Do not add unnecessary complexity
    - The fixed code must be complete and runnable

    Return your response as a JSON object:
    {{
      "fixed_code": "the complete corrected code",
      "changes_made": [
        {{
          "line_original": <number_or_null>,
          "description": "what was changed and why"
        }}
      ],
      "explanation": "plain English explanation of the fix",
      "confidence": "high|medium|low"
    }}

    ORIGINAL CODE:
    ```{language}
    {code}
    ```

    Respond with ONLY the JSON object.
    """).strip()


def build_verification_prompt(
    original_code: str,
    fixed_code: str,
    language: str,
    test_results: list,
    run_result_original: dict,
    run_result_fixed: dict,
) -> str:
    return textwrap.dedent(f"""
    You are an independent software engineer performing final verification.

    You must verify whether the fix actually resolves the original problems.
    Be critical and objective — do not rubber-stamp a broken fix.

    ORIGINAL CODE:
    ```{language}
    {original_code}
    ```

    FIXED CODE:
    ```{language}
    {fixed_code}
    ```

    Original Code Execution:
    - Exit Code: {run_result_original.get('exit_code', 'N/A')}
    - Stdout: {run_result_original.get('stdout', '')[:300]}
    - Stderr: {run_result_original.get('stderr', '')[:300]}

    Fixed Code Execution:
    - Exit Code: {run_result_fixed.get('exit_code', 'N/A')}
    - Stdout: {run_result_fixed.get('stdout', '')[:300]}
    - Stderr: {run_result_fixed.get('stderr', '')[:300]}

    Test Results:
    {json.dumps(test_results, indent=2)}

    Evaluate:
    1. Does the fixed code actually fix the identified issues?
    2. Does it introduce any new issues?
    3. Do the test results support that the fix works?
    4. Is the fixed code correct and complete?

    Return a JSON object:
    {{
      "verdict": "PASS" or "FAIL",
      "confidence": "high|medium|low",
      "issues_resolved": ["list of issues that were successfully fixed"],
      "remaining_issues": ["list of issues that are still present or new issues introduced"],
      "reasoning": "detailed explanation of the verdict",
      "recommendation": "what to do next if FAIL"
    }}

    Respond with ONLY the JSON object.
    """).strip()
def extract_fixed_code(output: str) -> Optional[str]:
    """
    Extract corrected source code from Bob when it does not return
    the expected JSON structure.
    """
    if not output:
        return None

    parsed = parse_bob_json_output(output)
    if parsed and isinstance(parsed.get("fixed_code"), str):
        return parsed["fixed_code"].strip()

    matches = re.findall(r"```(?:python|py)?\s*([\s\S]*?)```", output)
    if matches:
        return max(matches, key=len).strip()

    return None


def parse_bob_json_output(output: str) -> Optional[dict]:
    """
    Extract JSON from Bob's output. Bob may include markdown fences or extra text.
    Returns the parsed dict or None if parsing fails.
    """
    if not output:
        return None

    # Try direct parse first
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    fence_patterns = [
        r"```json\s*([\s\S]*?)\s*```",
        r"```\s*([\s\S]*?)\s*```",
    ]
    for pattern in fence_patterns:
        match = re.search(pattern, output, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # Find first balanced {...} block
    start = output.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(output[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = output[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    return None

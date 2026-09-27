"""
Tests for the FastAPI routes in main.py

Covers:
  - GET  /api/health
  - POST /api/analyze
  - POST /api/run
  - POST /api/correct
  - POST /api/workflow/stream  (basic SSE smoke test — no Bob required)

Uses FastAPI's TestClient (synchronous wrapper around httpx).
The frontend static-file mount is patched out so the tests don't require
the frontend directory to exist.
"""

import pytest
import sys
import os
import json
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Patch the static files mount before importing app ────────────────────────
# StaticFiles raises if the directory doesn't exist; bypass it in tests.
import fastapi.staticfiles

_orig_static_init = fastapi.staticfiles.StaticFiles.__init__

def _noop_static_init(self, *args, **kwargs):
    self.all_files: list = []
    self.packages = None

fastapi.staticfiles.StaticFiles.__init__ = _noop_static_init


from fastapi.testclient import TestClient
from main import app

client = TestClient(app, raise_server_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/health
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_response_has_status_ok(self):
        resp = client.get("/api/health")
        data = resp.json()
        assert data["status"] == "ok"

    def test_response_has_bob_shell_available_key(self):
        data = client.get("/api/health").json()
        assert "bob_shell_available" in data

    def test_response_has_hint_key(self):
        data = client.get("/api/health").json()
        assert "bob_shell_hint" in data

    def test_bob_shell_available_is_bool(self):
        data = client.get("/api/health").json()
        assert isinstance(data["bob_shell_available"], bool)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/analyze
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyzeEndpoint:
    def _post(self, code, language="python", filename="test.py"):
        return client.post(
            "/api/analyze",
            json={"code": code, "language": language, "filename": filename},
        )

    def test_returns_200(self):
        assert self._post("x = 1").status_code == 200

    def test_clean_code_status_ok(self):
        data = self._post("x = 1 + 1").json()
        assert data["status"] == "ok"

    def test_response_has_errors_key(self):
        data = self._post("x = 1").json()
        assert "errors" in data

    def test_response_has_warnings_key(self):
        data = self._post("x = 1").json()
        assert "warnings" in data

    def test_syntax_error_returns_error_status(self):
        data = self._post("def foo(:\n    pass").json()
        assert data["status"] == "error"
        assert len(data["errors"]) >= 1

    def test_syntax_error_contains_type(self):
        data = self._post("x = (").json()
        assert data["errors"][0]["type"] == "SyntaxError"

    def test_empty_code_returns_ok_no_errors(self):
        data = self._post("   ").json()
        assert data["status"] == "ok"
        assert data["errors"] == []

    def test_warning_code_has_warning_in_response(self):
        data = self._post("x = None\nif x == None: pass").json()
        assert data["status"] == "warning"
        warning_types = [w["type"] for w in data["warnings"]]
        assert "CompareToNone" in warning_types

    def test_tree_summary_present_for_valid_code(self):
        data = self._post("def foo(): pass").json()
        assert data["tree_summary"] is not None

    def test_tree_summary_has_functions_key(self):
        data = self._post("def foo(): pass").json()
        assert "functions" in data["tree_summary"]

    def test_wildcard_import_warning(self):
        data = self._post("from os import *").json()
        types = [w["type"] for w in data["warnings"]]
        assert "WildcardImport" in types

    def test_bare_except_warning(self):
        data = self._post("try:\n    pass\nexcept:\n    pass").json()
        types = [w["type"] for w in data["warnings"]]
        assert "BareExcept" in types

    def test_filename_accepted(self):
        resp = self._post("x = 1", filename="myfile.py")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/run
# ─────────────────────────────────────────────────────────────────────────────

class TestRunEndpoint:
    def _post(self, code, stdin=""):
        return client.post("/api/run", json={"code": code, "language": "python", "stdin": stdin})

    def test_returns_200(self):
        assert self._post("print('hi')").status_code == 200

    def test_successful_run_exit_code_zero(self):
        data = self._post("print('hello')").json()
        assert data["exit_code"] == 0

    def test_stdout_captured(self):
        data = self._post("print('hello world')").json()
        assert "hello world" in data["stdout"]

    def test_runtime_error_nonzero_exit(self):
        data = self._post("1 / 0").json()
        assert data["exit_code"] != 0

    def test_runtime_error_in_stderr(self):
        data = self._post("raise ValueError('test error')").json()
        assert "ValueError" in data["stderr"] or data["exit_code"] != 0

    def test_response_has_required_keys(self):
        data = self._post("pass").json()
        for key in ("stdout", "stderr", "exit_code", "timed_out"):
            assert key in data

    def test_empty_code_returns_no_code_error(self):
        data = self._post("   ").json()
        assert "No code to run" in data["stderr"]
        assert data["exit_code"] == 1

    def test_stdin_passed_to_code(self):
        code = "x = input()\nprint('got:', x)"
        data = self._post(code, stdin="world").json()
        assert data["exit_code"] == 0
        assert "got: world" in data["stdout"]

    def test_timed_out_is_bool(self):
        data = self._post("x = 1").json()
        assert isinstance(data["timed_out"], bool)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/correct
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrectEndpoint:
    def _post(self, code, cursor_position=None, language="python"):
        if cursor_position is None:
            cursor_position = len(code)
        return client.post(
            "/api/correct",
            json={"code": code, "cursor_position": cursor_position, "language": language},
        )

    def test_returns_200(self):
        assert self._post("print('hi')").status_code == 200

    def test_response_has_corrections_key(self):
        data = self._post("print('hi')").json()
        assert "corrections" in data

    def test_clean_code_returns_empty_corrections(self):
        data = self._post("print('hello')").json()
        assert data["corrections"] == []

    def test_typo_returns_correction(self):
        code = "pritn('hi')"
        data = self._post(code, cursor_position=len("pritn")).json()
        corrections = data["corrections"]
        assert any(c["original"] == "pritn" and c["corrected"] == "print" for c in corrections)

    def test_correction_has_required_fields(self):
        code = "retrun x"
        data = self._post(code, cursor_position=6).json()
        if data["corrections"]:
            c = data["corrections"][0]
            for key in ("original", "corrected", "confidence", "start", "end", "line", "col"):
                assert key in c

    def test_empty_code_returns_empty_corrections(self):
        data = self._post("   ", cursor_position=0).json()
        assert data["corrections"] == []

    def test_java_typo_detected(self):
        code = "pubilc class Foo {}"
        data = self._post(code, cursor_position=len("pubilc"), language="java").json()
        corrections = data["corrections"]
        assert any(c["original"] == "pubilc" for c in corrections)

    def test_confidence_above_threshold(self):
        """All returned corrections must meet the minimum confidence threshold."""
        from services.corrector import MIN_CONFIDENCE
        code = "pritn('hi')"
        data = self._post(code, cursor_position=len("pritn")).json()
        for c in data["corrections"]:
            assert c["confidence"] >= MIN_CONFIDENCE


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/workflow/stream
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkflowStreamEndpoint:
    def _post(self, code, language="python"):
        return client.post(
            "/api/workflow/stream",
            json={"code": code, "language": language, "filename": "test.py"},
        )

    def test_empty_code_returns_400(self):
        resp = self._post("   ")
        assert resp.status_code == 400

    def test_returns_200_for_valid_code(self):
        resp = self._post("x = 1")
        assert resp.status_code == 200

    def test_content_type_is_event_stream(self):
        resp = self._post("x = 1")
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_response_body_has_sse_data_lines(self):
        resp = self._post("x = 1")
        assert b"data:" in resp.content

    def test_done_event_emitted(self):
        resp = self._post("x = 1")
        # The last SSE event should be the done sentinel
        assert b'"type": "done"' in resp.content

    def test_code_analysis_step_emitted(self):
        resp = self._post("x = 1 + 1")
        assert b"code_analysis" in resp.content

    def test_workflow_complete_event_emitted(self):
        resp = self._post("x = 1")
        assert b"workflow_complete" in resp.content

    def test_syntax_error_code_still_streams(self):
        """Even broken code should stream events (no 500)."""
        resp = self._post("def foo(:\n    pass")
        assert resp.status_code == 200
        assert b"data:" in resp.content

    def test_sse_events_are_valid_json(self):
        """Each 'data:' line should contain valid JSON."""
        resp = self._post("x = 1")
        lines = resp.content.decode("utf-8").splitlines()
        data_lines = [l[len("data: "):] for l in lines if l.startswith("data: ")]
        assert len(data_lines) >= 1
        for line in data_lines:
            parsed = json.loads(line)
            assert "type" in parsed

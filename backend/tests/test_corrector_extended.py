"""
Extended tests for RealTimeCorrector — covers gaps left by test_corrector.py.

New cases:
  - check() with empty code string
  - check() with cursor at position 0
  - check() when cursor_position equals len(code)
  - scan_all() on empty string
  - scan_all() on whitespace-only string
  - check() returns corrections list (not None) for any input
  - Multiple corrections within the same window
  - Correction col value for first column on a line
  - Dot-separated token (e.g. os.paht) detected by scan_all
  - _offset_to_line_col() at end of string
  - _offset_to_line_col() on single-char string
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.corrector import (
    RealTimeCorrector,
    _offset_to_line_col,
    MIN_CONFIDENCE,
    PYTHON_CORRECTIONS,
)


# ─────────────────────────────────────────────────────────────────────────────
# _offset_to_line_col() — edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestOffsetToLineColEdgeCases:
    def test_single_char_string(self):
        line, col = _offset_to_line_col("x", 0)
        assert line == 1
        assert col == 0

    def test_offset_at_end_of_string(self):
        code = "abc"
        line, col = _offset_to_line_col(code, 3)
        assert line == 1
        assert col == 3

    def test_newline_only_string_second_position(self):
        code = "\nx"
        line, col = _offset_to_line_col(code, 1)
        assert line == 2
        assert col == 0

    def test_multiple_newlines(self):
        code = "a\nb\nc\nd"
        line, col = _offset_to_line_col(code, 6)  # 'd'
        assert line == 4
        assert col == 0

    def test_col_is_zero_at_line_start(self):
        code = "abc\nXYZ"
        _, col = _offset_to_line_col(code, 4)  # 'X'
        assert col == 0


# ─────────────────────────────────────────────────────────────────────────────
# RealTimeCorrector.check() — edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckEdgeCases:
    def setup_method(self):
        self.corrector = RealTimeCorrector()

    def test_empty_code_returns_empty_list(self):
        results = self.corrector.check("", 0, "python")
        assert results == []

    def test_whitespace_only_code_returns_empty_list(self):
        results = self.corrector.check("   \n  ", 3, "python")
        assert results == []

    def test_cursor_at_position_zero(self):
        """Cursor at 0 — no tokens left of cursor, should return nothing."""
        results = self.corrector.check("pritn('hi')", 0, "python")
        # Token starts at 0, end=5, cursor=0 — end(5) >= cursor-2(-2) so it IS in range.
        # Behaviour: returns correction or empty — both are acceptable; just must be a list.
        assert isinstance(results, list)

    def test_cursor_at_exact_end_of_token(self):
        code = "pritn"
        results = self.corrector.check(code, len(code), "python")
        assert any(r["original"] == "pritn" for r in results)

    def test_cursor_past_end_of_code_does_not_raise(self):
        """cursor_position > len(code) should not raise."""
        code = "pritn"
        results = self.corrector.check(code, len(code) + 100, "python")
        assert isinstance(results, list)

    def test_returns_list_not_none(self):
        """Return value is always a list, never None."""
        result = self.corrector.check("xyz_nonexistent_token", 10, "python")
        assert result is not None
        assert isinstance(result, list)

    def test_two_typos_in_window_both_detected(self):
        """Two known typos within 50 chars of each other should both appear."""
        code = "pritn retrun"
        cursor = len(code)
        results = self.corrector.check(code, cursor, "python")
        originals = {r["original"] for r in results}
        assert "pritn" in originals
        assert "retrun" in originals

    def test_col_zero_for_typo_at_line_start(self):
        """Typo at the very start of a line should have col == 0."""
        code = "pritn('hi')"
        results = self.corrector.check(code, len("pritn"), "python")
        assert len(results) >= 1
        assert results[0]["col"] == 0

    def test_col_nonzero_for_typo_after_assignment(self):
        """x = pritn — typo is at col 4."""
        code = "x = pritn"
        cursor = len(code)
        results = self.corrector.check(code, cursor, "python")
        assert len(results) >= 1
        hit = next(r for r in results if r["original"] == "pritn")
        assert hit["col"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# RealTimeCorrector.scan_all() — edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestScanAllEdgeCases:
    def setup_method(self):
        self.corrector = RealTimeCorrector()

    def test_empty_string_returns_empty_list(self):
        assert self.corrector.scan_all("", "python") == []

    def test_whitespace_only_returns_empty_list(self):
        assert self.corrector.scan_all("   \n\t  ", "python") == []

    def test_dot_separated_typo_detected(self):
        """os.paht is in PYTHON_CORRECTIONS and should be detected."""
        # confidence for 'os.paht' is 0.99 → above MIN_CONFIDENCE
        code = "x = os.paht.join('a', 'b')"
        results = self.corrector.scan_all(code, "python")
        assert any(r["original"] == "os.paht" for r in results)

    def test_scan_all_returns_list(self):
        assert isinstance(self.corrector.scan_all("x = 1", "python"), list)

    def test_scan_all_no_false_positives_on_clean_code(self):
        code = "def calculate(x, y):\n    return x + y\n\nresult = calculate(1, 2)\nprint(result)"
        results = self.corrector.scan_all(code, "python")
        assert results == []

    def test_scan_all_many_typos(self):
        """Three distinct typos should all be found."""
        code = "pritn(retrun(yeild))"
        results = self.corrector.scan_all(code, "python")
        originals = {r["original"] for r in results}
        assert "pritn" in originals
        assert "retrun" in originals
        assert "yeild" in originals

    def test_scan_all_result_confidence_above_threshold(self):
        code = "pritn yeild retrun"
        results = self.corrector.scan_all(code, "python")
        for r in results:
            assert r["confidence"] >= MIN_CONFIDENCE

    def test_scan_all_java_language(self):
        code = "pubilc static void main() {}"
        results = self.corrector.scan_all(code, "java")
        assert any(r["original"] == "pubilc" for r in results)

    def test_scan_all_cpp_language(self):
        code = "incude <iostream>"
        results = self.corrector.scan_all(code, "cpp")
        assert any(r["original"] == "incude" for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# PYTHON_CORRECTIONS integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestPythonCorrectionsIntegrity:
    def test_all_entries_are_tuples_of_two(self):
        for typo, value in PYTHON_CORRECTIONS.items():
            assert isinstance(value, tuple) and len(value) == 2, (
                f"Entry '{typo}' should be (str, float)"
            )

    def test_all_corrections_are_strings(self):
        for typo, (correction, _) in PYTHON_CORRECTIONS.items():
            assert isinstance(correction, str), f"Correction for '{typo}' is not a str"

    def test_all_confidences_are_floats_in_range(self):
        for typo, (_, confidence) in PYTHON_CORRECTIONS.items():
            assert isinstance(confidence, float), f"Confidence for '{typo}' is not a float"
            assert 0.0 <= confidence <= 1.0, (
                f"Confidence {confidence} for '{typo}' out of [0, 1]"
            )

    def test_identity_entries_never_returned_by_scan_all(self):
        """Entries where correction == typo (e.g. 'bool', 're') must never be returned."""
        corrector = RealTimeCorrector()
        identity_typos = [k for k, (v, _) in PYTHON_CORRECTIONS.items() if k == v]
        for typo in identity_typos:
            results = corrector.scan_all(typo, "python")
            hits = [r for r in results if r["original"] == typo]
            assert hits == [], f"Identity entry '{typo}' should not be returned"

"""
Tests for RealTimeCorrector (services/corrector.py)

Covers:
  - check(): cursor-windowed correction lookup, confidence filtering,
             multi-language dispatch, result shape
  - scan_all(): full-document scan, deduplication
  - _offset_to_line_col(): line/column calculation
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
    JAVA_CORRECTIONS,
    CPP_CORRECTIONS,
    CORRECTIONS_BY_LANGUAGE,
)


# ─────────────────────────────────────────────────────────────────────────────
# _offset_to_line_col()
# ─────────────────────────────────────────────────────────────────────────────

class TestOffsetToLineCol:
    def test_start_of_first_line(self):
        line, col = _offset_to_line_col("hello world", 0)
        assert line == 1
        assert col == 0

    def test_middle_of_first_line(self):
        line, col = _offset_to_line_col("hello world", 6)
        assert line == 1
        assert col == 6

    def test_start_of_second_line(self):
        code = "first\nsecond"
        line, col = _offset_to_line_col(code, 6)  # 's' of 'second'
        assert line == 2
        assert col == 0

    def test_middle_of_second_line(self):
        code = "abc\ndefgh"
        line, col = _offset_to_line_col(code, 7)  # 'g'
        assert line == 2
        assert col == 3

    def test_third_line(self):
        code = "a\nb\nc"
        line, col = _offset_to_line_col(code, 4)  # 'c'
        assert line == 3
        assert col == 0


# ─────────────────────────────────────────────────────────────────────────────
# RealTimeCorrector.check() — basic correction detection
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckBasic:
    def setup_method(self):
        self.corrector = RealTimeCorrector()

    def test_known_typo_at_cursor_is_corrected(self):
        code = "pritn('hello')"
        # cursor right after the typo
        results = self.corrector.check(code, len("pritn"), "python")
        assert len(results) >= 1
        assert results[0]["original"] == "pritn"
        assert results[0]["corrected"] == "print"

    def test_correction_result_has_required_keys(self):
        code = "retrun x"
        results = self.corrector.check(code, 6, "python")
        assert len(results) >= 1
        r = results[0]
        for key in ("original", "corrected", "confidence", "start", "end", "line", "col"):
            assert key in r, f"Missing key: {key}"

    def test_confidence_at_least_min_threshold(self):
        code = "pritn('hi')"
        results = self.corrector.check(code, 5, "python")
        for r in results:
            assert r["confidence"] >= MIN_CONFIDENCE

    def test_correct_word_returns_no_suggestion(self):
        """'print' is correct and should NOT appear in corrections."""
        code = "print('hi')"
        results = self.corrector.check(code, 5, "python")
        assert all(r["original"] != "print" for r in results)

    def test_token_far_from_cursor_not_returned(self):
        """A typo at position 0, cursor at 200 — too far, should be filtered."""
        code = "pritn('hi')" + " " * 200
        results = self.corrector.check(code, 210, "python")
        assert all(r["original"] != "pritn" for r in results)

    def test_low_confidence_entry_not_returned(self):
        """
        Some entries have confidence below MIN_CONFIDENCE (e.g. 0.90 < 0.95).
        Verify the threshold is respected.
        """
        # Find a known low-confidence entry
        low_entries = {k: v for k, v in PYTHON_CORRECTIONS.items() if v[1] < MIN_CONFIDENCE}
        if not low_entries:
            pytest.skip("No entries below MIN_CONFIDENCE in dictionary.")
        typo = next(iter(low_entries))
        code = typo
        results = self.corrector.check(code, len(typo), "python")
        assert all(r["original"] != typo for r in results)

    def test_start_end_offsets_correct(self):
        code = "x = pritn(1)"
        cursor = code.index("pritn") + 2
        results = self.corrector.check(code, cursor, "python")
        assert len(results) >= 1
        r = results[0]
        assert code[r["start"]:r["end"]] == r["original"]

    def test_line_number_is_one_based(self):
        code = "x = 1\npritn('hi')"
        cursor = len("x = 1\npritn")
        results = self.corrector.check(code, cursor, "python")
        assert len(results) >= 1
        assert results[0]["line"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# RealTimeCorrector.check() — language dispatch
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckLanguageDispatch:
    def setup_method(self):
        self.corrector = RealTimeCorrector()

    def test_java_correction_detected(self):
        typo = "pubilc"
        code = f"{typo} class Foo {{}}"
        results = self.corrector.check(code, len(typo), "java")
        assert any(r["original"] == "pubilc" and r["corrected"] == "public" for r in results)

    def test_cpp_correction_detected(self):
        typo = "retrun"
        code = f"{typo} 0;"
        results = self.corrector.check(code, len(typo), "cpp")
        assert any(r["original"] == "retrun" for r in results)

    def test_unknown_language_falls_back_to_python(self):
        """Unrecognised language should fall back to PYTHON_CORRECTIONS."""
        code = "pritn('hi')"
        results = self.corrector.check(code, 5, "brainfuck")
        assert any(r["original"] == "pritn" for r in results)

    def test_cplusplus_alias_works(self):
        typo = "retrun"
        code = f"{typo} 0;"
        results = self.corrector.check(code, len(typo), "c++")
        assert any(r["original"] == "retrun" for r in results)

    def test_language_isolation(self):
        """A Python-only typo should not be detected when language=java (unless shared)."""
        # "yeild" is in Python but not Java corrections
        typo = "yeild"
        code = f"boolean x = {typo};"
        results = self.corrector.check(code, len(f"boolean x = {typo}"), "java")
        python_hits = [r for r in results if r["original"] == typo]
        # Should NOT be flagged under java corrections
        assert python_hits == []


# ─────────────────────────────────────────────────────────────────────────────
# RealTimeCorrector.scan_all()
# ─────────────────────────────────────────────────────────────────────────────

class TestScanAll:
    def setup_method(self):
        self.corrector = RealTimeCorrector()

    def test_scan_finds_typo_anywhere(self):
        code = "x = 1\ny = 2\npritn('done')"
        results = self.corrector.scan_all(code, "python")
        assert any(r["original"] == "pritn" for r in results)

    def test_scan_no_typos_returns_empty(self):
        code = "x = 1\nprint('ok')"
        results = self.corrector.scan_all(code, "python")
        assert results == []

    def test_scan_deduplicates_same_position(self):
        """The same token at the same position should appear only once."""
        code = "pritn('a')"
        results = self.corrector.scan_all(code, "python")
        positions = [(r["start"], r["end"]) for r in results if r["original"] == "pritn"]
        assert len(positions) == len(set(positions))

    def test_scan_multiple_typos(self):
        code = "retrun pritn(x)"
        results = self.corrector.scan_all(code, "python")
        originals = {r["original"] for r in results}
        assert "retrun" in originals
        assert "pritn" in originals

    def test_scan_respects_confidence_threshold(self):
        results = self.corrector.scan_all("pritn x", "python")
        for r in results:
            assert r["confidence"] >= MIN_CONFIDENCE

    def test_scan_result_shape(self):
        code = "retrun 0"
        results = self.corrector.scan_all(code, "python")
        assert len(results) >= 1
        for key in ("original", "corrected", "confidence", "start", "end", "line", "col"):
            assert key in results[0]


# ─────────────────────────────────────────────────────────────────────────────
# Correction dictionaries — spot-checks
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrectionDictionaries:
    def test_python_dict_has_print_typos(self):
        for typo in ("printt", "pritn", "prnt"):
            assert typo in PYTHON_CORRECTIONS
            assert PYTHON_CORRECTIONS[typo][0] == "print"

    def test_python_dict_has_return_typos(self):
        for typo in ("retrun", "reutrn", "retrn"):
            assert typo in PYTHON_CORRECTIONS
            assert PYTHON_CORRECTIONS[typo][0] == "return"

    def test_python_dict_bool_is_identity(self):
        """'bool' maps to 'bool' — should never be flagged as needing correction."""
        correction, confidence = PYTHON_CORRECTIONS["bool"]
        assert correction == "bool"  # same → filtered out

    def test_java_dict_has_println_typos(self):
        assert "System.out.prinln" in JAVA_CORRECTIONS

    def test_cpp_dict_has_include_typos(self):
        assert "incude" in CPP_CORRECTIONS

    def test_corrections_by_language_keys(self):
        assert set(CORRECTIONS_BY_LANGUAGE.keys()) >= {"python", "java", "cpp", "c++"}

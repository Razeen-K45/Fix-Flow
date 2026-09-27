"""
Real-Time High-Confidence Error Corrector
Detects obvious typos/misspellings while the user types.

Rules:
- Only correct when confidence is HIGH (exact known misspelling → known identifier)
- Never aggressively rewrite user code
- Only correct the word at/near the cursor position
- Works for Python (and is designed to be extended for other languages)
"""

import re
from typing import List

# ── High-confidence correction dictionaries ───────────────────────────────────
# Format: { "misspelling": ("correction", confidence_0_to_1) }

PYTHON_CORRECTIONS = {
    # Built-in functions
    "printt": ("print", 0.99),
    "pritn": ("print", 0.99),
    "prnt": ("print", 0.99),
    "prnit": ("print", 0.99),
    "priint": ("print", 0.99),
    "ptint": ("print", 0.99),
    "pirnt": ("print", 0.99),
    "prit": ("print", 0.95),
    "prrint": ("print", 0.99),
    "inputt": ("input", 0.99),
    "inpuut": ("input", 0.99),
    "imput": ("input", 0.97),
    "lenght": ("len", 0.95),
    "leng": ("len", 0.90),
    "rangee": ("range", 0.99),
    "ragne": ("range", 0.99),
    "rnage": ("range", 0.99),
    "raange": ("range", 0.99),
    "tpye": ("type", 0.99),
    "tyep": ("type", 0.99),
    "typ": ("type", 0.90),
    "retrun": ("return", 0.99),
    "retrn": ("return", 0.99),
    "reutrn": ("return", 0.99),
    "retun": ("return", 0.97),
    "rturn": ("return", 0.95),
    "rerturn": ("return", 0.99),
    "yiled": ("yield", 0.99),
    "yeild": ("yield", 0.99),
    "yileld": ("yield", 0.99),
    "breka": ("break", 0.99),
    "braek": ("break", 0.99),
    "breeak": ("break", 0.99),
    "contiue": ("continue", 0.99),
    "contniue": ("continue", 0.99),
    "continuee": ("continue", 0.99),
    "cotinue": ("continue", 0.97),
    "passs": ("pass", 0.99),
    "passe": ("pass", 0.95),
    "sortt": ("sorted", 0.95),
    "sor": ("sorted", 0.85),
    "appendd": ("append", 0.99),
    "apend": ("append", 0.97),
    "apennd": ("append", 0.99),
    "strr": ("str", 0.99),
    "intt": ("int", 0.99),
    "flot": ("float", 0.97),
    "floatt": ("float", 0.99),
    "bool": ("bool", 1.0),  # Already correct — don't flag
    "lst": ("list", 0.90),
    "listt": ("list", 0.99),
    "dictt": ("dict", 0.99),
    "dct": ("dict", 0.90),
    "sett": ("set", 0.99),
    "tupl": ("tuple", 0.90),
    "tuplr": ("tuple", 0.97),
    "supeer": ("super", 0.99),
    "supr": ("super", 0.90),
    "selff": ("self", 0.99),
    "sself": ("self", 0.99),
    "seslf": ("self", 0.99),
    "slef": ("self", 0.99),
    # Keywords
    "clas": ("class", 0.97),
    "calss": ("class", 0.99),
    "classs": ("class", 0.99),
    "cllass": ("class", 0.99),
    "deef": ("def", 0.99),
    "ded": ("def", 0.90),
    "deff": ("def", 0.99),
    "improt": ("import", 0.99),
    "imoprt": ("import", 0.99),
    "ipmort": ("import", 0.99),
    "importt": ("import", 0.99),
    "impport": ("import", 0.99),
    "fro": ("from", 0.90),
    "fomr": ("from", 0.99),
    "frmo": ("from", 0.99),
    "iff": ("if", 0.99),
    "elf": ("elif", 0.90),
    "ellif": ("elif", 0.99),
    "eliff": ("elif", 0.99),
    "eliif": ("elif", 0.99),
    "eles": ("else", 0.97),
    "esle": ("else", 0.99),
    "elsee": ("else", 0.99),
    "elsse": ("else", 0.99),
    "whlie": ("while", 0.99),
    "whhile": ("while", 0.99),
    "whille": ("while", 0.99),
    "wile": ("while", 0.95),
    "forr": ("for", 0.99),
    "foor": ("for", 0.97),
    "inn": ("in", 0.97),
    "noot": ("not", 0.99),
    "nto": ("not", 0.95),
    "andt": ("and", 0.95),
    "adn": ("and", 0.99),
    "andd": ("and", 0.99),
    "ort": ("or", 0.90),
    "orr": ("or", 0.99),
    "truee": ("True", 0.99),
    "ture": ("True", 0.99),
    "treu": ("True", 0.99),
    "flase": ("False", 0.99),
    "fasle": ("False", 0.99),
    "Flase": ("False", 0.99),
    "falsee": ("False", 0.99),
    "nonee": ("None", 0.99),
    "nnone": ("None", 0.99),
    "Noen": ("None", 0.99),
    "nOne": ("None", 0.95),
    "trye": ("try", 0.97),
    "tryy": ("try", 0.99),
    "excpet": ("except", 0.99),
    "execpt": ("except", 0.99),
    "exept": ("except", 0.99),
    "exceptt": ("except", 0.99),
    "finaly": ("finally", 0.97),
    "fially": ("finally", 0.97),
    "finallyy": ("finally", 0.99),
    "asssert": ("assert", 0.99),
    "assret": ("assert", 0.99),
    "assertt": ("assert", 0.99),
    "withh": ("with", 0.99),
    "wiht": ("with", 0.99),
    "aas": ("as", 0.90),
    "lamda": ("lambda", 0.99),
    "lamba": ("lambda", 0.99),
    "lamdba": ("lambda", 0.99),
    "laambda": ("lambda", 0.99),
    "globall": ("global", 0.99),
    "gloabl": ("global", 0.99),
    "nonlocall": ("nonlocal", 0.99),
    "asnyc": ("async", 0.99),
    "aysnc": ("async", 0.99),
    "asynk": ("async", 0.95),
    "awiait": ("await", 0.99),
    "awaait": ("await", 0.99),
    "awit": ("await", 0.95),
    "delt": ("del", 0.90),
    "raies": ("raise", 0.99),
    "rais": ("raise", 0.90),
    "rasie": ("raise", 0.99),
    "raisee": ("raise", 0.99),
    # Common type names
    "Strng": ("str", 0.97),
    "Stirng": ("str", 0.99),
    "Srtring": ("str", 0.99),
    "Intger": ("int", 0.97),
    "Integerr": ("int", 0.97),
    "Flot": ("float", 0.97),
    "Blooean": ("bool", 0.97),
    "Booelean": ("bool", 0.97),
    # Common exceptions
    "ValuError": ("ValueError", 0.99),
    "ValueErorr": ("ValueError", 0.99),
    "TypeErorr": ("TypeError", 0.99),
    "TypError": ("TypeError", 0.99),
    "IndexErorr": ("IndexError", 0.99),
    "KeyErorr": ("KeyError", 0.99),
    "AttributErorr": ("AttributeError", 0.99),
    "NameErorr": ("NameError", 0.99),
    "IOErorr": ("IOError", 0.99),
    "RuntimeErorr": ("RuntimeError", 0.99),
    "FileNotFoundErorr": ("FileNotFoundError", 0.99),
    "StopIteraton": ("StopIteration", 0.99),
    "RecursionErorr": ("RecursionError", 0.99),
    "ZeroDivisionErorr": ("ZeroDivisionError", 0.99),
    "OverflowErorr": ("OverflowError", 0.99),
    "MemoryErorr": ("MemoryError", 0.99),
    # Common stdlib modules
    "imathport": ("import math", 0.85),
    "maths": ("math", 0.95),
    "os.paht": ("os.path", 0.99),
    "os.phat": ("os.path", 0.99),
    "syss": ("sys", 0.99),
    "jsoon": ("json", 0.99),
    "jsn": ("json", 0.90),
    "re": ("re", 1.0),  # Correct — do not flag
}

# Java corrections (for future language support)
JAVA_CORRECTIONS = {
    "System.out.prinln": ("System.out.println", 0.99),
    "System.out.Println": ("System.out.println", 0.99),
    "System.out.printLn": ("System.out.println", 0.99),
    "system.out.println": ("System.out.println", 0.95),
    "Sysytem.out.println": ("System.out.println", 0.99),
    "Sytem.out.println": ("System.out.println", 0.99),
    "pubilc": ("public", 0.99),
    "publci": ("public", 0.99),
    "privte": ("private", 0.99),
    "prviate": ("private", 0.99),
    "statci": ("static", 0.99),
    "sttaic": ("static", 0.99),
    "vooid": ("void", 0.99),
    "viod": ("void", 0.99),
    "clsas": ("class", 0.99),
    "calss": ("class", 0.99),
    "interafce": ("interface", 0.99),
    "interfce": ("interface", 0.99),
    "stirng": ("String", 0.99),
    "Strng": ("String", 0.99),
    "Stirng": ("String", 0.99),
    "booolean": ("boolean", 0.99),
    "boolen": ("boolean", 0.99),
    "intger": ("int", 0.97),
    "retrun": ("return", 0.99),
    "reutrn": ("return", 0.99),
    "extneds": ("extends", 0.99),
    "implments": ("implements", 0.99),
    "implemtns": ("implements", 0.99),
}

# C++ corrections
CPP_CORRECTIONS = {
    "incude": ("include", 0.99),
    "incldue": ("include", 0.99),
    "incluude": ("include", 0.99),
    "namepsace": ("namespace", 0.99),
    "namesapce": ("namespace", 0.99),
    "namespacee": ("namespace", 0.99),
    "cotu": ("cout", 0.99),
    "coiut": ("cout", 0.99),
    "cinn": ("cin", 0.99),
    "retrun": ("return", 0.99),
    "reutrn": ("return", 0.99),
    "vooid": ("void", 0.99),
    "viod": ("void", 0.99),
    "clas": ("class", 0.97),
    "clsas": ("class", 0.99),
    "strng": ("string", 0.97),
    "stirng": ("string", 0.99),
    "ture": ("true", 0.99),
    "treu": ("true", 0.99),
    "flase": ("false", 0.99),
    "fasle": ("false", 0.99),
    "nuull": ("null", 0.99),
    "nulptr": ("nullptr", 0.97),
    "nulptr": ("nullptr", 0.99),
}

CORRECTIONS_BY_LANGUAGE = {
    "python": PYTHON_CORRECTIONS,
    "java": JAVA_CORRECTIONS,
    "cpp": CPP_CORRECTIONS,
    "c++": CPP_CORRECTIONS,
}

# Minimum confidence threshold to report a correction
MIN_CONFIDENCE = 0.95


class RealTimeCorrector:
    """
    Checks the word at/near the cursor position for high-confidence corrections.
    Only returns corrections when confident enough to avoid false positives.
    """

    def check(self, code: str, cursor_position: int, language: str = "python") -> List[dict]:
        """
        Returns a list of correction suggestions near the cursor.

        Each suggestion:
          {
            "original": str,
            "corrected": str,
            "confidence": float,
            "start": int,   # character offset in code
            "end": int,     # character offset in code
            "line": int,    # 1-based
            "col": int,     # 0-based
          }
        """
        corrections_dict = CORRECTIONS_BY_LANGUAGE.get(language, PYTHON_CORRECTIONS)
        suggestions = []

        # Extract the word currently being typed (at cursor or just before it)
        # Look in a small window around the cursor
        window_start = max(0, cursor_position - 50)
        window_end = min(len(code), cursor_position + 50)

        # Find all word tokens in the window and check each
        # Pattern: word characters (no spaces, punctuation — but allow dots for method calls)
        token_pattern = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*')

        for match in token_pattern.finditer(code, window_start, window_end):
            token = match.group()
            start = match.start()
            end = match.end()

            # Build list of candidates: full token, then each dot-prefix (longest first)
            parts = token.split(".")
            candidates = []
            for length in range(len(parts), 0, -1):
                candidates.append((".".join(parts[:length]), start))

            for candidate_token, candidate_start in candidates:
                if candidate_token in corrections_dict:
                    correction, confidence = corrections_dict[candidate_token]
                    if confidence >= MIN_CONFIDENCE and correction != candidate_token:
                        candidate_end = candidate_start + len(candidate_token)
                        line_num, col = _offset_to_line_col(code, candidate_start)
                        suggestions.append({
                            "original": candidate_token,
                            "corrected": correction,
                            "confidence": confidence,
                            "start": candidate_start,
                            "end": candidate_end,
                            "line": line_num,
                            "col": col,
                        })
                    break  # Stop at first (longest) matching prefix

        return suggestions

    def scan_all(self, code: str, language: str = "python") -> List[dict]:
        """
        Scan the entire code for high-confidence corrections.
        Used when the user requests a full document scan.
        """
        corrections_dict = CORRECTIONS_BY_LANGUAGE.get(language, PYTHON_CORRECTIONS)
        suggestions = []
        seen = set()  # Avoid duplicate suggestions for same position

        token_pattern = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*')

        for match in token_pattern.finditer(code):
            token = match.group()
            start = match.start()

            # Build list of candidates: full token, then each dot-prefix (longest first)
            parts = token.split(".")
            for length in range(len(parts), 0, -1):
                candidate_token = ".".join(parts[:length])
                candidate_start = start
                candidate_end = candidate_start + len(candidate_token)
                key = (candidate_start, candidate_end)

                if key in seen:
                    break  # Already reported this span

                if candidate_token in corrections_dict:
                    correction, confidence = corrections_dict[candidate_token]
                    if confidence >= MIN_CONFIDENCE and correction != candidate_token:
                        seen.add(key)
                        line_num, col = _offset_to_line_col(code, candidate_start)
                        suggestions.append({
                            "original": candidate_token,
                            "corrected": correction,
                            "confidence": confidence,
                            "start": candidate_start,
                            "end": candidate_end,
                            "line": line_num,
                            "col": col,
                        })
                    break  # Stop at first (longest) matching prefix

        return suggestions


def _offset_to_line_col(code: str, offset: int) -> tuple:
    """Convert a character offset to (line_number, column) (1-based line)."""
    before = code[:offset]
    line = before.count("\n") + 1
    col = offset - before.rfind("\n") - 1
    return line, col

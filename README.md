# Fix_Flow

**AI-Powered Code Debugging Environment — IBM Bob 2.0 Hackathon**

Fix_Flow is an IDE-style web application that helps developers identify, correct, debug, test, and verify code using IBM Bob Shell as its AI engine.

---

## Features

| Feature | Description |
|---|---|
| **Real-Time Correction** | High-confidence typo detection while typing (`pritn` → `print`, `clas` → `class`, etc.) |
| **Static Analysis** | Python `ast` + `compile` based syntax, indentation, and style checks |
| **Code Execution** | Runs Python code in a sandboxed subprocess with a 10-second timeout |
| **Bob AI Workflow** | Full 11-step debugging pipeline powered by IBM Bob Shell |
| **Test Generation** | Bob generates test cases targeting discovered bugs |
| **Fix Generation** | Bob generates a targeted fix preserving the original code structure |
| **Independent Verification** | Bob independently verifies whether the fix works (PASS/FAIL) |
| **Proposed Fix Diff** | Shows a line-by-line diff before applying any changes |

---

## AI Debugging Workflow (powered by IBM Bob Shell)

```
Editor Code
    ↓
1. Static Code Analysis  (ast/compile)
    ↓
2. Run Original Code     (subprocess, 10s timeout)
    ↓
3. Bob: Code Analysis    (bob -p "Analyze this code...")
    ↓
4. Bob: Logic Analysis   (bob -p "Analyze logic issues...")
    ↓
5. Bob: Test Generation  (bob -p "Generate test cases...")
    ↓
6. Run Generated Tests   (against original code)
    ↓
7. Bob: Root Cause Analysis
    ↓
8. Bob: Fix Generation   (minimal targeted fix)
    ↓
9. Run Fixed Code        (subprocess, 10s timeout)
    ↓
10. Regression Testing   (run tests against fixed code)
    ↓
11. Bob: Independent Verification  →  PASS / FAIL
```

---

## Quick Start

### Prerequisites

- Python 3.10 or higher
- pip

### 1. Install dependencies

```bash
cd fix-flow-2.0
pip install -r requirements.txt
```

### 2. Run the server

```bash
cd backend
python main.py
```

The server starts on http://localhost:8000

### 3. Open the IDE

Navigate to http://localhost:8000 in your browser.

---

## IBM Bob Shell Setup (for AI features)

Bob Shell is the AI engine. Without it, Fix_Flow falls back to static analysis only.

### Install Bob Shell

```powershell
# Windows
powershell -ep Bypass 'irm -Uri "https://bob.ibm.com/download/bobshell.ps1" | iex'
```

```bash
# macOS / Linux
curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash
```

### Authenticate

For non-interactive use, create an API key at https://bob.ibm.com and set:

```powershell
# Windows (current session)
$env:BOBSHELL_API_KEY = "your-api-key-here"

# Windows (permanent)
[System.Environment]::SetEnvironmentVariable('BOBSHELL_API_KEY', 'your-api-key', 'User')
```

```bash
# macOS / Linux
export BOBSHELL_API_KEY="your-api-key-here"
```

Then restart the Fix_Flow server. The Bob status badge in the top-right corner will turn green when Bob Shell is detected.

---

## Project Structure

```
fix-flow-2.0/
├── backend/
│   ├── main.py                    ← FastAPI application + API routes
│   ├── analyzers/
│   │   └── python_analyzer.py     ← Static analysis (ast, compile, subprocess)
│   └── services/
│       ├── bob_service.py         ← IBM Bob Shell invocation + prompt builders
│       ├── workflow.py            ← Debugging workflow orchestrator (SSE stream)
│       ├── test_runner.py         ← Test case executor
│       └── corrector.py           ← Real-time typo correction
├── frontend/
│   ├── index.html                 ← IDE layout
│   ├── css/
│   │   └── style.css              ← Dark theme IDE styling
│   └── js/
│       ├── api.js                 ← Backend API client
│       ├── editor.js              ← CodeMirror editor + correction UI
│       └── workflow.js            ← Workflow panel + result rendering
├── requirements.txt
└── README.md
```

---

## Supported Languages

| Language | Static Analysis | Code Execution | Bob AI Workflow |
|---|---|---|---|
| **Python** | ✅ Full | ✅ Yes | ✅ Yes |
| Java | 🔜 Planned | 🔜 Planned | 🔜 Planned |
| C++ | 🔜 Planned | 🔜 Planned | 🔜 Planned |

The architecture is modular — new language analyzers can be added in `backend/analyzers/`.

---

## Security Notes

- User code runs in a separate subprocess (not in the server process).
- A 10-second execution timeout prevents infinite loops from hanging the server.
- Code is written to a temporary file and deleted after execution.
- No user code is persisted to disk.

---

## Development

```bash
# Run with auto-reload during development
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## IBM Bob Integration Details

Bob Shell is invoked via `bob --hide-intermediary-output -p "<prompt>"` for each workflow step.  
Each prompt requests a structured JSON response that Fix_Flow parses and displays.  
If Bob Shell is not available, Fix_Flow uses static analysis results and actual execution output  
to produce honest (non-fabricated) fallback analysis.

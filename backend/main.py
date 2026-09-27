"""
Fix_Flow Backend - Main FastAPI Application
IBM Bob 2.0 Hackathon
"""

import os
import json
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from services.workflow import DebuggingWorkflow
from services.corrector import RealTimeCorrector
from analyzers.python_analyzer import PythonAnalyzer

app = FastAPI(title="Fix_Flow", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount frontend
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")

# ── Request/Response models ──────────────────────────────────────────────────

class CodeRequest(BaseModel):
    code: str
    language: str = "python"
    filename: Optional[str] = "untitled.py"

class CorrectionRequest(BaseModel):
    code: str
    cursor_position: int
    language: str = "python"

class WorkflowRequest(BaseModel):
    code: str
    language: str = "python"
    filename: Optional[str] = "untitled.py"

class RunRequest(BaseModel):
    code: str
    language: str = "python"
    stdin: Optional[str] = ""

# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    """Serve the main IDE page."""
    index_path = os.path.join(frontend_path, "index.html")
    return FileResponse(index_path)


@app.post("/api/analyze")
async def analyze_code(req: CodeRequest):
    """
    Static analysis of code using Python's ast/compile.
    Returns syntax errors, indentation errors, and basic issues.
    """
    if not req.code.strip():
        return {"status": "ok", "errors": [], "warnings": [], "info": "No code to analyze."}

    analyzer = PythonAnalyzer()
    result = analyzer.analyze(req.code, req.filename or "untitled.py")
    return result


@app.post("/api/run")
async def run_code(req: RunRequest):
    """
    Execute user code in a sandboxed subprocess with a strict timeout.
    """
    if not req.code.strip():
        return {"stdout": "", "stderr": "No code to run.", "exit_code": 1, "timed_out": False}

    analyzer = PythonAnalyzer()
    result = await analyzer.run_code(req.code, stdin=req.stdin or "")
    return result


@app.post("/api/correct")
async def real_time_correct(req: CorrectionRequest):
    """
    High-confidence real-time error correction (typos/misspellings only).
    Returns a list of corrections with confidence scores.
    Only returns suggestions when confidence is high.
    """
    if not req.code.strip():
        return {"corrections": []}

    corrector = RealTimeCorrector()
    corrections = corrector.check(req.code, req.cursor_position, req.language)
    return {"corrections": corrections}


@app.post("/api/workflow/stream")
async def run_workflow_stream(req: WorkflowRequest):
    """
    Run the full AI debugging workflow using IBM Bob Shell as the AI engine.
    Streams Server-Sent Events (SSE) for each workflow step.
    """
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="No code provided.")

    workflow = DebuggingWorkflow(req.code, req.language, req.filename or "untitled.py")

    async def event_generator():
        async for event in workflow.run():
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/health")
async def health():
    """Health check — also reports Bob Shell availability."""
    import shutil
    bob_available = shutil.which("bob") is not None
    return {
        "status": "ok",
        "bob_shell_available": bob_available,
        "bob_shell_hint": "Install Bob Shell and set BOBSHELL_API_KEY to enable AI workflow." if not bob_available else "Bob Shell is ready."
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

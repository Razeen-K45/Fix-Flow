/**
 * Fix_Flow — API Client Module
 * All backend communication goes through this module.
 * Functions are exposed on the global FixFlowAPI object.
 */

'use strict';

const FixFlowAPI = (() => {
  const BASE = '';  // Same origin

  /**
   * POST /api/analyze
   * Run static code analysis.
   */
  async function analyzeCode(code, language = 'python', filename = 'untitled.py') {
    const res = await fetch(`${BASE}/api/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, language, filename }),
    });
    if (!res.ok) throw new Error(`Analysis failed: ${res.statusText}`);
    return res.json();
  }

  /**
   * POST /api/run
   * Execute user code in a sandboxed subprocess.
   */
  async function runCode(code, language = 'python', stdin = '') {
    const res = await fetch(`${BASE}/api/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, language, stdin }),
    });
    if (!res.ok) throw new Error(`Run failed: ${res.statusText}`);
    return res.json();
  }

  /**
   * POST /api/correct
   * Get real-time high-confidence correction suggestions.
   */
  async function getRealTimeCorrections(code, cursorPosition, language = 'python') {
    const res = await fetch(`${BASE}/api/correct`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, cursor_position: cursorPosition, language }),
    });
    if (!res.ok) throw new Error(`Correction check failed: ${res.statusText}`);
    return res.json();
  }

  /**
   * GET /api/health
   * Health check — also reports Bob Shell availability.
   */
  async function health() {
    const res = await fetch(`${BASE}/api/health`);
    if (!res.ok) throw new Error(`Health check failed: ${res.statusText}`);
    return res.json();
  }

  return {
    analyzeCode,
    runCode,
    getRealTimeCorrections,
    health,
  };
})();

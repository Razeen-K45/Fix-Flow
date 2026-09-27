/**
 * Fix_Flow — Workflow UI Module
 * Drives the debugging workflow panel:
 *   - Connects to the /api/workflow/stream SSE endpoint
 *   - Updates step indicators in real time
 *   - Renders Bob AI results (code analysis, logic analysis, tests, fix, verification)
 *   - Handles Apply Fix / Keep Original actions
 */

'use strict';

const WorkflowUI = (() => {
  // ── Internal state ─────────────────────────────────────────────────────────

  let _activeEventSource = null;
  let _originalCode = '';
  let _fixedCode = '';
  let _workflowRunning = false;

  // Step definitions in order (maps step name → display label)
  const STEP_DEFS = [
    'code_analysis',
    'run_original',
    'bob_code_analysis',
    'logic_analysis',
    'test_generation',
    'regression_testing_original',
    'root_cause_analysis',
    'fix_generation',
    'run_fixed',
    'regression_testing',
    'verification',
  ];

  // ── Public: Start workflow ──────────────────────────────────────────────────

  function startWorkflow(code, language, filename) {
    if (_workflowRunning) {
      stopWorkflow();
    }

    if (!code || !code.trim()) {
      setStatusMessage('No code to debug.');
      return;
    }

    _originalCode = code;
    _fixedCode = '';

    // Reset the UI
    resetWorkflowUI();
    showWorkflowSteps();
    setStatusMessage('Starting Bob AI workflow…');

    // Disable editor buttons while running
    setWorkflowRunning(true);

    // POST to trigger SSE stream
    // We use EventSource which requires GET, so we use fetch + ReadableStream instead
    streamWorkflow(code, language, filename);
  }

  // ── SSE Streaming ──────────────────────────────────────────────────────────

  async function streamWorkflow(code, language, filename) {
    try {
      const response = await fetch('/api/workflow/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, language, filename }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        setStatusMessage(`Workflow error: ${err.detail || response.statusText}`);
        setWorkflowRunning(false);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process all complete SSE messages in buffer
        const lines = buffer.split('\n');
        buffer = lines.pop(); // Keep incomplete line

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const jsonStr = line.slice(6).trim();
            if (!jsonStr) continue;
            try {
              const event = JSON.parse(jsonStr);
              handleWorkflowEvent(event);
            } catch (e) {
              console.warn('Failed to parse SSE event:', jsonStr, e);
            }
          }
        }
      }

      // Process any remaining buffer
      if (buffer.startsWith('data: ')) {
        const jsonStr = buffer.slice(6).trim();
        if (jsonStr) {
          try {
            const event = JSON.parse(jsonStr);
            handleWorkflowEvent(event);
          } catch (_) {}
        }
      }

    } catch (err) {
      setStatusMessage(`Network error: ${err.message}`);
    } finally {
      setWorkflowRunning(false);
      _workflowRunning = false;
    }
  }

  // ── Event Handler ──────────────────────────────────────────────────────────

  function handleWorkflowEvent(event) {
    const { type, step, data } = event;

    switch (type) {
      case 'step_start':
        markStepRunning(step, data.message);
        setStatusMessage(data.message || step);
        break;

      case 'step_done':
        markStepDone(step);
        handleStepResult(step, data);
        break;

      case 'step_error':
        markStepError(step, data.error);
        setStatusMessage(`Error in ${step}: ${data.error}`);
        break;

      case 'workflow_complete':
        handleWorkflowComplete(data);
        break;

      case 'done':
        setWorkflowRunning(false);
        break;

      default:
        break;
    }
  }

  // ── Handle individual step results ─────────────────────────────────────────

  function handleStepResult(step, data) {
    switch (step) {
      case 'code_analysis': {
        // Static analysis results
        const staticResult = data.static || {};
        renderAnalysisResults(staticResult);
        break;
      }

      case 'run_original': {
        // Show runtime output of original code in a step annotation
        const sd = stepEl(step);
        if (sd) {
          let note = '';
          if (data.timed_out) note = '⏱ Timed out';
          else if (data.exit_code !== 0) note = `⚠ Exit ${data.exit_code}`;
          else note = '✓ OK';
          sd.querySelector('.step-status').textContent = note;
        }
        break;
      }

      case 'bob_code_analysis': {
        const result = data.result || {};
        renderBobCodeAnalysis(result, data.bob_available, data.note);
        break;
      }

      case 'logic_analysis': {
        const result = data.result || {};
        appendBobLogicAnalysis(result, data.bob_available);
        break;
      }

      case 'test_generation': {
        const result = data.result || {};
        const sd = stepEl(step);
        if (sd) {
          const count = (result.test_cases || []).length;
          sd.querySelector('.step-status').textContent = `${count} test(s)`;
        }
        break;
      }

      case 'regression_testing_original': {
        const sd = stepEl(step);
        if (sd) {
          sd.querySelector('.step-status').textContent =
            `${data.passed || 0}/${data.total || 0} passed`;
        }
        break;
      }

      case 'root_cause_analysis': {
        const result = data.result || {};
        appendRootCauseAnalysis(result, data.bob_available);
        break;
      }

      case 'fix_generation': {
        const result = data.result || {};
        renderFixProposal(result, data.skipped);
        break;
      }

      case 'run_fixed': {
        const sd = stepEl(step);
        if (sd) {
          let note = '';
          if (data.timed_out) note = '⏱ Timed out';
          else if (data.exit_code !== 0) note = `⚠ Exit ${data.exit_code}`;
          else note = '✓ OK';
          sd.querySelector('.step-status').textContent = note;
        }
        break;
      }

      case 'regression_testing': {
        renderTestResults(data.results || [], data.passed, data.total);
        break;
      }

      case 'verification': {
        renderVerification(data.result || {}, data.bob_available);
        break;
      }

      default:
        break;
    }
  }

  // ── Workflow Complete ───────────────────────────────────────────────────────

  function handleWorkflowComplete(data) {
    const verdict = data.verdict || 'FAIL';
    _fixedCode = data.fixed_code || '';

    setStatusMessage(
      verdict === 'PASS'
        ? `✅ Workflow complete — PASS (${data.tests_passed}/${data.tests_total} tests passed)`
        : `❌ Workflow complete — FAIL`
    );

    setWorkflowRunning(false);
    _workflowRunning = false;
  }

  function stopWorkflow() {
    setWorkflowRunning(false);
    _workflowRunning = false;
  }

  // ── Step indicator helpers ──────────────────────────────────────────────────

  function stepEl(step) {
    return document.getElementById(`wf-${step}`);
  }

  function markStepRunning(step, message) {
    const el = stepEl(step);
    if (!el) return;
    el.className = 'workflow-step running';
    el.querySelector('.step-icon').textContent = '⏳';
    if (message) el.querySelector('.step-status').textContent = '';
  }

  function markStepDone(step) {
    const el = stepEl(step);
    if (!el) return;
    el.classList.remove('running', 'error', 'pending');
    el.classList.add('done');
    el.querySelector('.step-icon').textContent = '✅';
  }

  function markStepError(step, error) {
    const el = stepEl(step);
    if (!el) return;
    el.classList.remove('running', 'done', 'pending');
    el.classList.add('error');
    el.querySelector('.step-icon').textContent = '❌';
    el.querySelector('.step-status').textContent = 'Error';
  }

  function markStepSkipped(step) {
    const el = stepEl(step);
    if (!el) return;
    el.classList.add('skipped');
    el.querySelector('.step-icon').textContent = '➖';
  }

  // ── Render: Static Analysis ─────────────────────────────────────────────────

  function renderAnalysisResults(result) {
    const placeholder = document.getElementById('analysis-placeholder');
    const container   = document.getElementById('analysis-results');

    const errors   = result.errors   || [];
    const warnings = result.warnings || [];
    const info     = result.info     || [];

    placeholder.classList.add('hidden');
    container.classList.remove('hidden');

    let html = `<div class="analysis-summary">`;
    if (errors.length === 0 && warnings.length === 0) {
      html += `<span class="count-badge count-ok">✓ No Issues</span>`;
    } else {
      if (errors.length)   html += `<span class="count-badge count-error">${errors.length} Error${errors.length > 1 ? 's' : ''}</span>`;
      if (warnings.length) html += `<span class="count-badge count-warning">${warnings.length} Warning${warnings.length > 1 ? 's' : ''}</span>`;
    }
    html += `</div>`;

    for (const e of errors) {
      html += issueHtml('error', e.type, e.line, e.message);
    }
    for (const w of warnings) {
      html += issueHtml('warning', w.type, w.line, w.message);
    }
    // Show tree summary
    const tree = result.tree_summary;
    if (tree) {
      const fns = (tree.functions || []).length;
      const cls = (tree.classes  || []).length;
      if (fns > 0 || cls > 0) {
        html += `<div style="margin-top:6px;font-size:10px;color:var(--text-muted)">`;
        if (fns) html += `${fns} function${fns > 1 ? 's' : ''}  `;
        if (cls) html += `${cls} class${cls > 1 ? 'es' : ''}  `;
        html += `</div>`;
      }
    }

    container.innerHTML = html;
  }

  function issueHtml(severity, type, line, message) {
    const lineBadge = line ? `<span class="line-badge">L${line}</span>` : '';
    return `
      <div class="issue-item ${severity}">
        <div class="issue-title">
          ${escapeHtml(type || severity)} ${lineBadge}
        </div>
        <div class="issue-msg">${escapeHtml(message || '')}</div>
      </div>`;
  }

  // ── Render: Bob Code Analysis ───────────────────────────────────────────────

  function renderBobCodeAnalysis(result, bobAvailable, note) {
    const container = document.getElementById('analysis-results');
    if (!container) return;

    let html = '';

    if (!bobAvailable || note) {
      html += `<div class="bob-unavailable-note">${escapeHtml(note || 'Bob Shell not available.')}</div>`;
    }

    if (result.summary && !result.raw) {
      html += `<div class="bob-result-section">
        <div class="bob-result-label">Bob's Summary</div>
        <div class="bob-result-text">${escapeHtml(result.summary)}</div>
      </div>`;
    }

    if (result.issues && result.issues.length > 0) {
      html += `<div class="bob-result-label" style="margin-top:6px">Bob's Issues</div>`;
      for (const issue of result.issues) {
        html += issueHtml(issue.severity || 'info', issue.type || 'Issue', issue.line, issue.description);
      }
    }

    if (result.notes && !result.raw) {
      html += `<div class="bob-result-section">
        <div class="bob-result-label">Notes</div>
        <div class="bob-result-text">${escapeHtml(result.notes)}</div>
      </div>`;
    }

    // Append to existing analysis results
    container.innerHTML += html;
  }

  // ── Render: Logic Analysis ──────────────────────────────────────────────────

  function appendBobLogicAnalysis(result, bobAvailable) {
    const container = document.getElementById('analysis-results');
    if (!container) return;

    const issues = result.logic_issues || [];
    if (issues.length === 0 && !result.summary) return;

    let html = `<div class="bob-result-section" style="margin-top:6px">
      <div class="bob-result-label">Bob's Logic Analysis</div>`;

    if (result.summary) {
      html += `<div class="bob-result-text" style="margin-bottom:4px">${escapeHtml(result.summary)}</div>`;
    }

    for (const issue of issues) {
      html += issueHtml('warning', issue.type || 'LogicIssue', issue.line, issue.description);
    }

    if (result.root_causes && result.root_causes.length > 0) {
      html += `<div class="bob-result-label" style="margin-top:5px">Root Causes</div>`;
      for (const rc of result.root_causes) {
        html += `<div class="issue-item info"><div class="issue-msg">${escapeHtml(rc)}</div></div>`;
      }
    }

    html += `</div>`;
    container.innerHTML += html;
  }

  // ── Render: Root Cause Analysis ─────────────────────────────────────────────

  function appendRootCauseAnalysis(result, bobAvailable) {
    const container = document.getElementById('analysis-results');
    if (!container) return;

    if (!result.primary_root_cause) return;

    let html = `<div class="bob-result-section" style="margin-top:6px">
      <div class="bob-result-label">Root Cause</div>
      <div class="issue-item error">
        <div class="issue-msg">${escapeHtml(result.primary_root_cause)}</div>
      </div>`;

    if (result.fix_strategy) {
      html += `<div class="bob-result-label" style="margin-top:5px">Fix Strategy</div>
        <div class="issue-item info">
          <div class="issue-msg">${escapeHtml(result.fix_strategy)}</div>
        </div>`;
    }

    html += `</div>`;
    container.innerHTML += html;
  }

  // ── Render: Proposed Fix ────────────────────────────────────────────────────

  function renderFixProposal(result, skipped) {
    const placeholder = document.getElementById('fix-placeholder');
    const fixView     = document.getElementById('fix-view');
    const fixMeta     = document.getElementById('fix-meta');
    const diffContainer = document.getElementById('diff-container');

    if (skipped || !result.fixed_code || result.fixed_code === _originalCode) {
      placeholder.textContent = result.explanation || 'No fix needed — code looks correct.';
      placeholder.classList.remove('hidden');
      fixView.classList.add('hidden');
      markStepSkipped('fix_generation');
      return;
    }

    _fixedCode = result.fixed_code;

    placeholder.classList.add('hidden');
    fixView.classList.remove('hidden');

    // Meta info
    const changes = result.changes_made || [];
    const confidence = result.confidence || 'unknown';
    fixMeta.innerHTML = `
      <strong>Confidence:</strong> ${escapeHtml(confidence)}&nbsp;&nbsp;
      <strong>Changes:</strong> ${changes.length}<br>
      <span style="color:var(--text)">${escapeHtml(result.explanation || '')}</span>
    `;

    // Diff view
    diffContainer.innerHTML = buildDiff(_originalCode, result.fixed_code);

    // Wire buttons
    document.getElementById('btn-apply-fix').onclick = () => {
      applyFixToEditor(result.fixed_code);
    };
    document.getElementById('btn-keep-original').onclick = () => {
      restoreOriginalCode(_originalCode);
      fixView.classList.add('hidden');
      placeholder.textContent = 'Fix discarded — original code kept.';
      placeholder.classList.remove('hidden');
    };
  }

  // ── Render: Test Results ────────────────────────────────────────────────────

  function renderTestResults(results, passed, total) {
    const placeholder = document.getElementById('tests-placeholder');
    const container   = document.getElementById('test-results');

    if (!results || results.length === 0) {
      placeholder.textContent = 'No tests were generated or run.';
      return;
    }

    placeholder.classList.add('hidden');
    container.classList.remove('hidden');

    let html = `<div class="test-summary">
      <span class="count-badge ${passed === total ? 'count-ok' : 'count-error'}">${passed}/${total} passed</span>
    </div>`;

    for (const t of results) {
      const cls    = t.skipped ? 'skip' : t.passed ? 'pass' : 'fail';
      const icon   = t.skipped ? '⏭' : t.passed ? '✅' : '❌';
      const errHtml = (!t.passed && !t.skipped && t.error)
        ? `<div class="test-error">${escapeHtml(t.error)}</div>` : '';
      const skipHtml = t.skipped
        ? `<div class="test-error">${escapeHtml(t.skip_reason || 'Skipped')}</div>` : '';

      html += `
        <div class="test-item ${cls}">
          <span class="test-icon">${icon}</span>
          <div class="test-info">
            <div class="test-name">${escapeHtml(t.name || 'Test')}</div>
            <div class="test-desc">${escapeHtml(t.description || '')}</div>
            ${errHtml}${skipHtml}
          </div>
        </div>`;
    }

    container.innerHTML = html;
  }

  // ── Render: Verification ────────────────────────────────────────────────────

  function renderVerification(result, bobAvailable) {
    const placeholder = document.getElementById('verification-placeholder');
    const container   = document.getElementById('verification-result');

    placeholder.classList.add('hidden');
    container.classList.remove('hidden');

    const verdict = result.verdict || 'FAIL';
    const resolved = result.issues_resolved || [];
    const remaining = result.remaining_issues || [];

    let html = `
      <div class="verdict-banner verdict-${verdict}">
        ${verdict === 'PASS' ? '✅' : '❌'} ${verdict}
      </div>`;

    if (result.reasoning) {
      html += `<div class="verdict-detail">${escapeHtml(result.reasoning)}</div>`;
    }

    if (result.confidence) {
      html += `<div class="verdict-detail"><strong>Confidence:</strong> ${escapeHtml(result.confidence)}</div>`;
    }

    if (resolved.length > 0) {
      html += `<div class="verdict-issues"><div class="bob-result-label">Issues Resolved</div>`;
      for (const r of resolved) {
        html += `<div class="verdict-issue-item verdict-resolved">✓ ${escapeHtml(r)}</div>`;
      }
      html += `</div>`;
    }

    if (remaining.length > 0) {
      html += `<div class="verdict-issues"><div class="bob-result-label">Remaining Issues</div>`;
      for (const r of remaining) {
        html += `<div class="verdict-issue-item verdict-remaining">✗ ${escapeHtml(r)}</div>`;
      }
      html += `</div>`;
    }

    if (result.recommendation && verdict === 'FAIL') {
      html += `<div class="verdict-detail" style="margin-top:6px"><strong>Next Step:</strong> ${escapeHtml(result.recommendation)}</div>`;
    }

    if (!bobAvailable) {
      html += `<div class="bob-unavailable-note">Verification based on execution results (Bob Shell not available).</div>`;
    }

    container.innerHTML = html;
  }

  // ── Simple diff generator ───────────────────────────────────────────────────

  function buildDiff(original, fixed) {
    // Line-by-line diff (simplified — show added/removed lines)
    const origLines  = original.split('\n');
    const fixedLines = fixed.split('\n');

    let html = '';
    const maxLen = Math.max(origLines.length, fixedLines.length);
    let i = 0, j = 0;

    // Use a simple LCS-based approach for small files (< 200 lines)
    if (maxLen <= 200) {
      const diff = computeLineDiff(origLines, fixedLines);
      for (const [kind, line] of diff) {
        if (kind === '=') {
          html += `<span class="diff-context"> ${escapeHtml(line)}</span>`;
        } else if (kind === '+') {
          html += `<span class="diff-added">+ ${escapeHtml(line)}</span>`;
        } else if (kind === '-') {
          html += `<span class="diff-removed">- ${escapeHtml(line)}</span>`;
        }
      }
    } else {
      // For large files, just show the new code
      html += `<span class="diff-header">// Fixed code (diff omitted for large files)</span>`;
      for (const line of fixedLines.slice(0, 50)) {
        html += `<span class="diff-context"> ${escapeHtml(line)}</span>`;
      }
      if (fixedLines.length > 50) {
        html += `<span class="diff-header">... (${fixedLines.length - 50} more lines)</span>`;
      }
    }

    return html || '<span class="diff-context">(no changes)</span>';
  }

  function computeLineDiff(orig, fixed) {
    // Simple patience diff approximation using LCS
    const lcs = computeLCS(orig, fixed);
    const result = [];
    let i = 0, j = 0, k = 0;

    while (i < orig.length || j < fixed.length) {
      if (k < lcs.length && i < orig.length && orig[i] === lcs[k] && j < fixed.length && fixed[j] === lcs[k]) {
        result.push(['=', orig[i]]);
        i++; j++; k++;
      } else if (j < fixed.length && (k >= lcs.length || fixed[j] !== lcs[k])) {
        result.push(['+', fixed[j]]);
        j++;
      } else {
        result.push(['-', orig[i]]);
        i++;
      }
    }
    return result;
  }

  function computeLCS(a, b) {
    const m = a.length, n = b.length;
    if (m === 0 || n === 0) return [];
    // Limit LCS for performance on large files
    if (m > 100 || n > 100) return [];
    const dp = Array.from({length: m + 1}, () => new Array(n + 1).fill(0));
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        dp[i][j] = a[i-1] === b[j-1] ? dp[i-1][j-1] + 1 : Math.max(dp[i-1][j], dp[i][j-1]);
      }
    }
    const lcs = [];
    let i = m, j = n;
    while (i > 0 && j > 0) {
      if (a[i-1] === b[j-1]) { lcs.unshift(a[i-1]); i--; j--; }
      else if (dp[i-1][j] > dp[i][j-1]) i--;
      else j--;
    }
    return lcs;
  }

  // ── UI helpers ──────────────────────────────────────────────────────────────

  function resetWorkflowUI() {
    // Reset all step indicators
    for (const step of STEP_DEFS) {
      const el = stepEl(step);
      if (el) {
        el.className = 'workflow-step pending';
        el.querySelector('.step-icon').textContent = '⬜';
        el.querySelector('.step-status').textContent = '';
      }
    }

    // Clear analysis
    document.getElementById('analysis-placeholder').classList.remove('hidden');
    document.getElementById('analysis-results').classList.add('hidden');
    document.getElementById('analysis-results').innerHTML = '';

    // Clear fix
    document.getElementById('fix-placeholder').textContent = 'No fix generated yet.';
    document.getElementById('fix-placeholder').classList.remove('hidden');
    document.getElementById('fix-view').classList.add('hidden');

    // Clear tests
    document.getElementById('tests-placeholder').textContent = 'No tests run yet.';
    document.getElementById('tests-placeholder').classList.remove('hidden');
    document.getElementById('test-results').classList.add('hidden');
    document.getElementById('test-results').innerHTML = '';

    // Clear verification
    document.getElementById('verification-placeholder').textContent = 'Not verified yet.';
    document.getElementById('verification-placeholder').classList.remove('hidden');
    document.getElementById('verification-result').classList.add('hidden');
    document.getElementById('verification-result').innerHTML = '';
  }

  function showWorkflowSteps() {
    document.getElementById('workflow-placeholder').classList.add('hidden');
    document.getElementById('workflow-steps').classList.remove('hidden');
  }

  function setWorkflowRunning(flag) {
    _workflowRunning = flag;
    const btnDebug   = document.getElementById('btn-debug');
    const btnRun     = document.getElementById('btn-run');
    const btnAnalyze = document.getElementById('btn-analyze');
    if (btnDebug)   btnDebug.disabled   = flag;
    if (btnRun)     btnRun.disabled     = flag;
    if (btnAnalyze) btnAnalyze.disabled = flag;
  }

  function setStatusMessage(msg) {
    const el = document.getElementById('statusbar-message');
    if (el) el.textContent = msg;
  }

  function applyFixToEditor(code) {
    if (typeof window.applyFixToEditor === 'function') {
      window.applyFixToEditor(code);
    } else {
      // Fallback if editor.js exposed it differently
      console.warn('applyFixToEditor not available');
    }
  }

  function restoreOriginalCode(code) {
    if (typeof window.restoreOriginalCode === 'function') {
      window.restoreOriginalCode(code);
    }
  }

  // ── Utility ─────────────────────────────────────────────────────────────────

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  return {
    startWorkflow,
    renderAnalysisResults,
  };

})();

/**
 * Fix_Flow — Editor Module
 * Manages the CodeMirror editor, language switching,
 * real-time correction detection, and keyboard shortcuts.
 */

'use strict';

// ── Editor State ─────────────────────────────────────────────────────────────

const EditorState = {
  language: 'python',
  filename: 'untitled.py',
  pendingCorrection: null,    // { original, corrected, start, end, line, col }
  correctionDebounce: null,
  lastCode: '',               // Snapshot for stale-state prevention
};

// ── Default starter code ──────────────────────────────────────────────────────

const STARTER_CODE = {
  python: `# Fix_Flow — Python Editor
# Write your code here and click "Debug with Bob" to analyze it.

def calculate_average(numbers):
    """Calculate the average of a list of numbers."""
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)

data = [10, 20, 30, 40, 50]
result = calculate_average(data)
pritn(f"Average: {result}")
`,
};

// ── CodeMirror Setup ──────────────────────────────────────────────────────────

let cm;  // CodeMirror instance (global within this module)

function initEditor() {
  const mountEl = document.getElementById('editor-mount');

  cm = CodeMirror(mountEl, {
    value: STARTER_CODE['python'],
    mode: 'python',
    theme: 'dracula',
    lineNumbers: true,
    autoCloseBrackets: true,
    matchBrackets: true,
    styleActiveLine: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    lineWrapping: false,
    extraKeys: {
      'Tab': (editor) => {
        if (editor.somethingSelected()) {
          editor.indentSelection('add');
        } else {
          editor.replaceSelection('    ', 'end');
        }
      },
      'Ctrl-Enter': () => runCode(),
      'Ctrl-Shift-D': () => debugWithBob(),
      'Ctrl-Shift-A': () => analyzeCode(),
    },
  });

  // On every change: update cursor position, run real-time correction check
  cm.on('change', (editor, change) => {
    EditorState.lastCode = editor.getValue();
    updateCursorStatus(editor);
    scheduleRealTimeCheck(editor);
  });

  cm.on('cursorActivity', (editor) => {
    updateCursorStatus(editor);
  });

  // Wire UI elements
  document.getElementById('btn-run').addEventListener('click', runCode);
  document.getElementById('btn-analyze').addEventListener('click', analyzeCode);
  document.getElementById('btn-debug').addEventListener('click', debugWithBob);
  document.getElementById('btn-clear-output').addEventListener('click', clearOutput);
  document.getElementById('btn-new-file').addEventListener('click', newFile);
  document.getElementById('btn-correction-apply').addEventListener('click', applyCorrection);
  document.getElementById('btn-correction-dismiss').addEventListener('click', dismissCorrection);
  document.getElementById('lang-select').addEventListener('change', handleLanguageChange);
  document.getElementById('filename-input').addEventListener('input', (e) => {
    EditorState.filename = e.target.value;
    updateFileList();
  });

  // Collapsible panels
  document.querySelectorAll('.panel-header.collapsible').forEach(header => {
    header.addEventListener('click', togglePanel);
  });

  // Kick off health check
  checkBobStatus();
}

// ── Cursor Status ─────────────────────────────────────────────────────────────

function updateCursorStatus(editor) {
  const cursor = editor.getCursor();
  document.getElementById('statusbar-cursor').textContent =
    `Ln ${cursor.line + 1}, Col ${cursor.ch + 1}`;
}

// ── Language Switching ────────────────────────────────────────────────────────

function handleLanguageChange(e) {
  const lang = e.target.value;
  EditorState.language = lang;

  const modeMap = {
    python: 'python',
    java:   'text/x-java',
    cpp:    'text/x-c++src',
  };
  cm.setOption('mode', modeMap[lang] || 'python');

  // Update filename extension
  const extMap = { python: '.py', java: '.java', cpp: '.cpp' };
  const base = EditorState.filename.replace(/\.[^.]+$/, '');
  const newFilename = base + (extMap[lang] || '.py');
  EditorState.filename = newFilename;
  document.getElementById('filename-input').value = newFilename;
  updateFileList();
  document.getElementById('statusbar-lang').textContent = lang.charAt(0).toUpperCase() + lang.slice(1);
}

// ── File Management ───────────────────────────────────────────────────────────

function newFile() {
  const ext = EditorState.language === 'python' ? '.py'
            : EditorState.language === 'java'   ? '.java'
            : '.cpp';
  const name = `untitled${ext}`;
  EditorState.filename = name;
  document.getElementById('filename-input').value = name;
  cm.setValue('');
  updateFileList();
}

function updateFileList() {
  const list = document.getElementById('file-list');
  const existing = list.querySelector('.file-item');
  if (existing) {
    const icon = EditorState.language === 'python' ? '🐍'
               : EditorState.language === 'java'   ? '☕'
               : '⚙';
    existing.innerHTML = `<span class="file-icon">${icon}</span> ${escapeHtml(EditorState.filename)}`;
    existing.dataset.file = EditorState.filename;
  }
}

// ── Run Code ──────────────────────────────────────────────────────────────────

async function runCode() {
  const code = cm.getValue();
  if (!code.trim()) {
    setOutputContent('No code to run.', 'neutral');
    return;
  }

  setRunning(true);
  setStatusMessage('Running…');
  setRunStatusBadge('running', 'Running…');
  setOutputContent('Running…', 'neutral');

  try {
    const result = await FixFlowAPI.runCode(code, EditorState.language);

    let output = '';
    if (result.stdout) output += result.stdout;
    if (result.stderr) output += (output ? '\n' : '') + result.stderr;
    if (!output) output = '(no output)';

    if (result.timed_out) {
      setOutputContent(
        `⚠ Execution timed out!\n\n${result.stderr || 'Possible infinite loop.'}`,
        'error'
      );
      setRunStatusBadge('timeout', 'Timed Out');
      setStatusMessage('Execution timed out.');
    } else if (result.exit_code !== 0) {
      setOutputContent(output, 'error');
      setRunStatusBadge('error', `Exit ${result.exit_code}`);
      setStatusMessage(`Exit code: ${result.exit_code}`);
    } else {
      setOutputContent(output, 'success');
      setRunStatusBadge('success', 'OK');
      setStatusMessage('Run complete.');
    }
  } catch (err) {
    setOutputContent(`Error communicating with server: ${err.message}`, 'error');
    setRunStatusBadge('error', 'Error');
    setStatusMessage('Run failed.');
  } finally {
    setRunning(false);
  }
}

// ── Analyze Code ──────────────────────────────────────────────────────────────

async function analyzeCode() {
  const code = cm.getValue();
  if (!code.trim()) {
    setStatusMessage('No code to analyze.');
    return;
  }

  setStatusMessage('Analyzing…');
  setRunning(true);

  try {
    const result = await FixFlowAPI.analyzeCode(code, EditorState.language, EditorState.filename);
    WorkflowUI.renderAnalysisResults(result);
    const errCount = (result.errors || []).length;
    const warnCount = (result.warnings || []).length;
    setStatusMessage(
      errCount > 0
        ? `${errCount} error(s) found.`
        : warnCount > 0
          ? `${warnCount} warning(s) found.`
          : 'No issues found.'
    );
  } catch (err) {
    setStatusMessage(`Analysis error: ${err.message}`);
  } finally {
    setRunning(false);
  }
}

// ── Debug with Bob ────────────────────────────────────────────────────────────

function debugWithBob() {
  // This is handed off to workflow.js
  if (typeof WorkflowUI !== 'undefined') {
    WorkflowUI.startWorkflow(cm.getValue(), EditorState.language, EditorState.filename);
  }
}

// ── Real-Time Correction ──────────────────────────────────────────────────────

function scheduleRealTimeCheck(editor) {
  clearTimeout(EditorState.correctionDebounce);
  EditorState.correctionDebounce = setTimeout(async () => {
    const code = editor.getValue();
    const cursor = editor.getCursor();
    const cursorOffset = editor.indexFromPos(cursor);

    if (!code.trim()) return;

    try {
      const result = await FixFlowAPI.getRealTimeCorrections(
        code, cursorOffset, EditorState.language
      );
      const corrections = result.corrections || [];
      if (corrections.length > 0) {
        // Show only the most relevant correction (highest confidence, nearest cursor)
        const best = corrections.sort((a, b) => b.confidence - a.confidence)[0];
        showCorrectionBanner(best);
      } else {
        hideCorrectionBanner();
      }
    } catch (_) {
      // Real-time corrections are best-effort; swallow errors silently
    }
  }, 600); // 600ms debounce
}

function showCorrectionBanner(correction) {
  EditorState.pendingCorrection = correction;
  const msg = document.getElementById('correction-message');
  const pct = Math.round(correction.confidence * 100);
  msg.textContent = `Did you mean "${correction.corrected}" instead of "${correction.original}"?  (${pct}% confidence)`;
  document.getElementById('correction-banner').classList.remove('hidden');
}

function hideCorrectionBanner() {
  EditorState.pendingCorrection = null;
  document.getElementById('correction-banner').classList.add('hidden');
}

function applyCorrection() {
  const c = EditorState.pendingCorrection;
  if (!c) return;

  const code = cm.getValue();
  const from = cm.posFromIndex(c.start);
  const to   = cm.posFromIndex(c.end);

  // Verify the token is still the same (guard against stale correction)
  const currentText = cm.getRange(from, to);
  if (currentText !== c.original) {
    hideCorrectionBanner();
    return;
  }

  cm.replaceRange(c.corrected, from, to);
  hideCorrectionBanner();
  setStatusMessage(`Corrected "${c.original}" → "${c.corrected}"`);
}

function dismissCorrection() {
  hideCorrectionBanner();
}

// ── Output helpers ────────────────────────────────────────────────────────────

function setOutputContent(text, kind = 'neutral') {
  const el = document.getElementById('output-content');
  el.textContent = text;
  el.className = 'output-content';
  if (kind === 'error')   el.classList.add('output-error');
  if (kind === 'success') el.classList.add('output-success');
}

function clearOutput() {
  setOutputContent('');
  setRunStatusBadge('', '');
}

function setRunStatusBadge(kind, text) {
  const el = document.getElementById('run-status-badge');
  el.className = 'run-status';
  if (kind) el.classList.add(kind);
  el.textContent = text;
}

function setRunning(flag) {
  document.getElementById('btn-run').disabled = flag;
  document.getElementById('btn-analyze').disabled = flag;
  document.getElementById('btn-debug').disabled = flag;
}

function setStatusMessage(msg) {
  document.getElementById('statusbar-message').textContent = msg;
}

// ── Panel collapse/expand ─────────────────────────────────────────────────────

function togglePanel(e) {
  const header = e.currentTarget;
  const targetId = header.dataset.target;
  const body = document.getElementById(targetId);
  if (!body) return;
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  header.classList.toggle('collapsed', !isHidden);
}

// ── Bob Health Check ──────────────────────────────────────────────────────────

async function checkBobStatus() {
  try {
    const result = await FixFlowAPI.health();
    const badge = document.getElementById('bob-status-badge');
    const text  = document.getElementById('bob-status-text');
    if (result.bob_shell_available) {
      badge.className = 'badge badge-available';
      text.textContent = 'Bob Shell Ready';
    } else {
      badge.className = 'badge badge-unavailable';
      text.textContent = 'Bob Shell Not Found';
      console.warn('Bob Shell not available:', result.bob_shell_hint);
    }
  } catch (_) {
    document.getElementById('bob-status-text').textContent = 'Server Offline';
  }
}

// ── Public API exposed to workflow.js ────────────────────────────────────────

function getEditorCode()     { return cm.getValue(); }
function getEditorLanguage() { return EditorState.language; }
function getEditorFilename() { return EditorState.filename; }
function setEditorCode(code) { cm.setValue(code); }

// Apply proposed fix to editor (called from workflow.js after user approval)
function applyFixToEditor(fixedCode) {
  const original = cm.getValue();
  cm.setValue(fixedCode);
  setStatusMessage('Fix applied to editor.');
}

function restoreOriginalCode(originalCode) {
  cm.setValue(originalCode);
  setStatusMessage('Original code restored.');
}

// ── Utility ──────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', initEditor);

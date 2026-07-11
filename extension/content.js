// ProctorAI — content.js
// Injected into every page the student visits.
// Bridges exam platform ↔ background, enforces restrictions.

(function () {
  'use strict';

  let examActive = false;

  // ── Exam platform bridge (ping from exam.html) ─────────────
  window.addEventListener('message', e => {
    if (!e.data) return;

    if (e.data.type === 'PROCTOR_AI_PING') {
      chrome.runtime.sendMessage({ type:'EXAM_PLATFORM_PING' }, res => {
        if (chrome.runtime.lastError) {
          window.postMessage({ type:'PROCTOR_AI_PONG', ok:false, studentInfo:null }, '*');
          return;
        }
        window.postMessage({
          type:'PROCTOR_AI_PONG',
          ok: !!res?.ok,
          studentInfo: res ? {
            studentId:      res.studentId,
            studentName:    res.studentName,
            examActive:     res.examActive,
            violationCount: res.violationCount,
          } : null,
        }, '*');
      });
    }

    if (e.data.type === 'EXAM_STARTED')   examActive = true;
    if (e.data.type === 'EXAM_ENDED')     examActive = false;
    if (e.data.type === 'EXAM_VIOLATION') {
      chrome.runtime.sendMessage({ type:'CONTENT_HIDDEN' });
    }
  });

  // ── Messages from background ────────────────────────────────
  chrome.runtime.onMessage.addListener(msg => {
    if (msg.type === 'TERMINATE')         showTerminated();
    if (msg.type === 'VIOLATION_FROM_BG') showViolationToast(msg.vtype, msg.violationCount);
  });

  // ── Visibility API ──────────────────────────────────────────
  document.addEventListener('visibilitychange', () => {
    if (!examActive) return;
    if (document.visibilityState === 'hidden') {
      chrome.runtime.sendMessage({ type:'CONTENT_HIDDEN', reason:'visibility' });
    }
  });

  // ── Keyboard restrictions ───────────────────────────────────
  document.addEventListener('keydown', e => {
    if (!examActive) return;
    if (e.key === 'F12') { e.preventDefault(); showToast('DevTools disabled during exam.'); }
    if ((e.ctrlKey||e.metaKey) && e.shiftKey && e.key === 'I') e.preventDefault();
    if ((e.ctrlKey||e.metaKey) && e.key === 'u') e.preventDefault();
  });

  document.addEventListener('contextmenu', e => {
    if (examActive) { e.preventDefault(); showToast('Right-click disabled during exam.'); }
  });
  document.addEventListener('copy', e => { if (examActive) e.preventDefault(); });

  // ── UI helpers ──────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('proctor-styles')) return;
    const s = document.createElement('style');
    s.id = 'proctor-styles';
    s.textContent = `
      #proctor-toast{position:fixed;top:14px;left:50%;transform:translateX(-50%);
        background:#ff4e4e;color:#fff;font-family:system-ui,sans-serif;font-size:13px;
        font-weight:500;padding:9px 18px;border-radius:8px;z-index:2147483646;
        box-shadow:0 4px 20px rgba(255,78,78,.4);animation:pt-in .2s ease;}
      .proctor-viol{position:fixed;top:14px;right:14px;background:#1c2030;
        border:1px solid rgba(255,78,78,.3);color:#e8eaf0;font-family:system-ui,sans-serif;
        font-size:13px;padding:10px 16px;border-radius:10px;z-index:2147483646;
        box-shadow:0 4px 20px rgba(0,0,0,.5);animation:pt-in .2s ease;}
      #proctor-terminated{position:fixed;inset:0;background:rgba(0,0,0,.96);
        z-index:2147483647;display:flex;align-items:center;justify-content:center;
        font-family:system-ui,sans-serif;color:#fff;text-align:center;}
      @keyframes pt-in{from{opacity:0;transform:translateY(-8px) translateX(-50%)}
        to{opacity:1;transform:translateY(0) translateX(-50%)}}
    `;
    document.head.appendChild(s);
  }

  function showToast(msg) {
    injectStyles();
    document.getElementById('proctor-toast')?.remove();
    const el = document.createElement('div');
    el.id = 'proctor-toast';
    el.textContent = `⚠ ProctorAI: ${msg}`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  }

  function showViolationToast(type, count) {
    injectStyles();
    const el = document.createElement('div');
    el.className = 'proctor-viol';
    el.innerHTML = `<strong>Violation ${count}/6:</strong> ${(type||'').replace(/_/g,' ')}`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  }

  function showTerminated() {
    injectStyles();
    if (document.getElementById('proctor-terminated')) return;
    const el = document.createElement('div');
    el.id = 'proctor-terminated';
    el.innerHTML = `
      <div style="max-width:400px;padding:40px;">
        <div style="font-size:48px;margin-bottom:20px;">⛔</div>
        <h1 style="font-size:26px;font-weight:700;color:#ff4e4e;margin-bottom:12px;">
          Exam Terminated</h1>
        <p style="font-size:14px;color:rgba(255,255,255,.5);line-height:1.7;">
          Maximum violations reached. Your session has been reported to your invigilator.</p>
        <div style="margin-top:24px;padding:14px;background:rgba(255,255,255,.04);
          border-radius:10px;font-size:12px;color:rgba(255,255,255,.3);font-family:monospace;">
          All data recorded and saved</div>
      </div>`;
    document.body.appendChild(el);
  }
})();

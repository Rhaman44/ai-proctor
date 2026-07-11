import { initializeApp, getFirestore, doc, getDoc } from "./firebase-bundle.js";
import { FIREBASE_CONFIG } from "./firebase-config.js";

const app = initializeApp(FIREBASE_CONFIG);
const db  = getFirestore(app);

let audioOn = false, audioCtx = null, analyser = null, raf = null;
const THRESH = 0.15;

// ── helpers ───────────────────────────────────────────────────────────────────
function showErr(m) {
  const e = document.getElementById('errBox');
  e.textContent = m;
  e.classList.add('show');
}
function clearErr() {
  document.getElementById('errBox').classList.remove('show');
}
function updateWS(ok) {
  const el = document.getElementById('wsEl');
  el.textContent = ok ? '⬤ Connected to backend' : '⬤ Backend offline — retrying';
  el.className = `ws ${ok ? 'ws-ok' : 'ws-fail'}`;
}
function showActive(id, name, active, vc, ts) {
  document.getElementById('loginView').style.display  = 'none';
  document.getElementById('activeView').style.display = 'block';
  document.getElementById('stuName').textContent = name || id;
  document.getElementById('stuId').textContent   = id;
  document.getElementById('stuAv').textContent   =
    (name || '?').split(' ').map(p => p[0]).join('').toUpperCase().slice(0, 2);
  document.getElementById('vcCount').textContent = vc;
  document.getElementById('tsCount').textContent = ts;
  document.getElementById('hdrStatus').textContent = `Hi, ${(name || '').split(' ')[0]}`;
  if (active) {
    document.getElementById('statusPill').className    = 'pill pill-active';
    document.getElementById('pdot').className          = 'pdot pdot-active';
    document.getElementById('pillTxt').textContent     = 'Monitoring active';
    document.getElementById('btnStart').style.display  = 'none';
  }
}

// ── login ─────────────────────────────────────────────────────────────────────
async function doLogin() {
  const id  = document.getElementById('loginId').value.trim().toUpperCase();
  const pw  = document.getElementById('loginPwd').value;
  const btn = document.getElementById('loginBtn');
  clearErr();

  if (!id || !pw) { showErr('Enter Student ID and password.'); return; }

  btn.disabled  = true;
  btn.innerHTML = '<span class="spin"></span>Verifying...';

  try {
    const snap = await getDoc(doc(db, 'students', id));

    if (!snap.exists()) {
      showErr(`Student ID "${id}" not found. Check with your teacher.`);
      return;
    }

    const data = snap.data();

    if (data.password !== pw) {
      showErr('Incorrect password. Try again.');
      return;
    }
    if (data.terminated) {
      showErr('Your exam has been terminated. Contact your teacher.');
      return;
    }

    await chrome.storage.local.set({
      studentId: id, studentName: data.name,
      examActive: false, violationCount: 0, tabSwitchCount: 0
    });
    showActive(id, data.name, false, 0, 0);

  } catch (e) {
    console.error('[ProctorAI] login error:', e);
    const code = e?.code || '';
    if (code.includes('permission-denied')) {
      showErr('Firestore permission denied — check your security rules.');
    } else if (code.includes('unavailable') || code.includes('network')) {
      showErr('Network error. Check your internet connection.');
    } else {
      showErr(`Error: ${e.message || 'Unknown — open DevTools console.'}`);
    }
  } finally {
    btn.disabled  = false;
    btn.innerHTML = 'Sign In to Proctor';
  }
}

// ── logout ────────────────────────────────────────────────────────────────────
function doLogout() {
  chrome.runtime.sendMessage({ type: 'STOP_EXAM' }, () => {
    chrome.storage.local.clear();
    document.getElementById('loginView').style.display  = 'block';
    document.getElementById('activeView').style.display = 'none';
    document.getElementById('loginId').value  = '';
    document.getElementById('loginPwd').value = '';
    stopAudio();
  });
}

// ── start monitoring ──────────────────────────────────────────────────────────
function startMonitoring() {
  chrome.storage.local.get(['studentId', 'studentName'], d => {
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
      chrome.runtime.sendMessage({
        type:        'START_EXAM',
        studentId:   d.studentId,
        studentName: d.studentName,
        tabId:       tabs[0]?.id
      }, () => {
        document.getElementById('statusPill').className    = 'pill pill-active';
        document.getElementById('pdot').className          = 'pdot pdot-active';
        document.getElementById('pillTxt').textContent     = 'Monitoring active';
        document.getElementById('btnStart').style.display  = 'none';
      });
    });
  });
}

// ── audio ─────────────────────────────────────────────────────────────────────
async function toggleAudio() {
  audioOn = !audioOn;
  document.getElementById('audioTg').className = `tg ${audioOn ? 'on' : 'off'}`;
  if (audioOn) await startAudio(); else stopAudio();
}
async function startAudio() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    audioCtx = new AudioContext();
    const src = audioCtx.createMediaStreamSource(stream);
    analyser  = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    src.connect(analyser);
    monitorAudio();
  } catch (e) {
    audioOn = false;
    document.getElementById('audioTg').className = 'tg off';
    alert('Microphone access denied.');
  }
}
function stopAudio() {
  cancelAnimationFrame(raf);
  if (audioCtx) audioCtx.close();
  audioCtx = null; analyser = null;
  document.getElementById('audioFill').style.width = '0%';
}
let lastAV = 0;
function monitorAudio() {
  if (!analyser) return;
  const d = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(d);
  const avg = d.reduce((s, v) => s + v, 0) / d.length / 255;
  document.getElementById('audioFill').style.width      = `${Math.min(100, avg * 400)}%`;
  document.getElementById('audioFill').style.background = avg > THRESH ? '#ff4e4e' : '#3ecf8e';
  const now = Date.now();
  if (avg > THRESH && now - lastAV > 8000) {
    lastAV = now;
    chrome.runtime.sendMessage({ type: 'AUDIO_VIOLATION', level: avg });
  }
  raf = requestAnimationFrame(monitorAudio);
}

// ── wire up buttons ───────────────────────────────────────────────────────────
document.getElementById('loginBtn').addEventListener('click', doLogin);
document.getElementById('btnLogout').addEventListener('click', doLogout);
document.getElementById('btnStart').addEventListener('click', startMonitoring);
document.getElementById('audioRow').addEventListener('click', toggleAudio);
document.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

// ── restore existing session ──────────────────────────────────────────────────
chrome.storage.local.get(
  ['studentId', 'studentName', 'examActive', 'violationCount', 'tabSwitchCount'],
  d => {
    if (d.studentId) {
      showActive(d.studentId, d.studentName, d.examActive, d.violationCount || 0, d.tabSwitchCount || 0);
    }
  }
);

// ── poll for live updates ─────────────────────────────────────────────────────
setInterval(() => {
  chrome.runtime.sendMessage({ type: 'GET_STATUS' }, res => {
    if (chrome.runtime.lastError || !res || !res.examActive) return;
    document.getElementById('vcCount').textContent = res.violationCount || 0;
    document.getElementById('tsCount').textContent = res.tabSwitchCount || 0;
    updateWS(res.wsReady);
  });
}, 2000);

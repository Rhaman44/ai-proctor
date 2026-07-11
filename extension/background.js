// ProctorAI — background.js
// Handles: WebSocket to server.py, tab switch detection,
//          audio violation relay, exam platform ping/pong

const WS_URL       = "ws://localhost:8765";
const MAX_VIOLATIONS = 6;

let ws             = null;
let wsReady        = false;
let studentId      = null;
let studentName    = null;
let examActive     = false;
let examTabId      = null;
let violationCount = 0;
let tabSwitchCount = 0;
let reconnectTimer = null;
let pendingQueue   = [];

// ── WebSocket ──────────────────────────────────────────
function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      wsReady = true;
      clearTimeout(reconnectTimer);
      console.log("[ProctorAI] WS connected");
      pendingQueue.forEach(m => ws.send(JSON.stringify(m)));
      pendingQueue = [];
      if (studentId) {
        ws.send(JSON.stringify({ type:"EXAM_START", studentId, studentName }));
      }
    };
    ws.onclose = () => {
      wsReady = false;
      reconnectTimer = setTimeout(connectWS, 3000);
    };
    ws.onerror = () => {};
    ws.onmessage = e => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "TERMINATE")      terminateExam("server");
        if (msg.type === "VIOLATION_ACK") {
          violationCount = msg.violationCount;
          updateBadge();
        }
      } catch {}
    };
  } catch {
    reconnectTimer = setTimeout(connectWS, 5000);
  }
}

function sendWS(payload) {
  const msg = { ...payload, studentId, timestamp: new Date().toISOString() };
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  } else {
    pendingQueue.push(msg);
    connectWS();
  }
}

// ── Violation ──────────────────────────────────────────
function reportViolation(vtype, meta = {}) {
  if (!examActive || !studentId) return;
  violationCount++;
  updateBadge();
  sendWS({ type:"VIOLATION", violationType:vtype, violationCount, ...meta });
  chrome.runtime.sendMessage({
    type:"VIOLATION_UPDATE", vtype, violationCount, tabSwitchCount
  }).catch(() => {});
  if (examTabId) {
    chrome.tabs.sendMessage(examTabId, {
      type:"VIOLATION_FROM_BG", vtype, violationCount
    }).catch(() => {});
  }
  if (violationCount >= MAX_VIOLATIONS) terminateExam("max_violations");
}

function terminateExam(reason) {
  examActive = false;
  sendWS({ type:"EXAM_TERMINATED", reason });
  chrome.storage.local.set({ examActive:false, terminated:true });
  chrome.action.setBadgeText({ text:"⛔" });
  chrome.action.setBadgeBackgroundColor({ color:"#ff4e4e" });
  if (examTabId) {
    chrome.tabs.sendMessage(examTabId, { type:"TERMINATE" }).catch(() => {});
  }
}

function updateBadge() {
  const color = violationCount >= 5 ? "#ff4e4e"
    : violationCount >= 3 ? "#f5a623" : "#5b7fff";
  chrome.action.setBadgeText({ text: String(violationCount) });
  chrome.action.setBadgeBackgroundColor({ color });
}

// ── Tab / window detection ─────────────────────────────
chrome.tabs.onActivated.addListener(({ tabId }) => {
  if (!examActive || !examTabId) return;
  if (tabId !== examTabId) {
    tabSwitchCount++;
    reportViolation("TAB_SWITCH", { tabSwitchCount });
  }
});

chrome.windows.onFocusChanged.addListener(windowId => {
  if (!examActive) return;
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    reportViolation("WINDOW_UNFOCUSED");
  }
});

// ── Message handler ────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  if (msg.type === "START_EXAM") {
    studentId      = msg.studentId;
    studentName    = msg.studentName;
    examTabId      = sender.tab ? sender.tab.id : msg.tabId;
    examActive     = true;
    violationCount = 0;
    tabSwitchCount = 0;
    chrome.storage.local.set({
      examActive:true, studentId, studentName, examTabId, terminated:false
    });
    chrome.action.setBadgeText({ text:"0" });
    chrome.action.setBadgeBackgroundColor({ color:"#5b7fff" });
    connectWS();
    sendResponse({ ok:true });
  }

  if (msg.type === "STOP_EXAM") {
    examActive = false;
    examTabId  = null;
    chrome.action.setBadgeText({ text:"" });
    sendWS({ type:"EXAM_END" });
    chrome.storage.local.set({ examActive:false });
    sendResponse({ ok:true });
  }

  if (msg.type === "GET_STATUS") {
    sendResponse({ examActive, studentId, studentName,
      violationCount, tabSwitchCount, wsReady });
  }

  if (msg.type === "CONTENT_HIDDEN") {
    if (examActive) {
      tabSwitchCount++;
      reportViolation("TAB_SWITCH", { source:"visibility_api", tabSwitchCount });
    }
  }

  if (msg.type === "AUDIO_VIOLATION") {
    reportViolation("AUDIO_DETECTED", { level: msg.level });
  }

  // Exam platform ping
  if (msg.type === "EXAM_PLATFORM_PING") {
    sendResponse({ ok:true, studentId, studentName, examActive, violationCount });
  }

  return true;
});

// ── Restore state on SW restart ────────────────────────
chrome.storage.local.get(
  ["examActive","studentId","studentName","examTabId"],
  data => {
    if (data.examActive) {
      examActive  = true;
      studentId   = data.studentId;
      studentName = data.studentName;
      examTabId   = data.examTabId;
      connectWS();
    }
  }
);

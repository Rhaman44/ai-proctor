"""
ProctorAI — proctor_main.py
============================
Upgraded version of your original proctor_stage2.py.
Your best.pt model is used exactly as before.

Edit STUDENT_ID and STUDENT_NAME before each session, then:
  python proctor_main.py

Make sure server.py is already running first.
"""

import cv2
import mediapipe as mp
import numpy as np
from ultralytics import YOLO
import os, time, json, base64, threading, asyncio
from datetime import datetime

# ══════════════════════════════════════════
#  EDIT THESE before every exam session
STUDENT_ID   = "Test001"
STUDENT_NAME = "Rushan"
# ══════════════════════════════════════════

# ── Detection thresholds (same as your original) ──────
AUDIO_ENABLED            = False
AUDIO_THRESHOLD          = 0.07
AUDIO_VIOLATION_COOLDOWN = 8

MAX_VIOLATIONS        = 6
COOLDOWN              = 5
PHONE_LIMIT           = 2      # seconds phone must be visible
FACE_ABSENCE_LIMIT    = 3      # seconds face must be missing
LOOKING_AWAY_LIMIT    = 6      # seconds → first gaze violation
LOOKING_AWAY_MAX      = 60     # seconds → second gaze violation

# ── WebSocket → server.py ────────────────────────────
WS_URL      = "ws://localhost:8765"
_ws         = [None]
_ws_loop    = [None]
_ws_ok      = [False]
_terminated = [False]

def _ws_thread():
    import websockets
    async def run():
        _ws_loop[0] = asyncio.get_event_loop()
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as conn:
                    _ws[0] = conn
                    _ws_ok[0] = True
                    print("[proctor] ✓ Connected to server.py")
                    await conn.send(json.dumps({
                        "type":        "EXAM_START",
                        "studentId":   STUDENT_ID,
                        "studentName": STUDENT_NAME,
                    }))
                    async for raw in conn:
                        data = json.loads(raw)
                        if data.get("type") == "TERMINATE":
                            print("[proctor] SERVER: exam terminated")
                            _terminated[0] = True
            except Exception as e:
                _ws_ok[0] = False
                _ws[0]    = None
                print(f"[proctor] server unreachable ({e}), retry in 3s")
                await asyncio.sleep(3)
    asyncio.run(run())

threading.Thread(target=_ws_thread, daemon=True).start()
time.sleep(1.0)   # give WS time to connect

def _send(payload):
    """Send JSON message to server.py — non-blocking."""
    if _ws[0] is None or _ws_loop[0] is None:
        return
    async def go():
        try:
            await _ws[0].send(json.dumps(payload))
        except Exception:
            pass
    if _ws_loop[0].is_running():
        asyncio.run_coroutine_threadsafe(go(), _ws_loop[0])

# ── Audio monitoring ──────────────────────────────────
_rms      = [0.0]
_audio_ok = [False]

if AUDIO_ENABLED:
    try:
        import sounddevice as sd
        def _audio_cb(indata, frames, t, status):
            _rms[0] = float(np.sqrt(np.mean(indata ** 2)))
        sd.InputStream(
            samplerate=16000, channels=1,
            blocksize=1600, callback=_audio_cb
        ).start()
        _audio_ok[0] = True
        print("[proctor] ✓ Audio monitoring active")
    except Exception as e:
        print(f"[proctor] Audio unavailable: {e}")
        print("[proctor] Mac fix: brew install portaudio && pip install sounddevice")

# ── Load YOLO model (best.pt) ───────────────────
print("[proctor] Loading best.pt ...")
model = YOLO("best.pt")
print("[proctor] Model classes:", list(model.names.values()))

# ── MediaPipe (same as your original) ────────────────
mp_face_det  = mp.solutions.face_detection
mp_face_mesh = mp.solutions.face_mesh
mp_draw      = mp.solutions.drawing_utils

face_detector = mp_face_det.FaceDetection(
    model_selection=0, min_detection_confidence=0.5)

face_mesher = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=2,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)

# ── Camera ────────────────────────────────────────────
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
time.sleep(2)

if not cap.isOpened():
    print("Camera failed")
    exit()



os.makedirs("logs",     exist_ok=True)
os.makedirs("evidence", exist_ok=True)

# ── Violation state ───────────────────────────────────
violation_count     = 0
last_violation_time = 0

face_missing_start  = None
phone_start         = None
looking_start       = None
last_audio_viol     = 0

phone_flag          = False
look_short_flag     = False
look_long_flag      = False

# ── Helpers ───────────────────────────────────────────
def frame_to_b64(frame):
    """Encode frame as base64 JPEG for server upload."""
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return base64.b64encode(buf.tobytes()).decode("utf-8")

def log_violation(vtype, frame):
    """Log locally + send to server.py (which uploads to Cloudinary + Firestore)."""
    global violation_count

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Local text log (same as your original)
    with open("logs/violations.log", "a") as f:
        f.write(f"{ts} - {STUDENT_NAME} ({STUDENT_ID}) - {vtype}\n")

    # Local screenshot backup (same as your original)
    fname = f"evidence/{vtype}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    cv2.imwrite(fname, frame)

    print(f"[proctor] VIOLATION: {vtype} ({violation_count + 1}/{MAX_VIOLATIONS})")

    # Send to server → Cloudinary upload + Firestore write
    _send({
        "type":          "VIOLATION",
        "studentId":     STUDENT_ID,
        "studentName":   STUDENT_NAME,
        "violationType": vtype,
        "timestamp":     ts,
        "screenshot":    frame_to_b64(frame),   # base64 JPEG
    })

def get_head_direction(landmarks, img_w):
    """
    Same geometry as your original proctor_stage2.py
    plus vertical (down) detection added.
    """
    nose      = landmarks.landmark[1]
    left_eye  = landmarks.landmark[33]
    right_eye = landmarks.landmark[263]

    nose_x      = int(nose.x * img_w)
    left_eye_x  = int(left_eye.x * img_w)
    right_eye_x = int(right_eye.x * img_w)
    eye_center  = (left_eye_x + right_eye_x) // 2

    # Vertical: looking down at notes
    if nose.y > left_eye.y + 0.09:
        return "Looking Down"

    if nose_x < eye_center - 20:
        return "Looking Left"
    if nose_x > eye_center + 20:
        return "Looking Right"
    return "Looking Forward"

def hud(frame, text, pos, color=(200, 200, 200), scale=0.55, thick=1):
    cv2.putText(frame, text, pos,
                cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)

# ── Main loop ─────────────────────────────────────────
print(f"\n[proctor] Starting — {STUDENT_NAME} ({STUDENT_ID})")
print("[proctor] Press Q to quit\n")

while True:
    ret, frame = cap.read()
    print("Reading frame...", ret)
    if not ret:
        print("Frame read failed")
        break

    # Server-side termination
    if _terminated[0]:
        hud(frame, "EXAM TERMINATED BY SERVER", (30, 300), (0, 0, 255), 1.0, 3)
        cv2.imshow("ProctorAI", frame)
        cv2.waitKey(3000)
        break

    h, w  = frame.shape[:2]
    rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    now   = time.time()

    # ── Face detection (MediaPipe) ────────────────────
    det_result = face_detector.process(rgb)
    face_count = 0
    if det_result.detections:
        face_count = len(det_result.detections)
        for d in det_result.detections:
            mp_draw.draw_detection(frame, d)

    # ── Gaze (MediaPipe face mesh) ────────────────────
    mesh_result = face_mesher.process(rgb)
    direction   = "Looking Forward"
    if mesh_result.multi_face_landmarks:
        direction = get_head_direction(mesh_result.multi_face_landmarks[0], w)

    # ── Phone detection (your best.pt) ───────────────
    phone_visible = False
    for r in model(frame, verbose=False):
        for box in r.boxes:
            label = model.names[int(box.cls[0])]
            conf  = float(box.conf[0])
            if label == "phone" and conf > 0.55:
                phone_visible = True
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 60, 60), 2)
                hud(frame, f"PHONE {conf:.0%}", (x1, y1 - 8), (255, 60, 60), 0.55, 2)

    rms = _rms[0] if _audio_ok[0] else 0.0

   
    #  VIOLATION CHECKS 
    

    # 1 ── Phone
    if phone_visible:
        if phone_start is None:
            phone_start = now
        dur = now - phone_start
        hud(frame, f"Phone visible: {dur:.0f}s", (30, 165), (255, 80, 80))
        if dur > PHONE_LIMIT and not phone_flag:
            log_violation("PHONE_DETECTED", frame)
            violation_count += 1
            last_violation_time = now
            phone_flag = True
    else:
        phone_start = None
        phone_flag  = False

    # 2 ── Face absence
    if face_count == 0:
        if face_missing_start is None:
            face_missing_start = now
        dur = now - face_missing_start
        hud(frame, f"Face missing: {dur:.0f}s", (30, 115), (80, 80, 255))
        if dur > FACE_ABSENCE_LIMIT and now - last_violation_time > COOLDOWN:
            log_violation("LEFT_SEAT", frame)
            violation_count += 1
            last_violation_time = now
    else:
        face_missing_start = None

    # 3 ── Gaze / looking away
    if direction != "Looking Forward":
        if looking_start is None:
            looking_start = now
        dur = now - looking_start
        hud(frame, f"Looking away: {dur:.0f}s", (30, 140), (80, 80, 255))
        if dur > LOOKING_AWAY_LIMIT and not look_short_flag:
            log_violation("LOOKING_AWAY_SHORT", frame)
            violation_count += 1
            last_violation_time = now
            look_short_flag = True
        if dur > LOOKING_AWAY_MAX and not look_long_flag:
            log_violation("LOOKING_AWAY_LONG", frame)
            violation_count += 1
            last_violation_time = now
            look_long_flag = True
    else:
        looking_start   = None
        look_short_flag = False
        look_long_flag  = False

    # 4 ── Multiple faces
    if face_count > 1 and now - last_violation_time > COOLDOWN:
        log_violation("MULTIPLE_FACES", frame)
        violation_count += 1
        last_violation_time = now

    # 5 ── Audio
    if (_audio_ok[0] and AUDIO_ENABLED
            and rms > AUDIO_THRESHOLD
            and now - last_audio_viol > AUDIO_VIOLATION_COOLDOWN):
        log_violation("AUDIO_DETECTED", frame)
        violation_count += 1
        last_violation_time = now
        last_audio_viol = now

    # ── HUD overlay ───────────────────────────────────
    cv2.rectangle(frame, (0, 0), (w, 46), (18, 20, 28), -1)
    hud(frame, f"{STUDENT_NAME}  |  {STUDENT_ID}", (10, 16), (180, 180, 200), 0.48)
    g_col = (80, 220, 80) if direction == "Looking Forward" else (80, 80, 255)
    hud(frame, f"Gaze: {direction}", (10, 34), g_col, 0.50)
    ws_col = (80, 220, 80) if _ws_ok[0] else (80, 80, 255)
    hud(frame, "WS:OK" if _ws_ok[0] else "WS:OFF", (w - 75, 16), ws_col, 0.44)
    v_col = (80, 80, 255) if violation_count >= 4 else (80, 200, 80)
    hud(frame, f"Violations: {violation_count}/{MAX_VIOLATIONS}",
        (10, h - 46), v_col, 0.72, 2)

    # Audio level bar
    if _audio_ok[0]:
        bw  = int(min(rms / AUDIO_THRESHOLD, 1.5) * 140)
        b_c = (50, 50, 220) if rms > AUDIO_THRESHOLD else (50, 180, 80)
        cv2.rectangle(frame, (10, h - 26), (150, h - 14), (50, 50, 50), -1)
        cv2.rectangle(frame, (10, h - 26), (10 + min(bw, 140), h - 14), b_c, -1)
        hud(frame, "MIC", (154, h - 15), (130, 130, 130), 0.36)

    # ── Termination ───────────────────────────────────
    if violation_count >= MAX_VIOLATIONS:
        cv2.rectangle(frame, (0, 0), (w, h), (0, 0, 120), 8)
        hud(frame, "EXAM TERMINATED", (30, h // 2), (50, 50, 255), 1.3, 3)
        cv2.imshow("ProctorAI", frame)
        cv2.waitKey(3000)
        _send({
            "type":   "EXAM_TERMINATED",
            "reason": "max_violations",
            "studentId": STUDENT_ID,
        })
        break

    cv2.imshow("ProctorAI", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# ── Cleanup ───────────────────────────────────────────
_send({"type": "EXAM_END", "studentId": STUDENT_ID})
time.sleep(0.5)
cap.release()
cv2.destroyAllWindows()
print("[proctor] Session ended.")

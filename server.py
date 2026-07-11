

import asyncio, json, websockets, threading, os, base64
from datetime import datetime, timezone



CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "add-your-cloud-name")
CLOUDINARY_API_KEY    = os.environ.get("CLOUDINARY_API_KEY", "add-your-api-key")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "add-your-api-secret")
# ══════════════════════════════════════════════════════

HOST           = "localhost"
PORT           = 8765
MAX_VIOLATIONS = 6

# ── Cloudinary ────────────────────────────────────────
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name = CLOUDINARY_CLOUD_NAME,
    api_key    = CLOUDINARY_API_KEY,
    api_secret = CLOUDINARY_API_SECRET,
    secure     = True,
)
print("[server] ✓ Cloudinary configured")

def upload_screenshot(img_bytes, student_id, violation_type):
    """Upload screenshot to Cloudinary. Returns secure URL or None."""
    if not img_bytes:
        return None
    try:
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        public_id = f"proctor/{student_id}/{violation_type}_{ts}"
        result    = cloudinary.uploader.upload(
            img_bytes,
            public_id     = public_id,
            resource_type = "image",
            format        = "jpg",
            overwrite     = False,
        )
        url = result.get("secure_url")
        print(f"[cloudinary] uploaded: ...{url[-40:]}")
        return url
    except Exception as e:
        print(f"[cloudinary] upload error: {e}")
        return None

# ── Firebase Firestore ────────────────────────────────
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    HAVE_FB = True
except ImportError:
    HAVE_FB = False
    print("[server] ERROR: pip install firebase-admin")

db = None

def init_firebase():
    global db
    if not HAVE_FB:
        return False
    key = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY", "serviceAccountKey.json")
    if not os.path.exists(key):
        print(f"[server] ERROR: {key} not found. Download your own service account "
              f"key from Firebase Console > Project Settings > Service Accounts, "
              f"save it as serviceAccountKey.json in this folder (it's gitignored), "
              f"or set FIREBASE_SERVICE_ACCOUNT_KEY to its path.")
        return False
    try:
        cred = credentials.Certificate(key)
        firebase_admin.initialize_app(cred)   # no storageBucket — using Cloudinary
        db = firestore.client()
        print("[server] ✓ Firestore connected")
        return True
    except Exception as e:
        print(f"[server] Firebase error: {e}")
        return False

# ── Firestore helpers ─────────────────────────────────
def set_student(sid, data):
    if db is None:
        return
    def go():
        try:
            db.collection("students").document(sid).set(data, merge=True)
        except Exception as e:
            print(f"[firestore] student write error: {e}")
    threading.Thread(target=go, daemon=True).start()

def add_violation(data):
    if db is None:
        print(f"[server] (no db) {data.get('type')} — {data.get('studentId')}")
        return
    def go():
        try:
            db.collection("violations").add(data)
        except Exception as e:
            print(f"[firestore] violation write error: {e}")
    threading.Thread(target=go, daemon=True).start()

# ── Clients ───────────────────────────────────────────
clients = {}

# ── WebSocket handler ─────────────────────────────────
async def handler(websocket):
    info = {"studentId": None, "studentName": "Unknown", "violationCount": 0}
    clients[websocket] = info
    print(f"[server] connected: {websocket.remote_address}")

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            t    = msg.get("type", "")
            sid  = msg.get("studentId")   or info["studentId"]   or "unknown"
            name = msg.get("studentName") or info["studentName"] or "Unknown"
            now  = datetime.now(timezone.utc).isoformat()

            # EXAM START
            if t in ("EXAM_START", "REGISTER"):
                info.update({"studentId": sid, "studentName": name, "violationCount": 0})
                clients[websocket] = info
                print(f"[server] exam start: {name} ({sid})")
                set_student(sid, {
                    "id": sid, "name": name, "online": True,
                    "terminated": False, "violationCount": 0,
                    "examStarted": now, "lastSeen": now,
                })

            # VIOLATION (from proctor_main.py or extension)
            elif t == "VIOLATION":
                vtype = msg.get("violationType", "UNKNOWN")
                vc    = msg.get("violationCount", info["violationCount"] + 1)
                info["violationCount"] = vc
                print(f"[server] VIOLATION [{vtype}] {name} ({vc}/{MAX_VIOLATIONS})")

                # Decode + upload screenshot to Cloudinary
                screenshot_url = None
                b64 = msg.get("screenshot")
                if b64:
                    try:
                        img_bytes      = base64.b64decode(b64)
                        screenshot_url = upload_screenshot(img_bytes, sid, vtype)
                    except Exception as e:
                        print(f"[server] screenshot error: {e}")

                # Write violation to Firestore
                add_violation({
                    "studentId":      sid,
                    "studentName":    name,
                    "type":           vtype,
                    "timestamp":      firestore.SERVER_TIMESTAMP if db else now,
                    "violationCount": vc,
                    "screenshotUrl":  screenshot_url,   # Cloudinary URL
                    "metadata": {
                        k: v for k, v in msg.items()
                        if k not in ["type", "studentId", "studentName",
                                     "timestamp", "violationCount",
                                     "violationType", "screenshot"]
                    },
                })
                set_student(sid, {
                    "violationCount": vc,
                    "lastViolation":  vtype,
                    "lastSeen":       now,
                })

                await websocket.send(json.dumps({
                    "type": "VIOLATION_ACK", "violationCount": vc
                }))

                if vc >= MAX_VIOLATIONS:
                    print(f"[server] TERMINATING: {name}")
                    set_student(sid, {
                        "terminated": True, "online": False, "terminatedAt": now
                    })
                    await websocket.send(json.dumps({"type": "TERMINATE"}))

            # AUDIO VIOLATION (from extension popup mic)
            elif t == "AUDIO_VIOLATION":
                info["violationCount"] += 1
                vc    = info["violationCount"]
                level = msg.get("level", 0)
                print(f"[server] AUDIO_DETECTED {name} ({vc}/{MAX_VIOLATIONS})")
                add_violation({
                    "studentId": sid, "studentName": name,
                    "type": "AUDIO_DETECTED",
                    "timestamp": firestore.SERVER_TIMESTAMP if db else now,
                    "violationCount": vc, "screenshotUrl": None,
                    "metadata": {"audioLevel": round(float(level), 4)},
                })
                set_student(sid, {"violationCount": vc, "lastViolation": "AUDIO_DETECTED"})
                await websocket.send(json.dumps({
                    "type": "VIOLATION_ACK", "violationCount": vc
                }))
                if vc >= MAX_VIOLATIONS:
                    set_student(sid, {"terminated": True, "online": False, "terminatedAt": now})
                    await websocket.send(json.dumps({"type": "TERMINATE"}))

            # EXAM END
            elif t == "EXAM_END":
                print(f"[server] exam ended: {name}")
                set_student(sid, {"online": False, "examEnded": now})

            # EXAM TERMINATED (client-side max violations)
            elif t == "EXAM_TERMINATED":
                print(f"[server] terminated ({msg.get('reason','?')}): {name}")
                set_student(sid, {
                    "terminated": True, "online": False,
                    "terminatedAt": now,
                    "terminationReason": msg.get("reason"),
                })

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        sid = info.get("studentId")
        if sid:
            set_student(sid, {
                "online": False,
                "lastSeen": datetime.now(timezone.utc).isoformat(),
            })
        clients.pop(websocket, None)
        print(f"[server] disconnected: {info.get('studentName','?')}")


async def main():
    ok = init_firebase()
    if not ok:
        print("[server] WARNING: Firestore not connected. Check serviceAccountKey.json")

    print(f"\n[server] ✓ Listening on ws://{HOST}:{PORT}")
    print("[server] Waiting for connections...\n")

    async with websockets.serve(handler, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[server] stopped.")

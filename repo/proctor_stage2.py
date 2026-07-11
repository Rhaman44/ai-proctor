import cv2
import mediapipe as mp
import numpy as np
from ultralytics import YOLO
import os
import time
from datetime import datetime




model = YOLO("best.pt")
print("Model class names:", model.names)




mp_face_detection = mp.solutions.face_detection
mp_face_mesh = mp.solutions.face_mesh
mp_drawing = mp.solutions.drawing_utils

face_detection = mp_face_detection.FaceDetection(
    model_selection=0,
    min_detection_confidence=0.5
)

face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=2,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)




cap = cv2.VideoCapture(0)




os.makedirs("logs", exist_ok=True)
os.makedirs("evidence", exist_ok=True)

LOG_FILE = "logs/violations.log"




violation_count = 0
MAX_VIOLATIONS = 6
last_violation_time = 0
COOLDOWN = 5




face_missing_start = None
FACE_ABSENCE_LIMIT = 3




looking_away_start = None
LOOKING_AWAY_LIMIT = 6
LOOKING_AWAY_MAX = 60

looking_away_violation_triggered = False
long_away_violation_triggered = False




phone_detect_start = None
PHONE_DETECTION_LIMIT = 2

phone_violation_triggered = False




def log_violation(violation_type, frame):

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} - {violation_type}\n")

    filename = f"evidence/{violation_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

    cv2.imwrite(filename, frame)

    print("Violation logged:", violation_type)




def get_head_direction(landmarks, img_w):

    nose = landmarks.landmark[1]
    left_eye = landmarks.landmark[33]
    right_eye = landmarks.landmark[263]

    nose_x = int(nose.x * img_w)
    left_eye_x = int(left_eye.x * img_w)
    right_eye_x = int(right_eye.x * img_w)

    eye_center = (left_eye_x + right_eye_x) // 2

    if nose_x < eye_center - 20:
        return "Looking Left"

    elif nose_x > eye_center + 20:
        return "Looking Right"

    else:
        return "Looking Forward"



while True:

    ret, frame = cap.read()

    if not ret:
        break

    img_h, img_w, _ = frame.shape
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    current_time = time.time()

    

    detection_results = face_detection.process(rgb_frame)

    face_count = 0

    if detection_results.detections:

        face_count = len(detection_results.detections)

        for detection in detection_results.detections:
            mp_drawing.draw_detection(frame, detection)


   

    mesh_results = face_mesh.process(rgb_frame)

    direction_text = "Looking Forward"

    if mesh_results.multi_face_landmarks:

        for face_landmarks in mesh_results.multi_face_landmarks:
            direction_text = get_head_direction(face_landmarks, img_w)


    

    phone_detected = False

    results = model(frame, verbose=False)

    for r in results:

        for box in r.boxes:

            cls_id = int(box.cls[0])
            label = model.names[cls_id]
            conf = float(box.conf[0])

            if label == "phone" and conf > 0.45:

                phone_detected = True

                x1, y1, x2, y2 = map(int, box.xyxy[0])

                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)

                cv2.putText(
                    frame,
                    f"Phone {conf:.2f}",
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (255, 0, 0),
                    2
                )


    
    if phone_detected:

        if phone_detect_start is None:
            phone_detect_start = current_time

        phone_duration = current_time - phone_detect_start

        cv2.putText(
            frame,
            f"Phone Seen: {int(phone_duration)}s",
            (30, 170),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 0, 0),
            2
        )

        if phone_duration > PHONE_DETECTION_LIMIT and not phone_violation_triggered:

            log_violation("PHONE_DETECTED", frame)
            violation_count += 1
            last_violation_time = current_time

            phone_violation_triggered = True

    else:

        phone_detect_start = None
        phone_violation_triggered = False


   

    if face_count == 0:

        if face_missing_start is None:
            face_missing_start = current_time

        absence_duration = current_time - face_missing_start

        cv2.putText(
            frame,
            f"Face Missing: {int(absence_duration)}s",
            (30, 120),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0,0,255),
            2
        )

        if absence_duration > FACE_ABSENCE_LIMIT:

            if current_time - last_violation_time > COOLDOWN:

                log_violation("LEFT_SEAT", frame)
                violation_count += 1
                last_violation_time = current_time

    else:

        face_missing_start = None


  

    if direction_text in ["Looking Left", "Looking Right"]:

        if looking_away_start is None:
            looking_away_start = current_time

        away_duration = current_time - looking_away_start

        cv2.putText(
            frame,
            f"Looking Away: {int(away_duration)}s",
            (30, 140),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0,0,255),
            2
        )

        if away_duration > LOOKING_AWAY_LIMIT and not looking_away_violation_triggered:

            log_violation("LOOKING_AWAY_SHORT", frame)
            violation_count += 1
            last_violation_time = current_time

            looking_away_violation_triggered = True


        if away_duration > LOOKING_AWAY_MAX and not long_away_violation_triggered:

            log_violation("LOOKING_AWAY_LONG", frame)
            violation_count += 1
            last_violation_time = current_time

            long_away_violation_triggered = True

    else:

        looking_away_start = None
        looking_away_violation_triggered = False
        long_away_violation_triggered = False


  

    if face_count > 1:

        if current_time - last_violation_time > COOLDOWN:

            log_violation("MULTIPLE_FACES", frame)
            violation_count += 1
            last_violation_time = current_time


   

    cv2.putText(
        frame,
        direction_text,
        (30, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0,255,0),
        2
    )

    cv2.putText(
        frame,
        f"Violations: {violation_count}",
        (30, 210),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (0,0,255),
        2
    )


    

    if violation_count >= MAX_VIOLATIONS:

        cv2.putText(
            frame,
            "EXAM TERMINATED",
            (30,260),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0,0,255),
            3
        )

        cv2.imshow("AI Proctor - Stage 2", frame)
        cv2.waitKey(3000)
        break


    cv2.imshow("AI Proctor - Stage 2", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break


cap.release()
cv2.destroyAllWindows()
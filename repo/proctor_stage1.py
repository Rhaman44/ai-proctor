import cv2
import mediapipe as mp
import numpy as np

# Initialize MediaPipe modules
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

def get_head_direction(landmarks, img_w, img_h):
    # Nose tip = landmark 1
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

    # Face Detection
    detection_results = face_detection.process(rgb_frame)
    face_count = 0

    if detection_results.detections:
        face_count = len(detection_results.detections)
        for detection in detection_results.detections:
            mp_drawing.draw_detection(frame, detection)

    # Face Mesh (for head direction)
    mesh_results = face_mesh.process(rgb_frame)

    direction_text = ""

    if mesh_results.multi_face_landmarks:
        for face_landmarks in mesh_results.multi_face_landmarks:
            direction_text = get_head_direction(face_landmarks, img_w, img_h)

    # Logic
    if face_count == 0:
        status = "NO FACE DETECTED"
        color = (0, 0, 255)

    elif face_count > 1:
        status = "MULTIPLE FACES DETECTED"
        color = (0, 0, 255)

    else:
        status = direction_text
        color = (0, 255, 0)

    cv2.putText(
        frame,
        status,
        (30, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        color,
        2
    )

    cv2.imshow("AI Proctor - Stage 1", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
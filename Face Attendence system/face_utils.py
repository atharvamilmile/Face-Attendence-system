"""
face_utils.py - Face detection, encoding, and recognition logic.

Anti-spoofing strategy:
  - During ENROLLMENT: user must blink at least once to prove liveness.
    Uses facial landmarks (eye aspect ratio) from face_recognition library.
    Works on any camera in any lighting — no texture/LBP analysis.
  - During LIVE RECOGNITION: spoof check is DISABLED by default because
    blink detection requires sustained observation (multiple frames).
    The enrollment blink gate is the primary liveness barrier.
    Set ENABLE_LIVE_SPOOF = True only if you add a per-student blink tracker.

Other features:
  - Confidence threshold: minimum 60% match to mark attendance
  - Multi-angle enrollment: 5 pose prompts + blink gate
"""

import face_recognition
import cv2
import numpy as np
import logging
from scipy.spatial import distance as dist

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

TOLERANCE           = 0.45   # face distance threshold (lower = stricter)
MIN_CONFIDENCE      = 60.0   # minimum % confidence to accept recognition
FRAME_SCALE         = 0.25   # downscale factor for recognition speed
NUM_ENROLLMENT_SAMPLES = 5

# Eye Aspect Ratio thresholds for blink detection
EAR_BLINK_THRESHOLD = 0.22   # EAR below this = eye is closed
EAR_CONSEC_FRAMES   = 2      # must be closed for this many frames to count as blink

# Set False to skip per-frame spoof check during live attendance
# (blink gate during enrollment is always active)
ENABLE_LIVE_SPOOF   = False

ENROLLMENT_PROMPTS = [
    "Look STRAIGHT at camera",
    "Turn slightly LEFT",
    "Turn slightly RIGHT",
    "Tilt slightly UP",
    "Tilt slightly DOWN",
]

# Landmark indices for left and right eyes (face_recognition 68-point model)
LEFT_EYE_IDX  = list(range(36, 42))
RIGHT_EYE_IDX = list(range(42, 48))


# ── Eye Aspect Ratio ───────────────────────────────────────────────────────────

def _eye_aspect_ratio(eye_points: np.ndarray) -> float:
    """
    Compute Eye Aspect Ratio (EAR).
    EAR ≈ 0.25–0.35 when eye is open; drops below EAR_BLINK_THRESHOLD when closed.

    Formula: EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
    where p1..p6 are the 6 eye landmark points.
    """
    A = dist.euclidean(eye_points[1], eye_points[5])
    B = dist.euclidean(eye_points[2], eye_points[4])
    C = dist.euclidean(eye_points[0], eye_points[3])
    return (A + B) / (2.0 * C) if C > 0 else 0.0


def _get_ear_from_landmarks(landmarks: dict) -> float:
    """Extract average EAR from a face_recognition landmark dict."""
    try:
        left_eye  = np.array(landmarks["left_eye"])
        right_eye = np.array(landmarks["right_eye"])
        left_ear  = _eye_aspect_ratio(left_eye)
        right_ear = _eye_aspect_ratio(right_eye)
        return (left_ear + right_ear) / 2.0
    except (KeyError, ValueError):
        return 0.3   # neutral open-eye fallback


# ── Blink-gated enrollment ─────────────────────────────────────────────────────

def _wait_for_blink(video: cv2.VideoCapture, timeout_frames: int = 300) -> bool:
    """
    Block until the user blinks (or timeout).
    Shows a live preview with EAR meter and instructions.

    Args:
        video:          Already-opened VideoCapture
        timeout_frames: Give up after this many frames (~10 s at 30 fps)

    Returns:
        True if a blink was detected, False on timeout or Q-press.
    """
    blink_detected   = False
    consec_closed    = 0
    frames_checked   = 0

    while not blink_detected and frames_checked < timeout_frames:
        ret, frame = video.read()
        if not ret:
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locs = face_recognition.face_locations(rgb, model="hog")

        # Dark overlay header
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], 90), (10, 18, 38), -1)
        cv2.addWeighted(overlay, 0.80, frame, 0.20, 0, frame)

        cv2.putText(frame, "LIVENESS CHECK — Please BLINK to continue",
                    (12, 32), cv2.FONT_HERSHEY_DUPLEX, 0.62, (0, 212, 255), 1)
        cv2.putText(frame, "Press Q to cancel",
                    (12, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (120, 150, 190), 1)

        if locs:
            landmarks_list = face_recognition.face_landmarks(rgb, locs)
            if landmarks_list:
                ear = _get_ear_from_landmarks(landmarks_list[0])

                # EAR progress bar
                bar_w = int(np.clip(ear / 0.40, 0, 1) * 200)
                bar_color = (0, 255, 100) if ear >= EAR_BLINK_THRESHOLD else (0, 100, 255)
                cv2.rectangle(frame, (12, 72), (212, 82), (30, 40, 60), -1)
                cv2.rectangle(frame, (12, 72), (12 + bar_w, 82), bar_color, -1)
                cv2.putText(frame, f"EAR {ear:.2f}", (218, 82),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, bar_color, 1)

                top, right, bottom, left = locs[0]
                cv2.rectangle(frame, (left, top), (right, bottom), (0, 180, 255), 2)

                if ear < EAR_BLINK_THRESHOLD:
                    consec_closed += 1
                else:
                    if consec_closed >= EAR_CONSEC_FRAMES:
                        blink_detected = True
                        logger.info("Blink detected — liveness confirmed.")
                    consec_closed = 0

        cv2.imshow("Face Enrollment - FaceTrack", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            return False
        frames_checked += 1

    return blink_detected


# ── Enrollment ─────────────────────────────────────────────────────────────────

def capture_face_encoding(num_samples: int = NUM_ENROLLMENT_SAMPLES) -> list | None:
    """
    Multi-angle enrollment with blink-based liveness gate.

    Steps:
      1. Ask user to blink (proves it's a real person, not a photo).
      2. Walk through NUM_ENROLLMENT_SAMPLES head-pose prompts.
      3. Capture one encoding per pose.

    Returns:
        List of numpy arrays, or None if cancelled/failed.
    """
    video = cv2.VideoCapture(0)
    if not video.isOpened():
        logger.error("Could not open webcam.")
        return None

    logger.info("Enrollment started — waiting for blink liveness check.")

    # ── Step 1: Blink gate ──
    blinked = _wait_for_blink(video, timeout_frames=400)
    if not blinked:
        video.release()
        cv2.destroyAllWindows()
        logger.warning("Liveness check failed or cancelled.")
        return None

    # Brief confirmation
    ret, confirm_frame = video.read()
    if ret:
        cv2.putText(confirm_frame, "✓ Liveness confirmed! Starting capture...",
                    (12, 40), cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 255, 100), 2)
        cv2.imshow("Face Enrollment - FaceTrack", confirm_frame)
        cv2.waitKey(800)

    # ── Step 2: Multi-angle capture ──
    encodings_collected = []
    prompts = ENROLLMENT_PROMPTS[:num_samples]
    logger.info(f"Collecting {num_samples} pose encodings.")

    while len(encodings_collected) < num_samples:
        ret, frame = video.read()
        if not ret:
            continue

        current_prompt = prompts[len(encodings_collected)]
        rgb_frame      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame, model="hog")

        # Header overlay
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], 80), (15, 25, 50), -1)
        cv2.addWeighted(overlay, 0.78, frame, 0.22, 0, frame)

        cv2.putText(frame,
                    f"Pose {len(encodings_collected)+1}/{num_samples}: {current_prompt}",
                    (12, 30), cv2.FONT_HERSHEY_DUPLEX, 0.65, (0, 212, 255), 1)
        cv2.putText(frame, "Press Q to cancel",
                    (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (130, 160, 200), 1)

        if len(face_locations) == 1:
            top, right, bottom, left = face_locations[0]
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 200, 80), 2)

            encs = face_recognition.face_encodings(rgb_frame, face_locations)
            if encs:
                encodings_collected.append(encs[0])
                logger.info(f"Pose {len(encodings_collected)}/{num_samples} captured: "
                            f"{current_prompt}")
                confirm = frame.copy()
                cv2.rectangle(confirm, (left, top), (right, bottom), (0, 255, 100), 4)
                cv2.putText(confirm, "Captured!", (left, top - 12),
                            cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 255, 100), 2)
                cv2.imshow("Face Enrollment - FaceTrack", confirm)
                cv2.waitKey(500)
                continue

        elif len(face_locations) > 1:
            cv2.putText(frame, "Multiple faces — please be alone.",
                        (12, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 60, 220), 2)
        else:
            cv2.putText(frame, "No face detected — look at camera.",
                        (12, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 140, 255), 2)

        cv2.imshow("Face Enrollment - FaceTrack", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            logger.info("Enrollment cancelled.")
            video.release()
            cv2.destroyAllWindows()
            return None

    video.release()
    cv2.destroyAllWindows()
    logger.info(f"Enrollment complete: {len(encodings_collected)} encodings.")
    return encodings_collected


# ── Recognition ────────────────────────────────────────────────────────────────

def recognize_faces_in_frame(frame: np.ndarray, known_students: list) -> list:
    """
    Detect and identify all faces in a single video frame.

    Args:
        frame:          BGR image from OpenCV
        known_students: List of dicts with student_id, name, encoding

    Returns:
        List of result dicts:
            name        – recognised name or "Unknown"
            student_id  – ID string or ""
            location    – (top, right, bottom, left)
            confidence  – 0–100 float
            spoof       – always True (live spoof disabled; handled at enrollment)
    """
    small  = cv2.resize(frame, (0, 0), fx=FRAME_SCALE, fy=FRAME_SCALE)
    rgb_sm = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    face_locations = face_recognition.face_locations(rgb_sm, model="hog")
    face_encodings = face_recognition.face_encodings(rgb_sm, face_locations)

    # Flatten known encodings
    known_encs, known_names, known_ids = [], [], []
    for s in known_students:
        encs = s["encoding"] if isinstance(s["encoding"], list) else [s["encoding"]]
        known_encs.extend(encs)
        known_names.extend([s["name"]]       * len(encs))
        known_ids.extend([s["student_id"]]   * len(encs))

    scale   = int(1 / FRAME_SCALE)
    results = []

    for enc, loc in zip(face_encodings, face_locations):
        name       = "Unknown"
        student_id = ""
        confidence = 0.0

        if known_encs:
            distances      = face_recognition.face_distance(known_encs, enc)
            best_idx       = int(np.argmin(distances))
            best_dist      = distances[best_idx]
            raw_conf       = round((1 - best_dist) * 100, 1)

            if best_dist <= TOLERANCE and raw_conf >= MIN_CONFIDENCE:
                name       = known_names[best_idx]
                student_id = known_ids[best_idx]
                confidence = raw_conf

        top, right, bottom, left = [c * scale for c in loc]

        results.append({
            "name":       name,
            "student_id": student_id,
            "location":   (top, right, bottom, left),
            "confidence": confidence,
            "spoof":      True,   # liveness is enforced at enrollment
        })

    return results


def draw_face_annotations(frame: np.ndarray, face_results: list) -> np.ndarray:
    """
    Draw bounding boxes and name/confidence labels.

    Cyan  = recognised
    Red   = unknown
    """
    for r in face_results:
        top, right, bottom, left = r["location"]
        name       = r["name"]
        confidence = r["confidence"]

        color = (0, 212, 255) if name != "Unknown" else (0, 0, 200)

        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.rectangle(frame, (left, bottom - 36), (right, bottom), color, cv2.FILLED)

        label = f"{name}  {confidence:.0f}%" if name != "Unknown" and confidence > 0 else name
        cv2.putText(frame, label, (left + 6, bottom - 8),
                    cv2.FONT_HERSHEY_DUPLEX, 0.55, (255, 255, 255), 1)

    return frame
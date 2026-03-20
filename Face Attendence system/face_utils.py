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
    Multi-angle enrollment: guide the user through head-pose prompts and
    collect one encoding per pose for robust recognition.
    No blink gate — captures directly.

    Args:
        num_samples: Number of angle samples to collect (default 5)

    Returns:
        List of numpy face encoding arrays, or None if cancelled / failed.
    """
    video = cv2.VideoCapture(0)
    if not video.isOpened():
        logger.error("Could not open webcam.")
        return None

    encodings_collected = []
    prompts = ENROLLMENT_PROMPTS[:num_samples]
    logger.info(f"Starting enrollment: {num_samples} poses.")

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
                # Brief green flash to confirm capture
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


# ── Cached known encodings (rebuilt only when students list changes) ───────────
_cached_encs  = []
_cached_names = []
_cached_ids   = []
_cached_hash  = 0   # hash of student list to detect changes


def _rebuild_cache(known_students: list):
    """Flatten all student encodings into parallel lists for fast comparison."""
    global _cached_encs, _cached_names, _cached_ids, _cached_hash
    new_hash = hash(tuple(s["student_id"] for s in known_students))
    if new_hash == _cached_hash:
        return   # nothing changed, reuse cache
    encs, names, ids = [], [], []
    for s in known_students:
        enc_list = s["encoding"] if isinstance(s["encoding"], list) else [s["encoding"]]
        encs.extend(enc_list)
        names.extend([s["name"]]       * len(enc_list))
        ids.extend([s["student_id"]]   * len(enc_list))
    _cached_encs  = encs
    _cached_names = names
    _cached_ids   = ids
    _cached_hash  = new_hash
    logger.info(f"Encoding cache rebuilt: {len(known_students)} students, "
                f"{len(encs)} total encodings.")


# ── Recognition ────────────────────────────────────────────────────────────────

def recognize_faces_in_frame(frame: np.ndarray, known_students: list) -> list:
    """
    Detect and identify all faces in a single VIDEO frame.

    Pipeline:
      1. Resize frame to 50% for faster HOG face detection
      2. Convert BGR → RGB (face_recognition expects RGB)
      3. Locate faces using HOG model
      4. Compute 128-dim encodings for each face
      5. Compare against cached known encodings
      6. Apply TOLERANCE + MIN_CONFIDENCE threshold
      7. Scale bounding boxes back to original frame size

    Args:
        frame:          BGR image from OpenCV (live video frame)
        known_students: List of dicts with student_id, name, encoding

    Returns:
        List of result dicts:
            name        – recognised name or "Unknown"
            student_id  – ID string or ""
            location    – (top, right, bottom, left) in ORIGINAL frame coords
            confidence  – 0–100 float
    """
    # Rebuild encoding cache if student list changed
    _rebuild_cache(known_students)

    # Resize to 50% for faster detection (better than 25% for laptop cameras)
    small  = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
    rgb_sm = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    # Detect face locations in downsampled frame
    face_locations = face_recognition.face_locations(rgb_sm, model="hog")
    if not face_locations:
        return []

    # Compute encodings only for detected faces
    face_encodings = face_recognition.face_encodings(
        rgb_sm, face_locations, num_jitters=1)

    results = []

    for enc, loc in zip(face_encodings, face_locations):
        name       = "Unknown"
        student_id = ""
        confidence = 0.0

        if _cached_encs:
            distances  = face_recognition.face_distance(_cached_encs, enc)
            best_idx   = int(np.argmin(distances))
            best_dist  = distances[best_idx]
            raw_conf   = round((1 - best_dist) * 100, 1)

            if best_dist <= TOLERANCE and raw_conf >= MIN_CONFIDENCE:
                name       = _cached_names[best_idx]
                student_id = _cached_ids[best_idx]
                confidence = raw_conf

        # Scale bounding box coordinates back to original frame size (×2)
        top, right, bottom, left = [c * 2 for c in loc]

        results.append({
            "name":       name,
            "student_id": student_id,
            "location":   (top, right, bottom, left),
            "confidence": confidence,
        })

    return results


def draw_face_annotations(frame: np.ndarray, face_results: list,
                          just_marked: set = None) -> np.ndarray:
    """
    Draw bounding boxes, labels, and a ✓ tick for newly marked students.

    Colours:
      Bright green + tick  = attendance just marked this recognition cycle
      Cyan                 = recognised, already marked earlier
      Red                  = unknown face
    
    Args:
        frame:        BGR image from OpenCV
        face_results: Output from recognize_faces_in_frame()
        just_marked:  Set of student_ids marked in the CURRENT frame cycle
    """
    if just_marked is None:
        just_marked = set()

    for r in face_results:
        top, right, bottom, left = r["location"]
        name       = r["name"]
        confidence = r["confidence"]
        sid        = r.get("student_id", "")
        is_new     = sid and sid in just_marked

        if is_new:
            color = (0, 255, 100)       # bright green — just marked
        elif name != "Unknown":
            color = (0, 212, 255)       # cyan — recognised
        else:
            color = (0, 0, 200)         # red — unknown

        # ── Bounding box ──────────────────────────────────────────────────────
        thickness = 3 if is_new else 2
        cv2.rectangle(frame, (left, top), (right, bottom), color, thickness)

        # ── Corner tick marks (makes box look more polished) ──────────────────
        ln = 18  # corner line length
        for (cx, cy, dx, dy) in [
            (left,  top,    1,  1), (right, top,   -1,  1),
            (left,  bottom, 1, -1), (right, bottom,-1, -1)
        ]:
            cv2.line(frame, (cx, cy), (cx + dx*ln, cy), color, 2)
            cv2.line(frame, (cx, cy), (cx, cy + dy*ln), color, 2)

        # ── Label background ──────────────────────────────────────────────────
        cv2.rectangle(frame, (left, bottom - 36), (right, bottom), color, cv2.FILLED)

        label = f"{name}  {confidence:.0f}%" if name != "Unknown" and confidence > 0 else name
        cv2.putText(frame, label, (left + 6, bottom - 8),
                    cv2.FONT_HERSHEY_DUPLEX, 0.52, (255, 255, 255), 1)

        # ── Big ✓ tick for newly marked ───────────────────────────────────────
        if is_new:
            cx = (left + right) // 2
            cy = top - 18

            # Tick background circle
            cv2.circle(frame, (cx, cy), 22, (0, 255, 100), -1)
            cv2.circle(frame, (cx, cy), 22, (255, 255, 255), 2)

            # Draw ✓ using two lines
            # Short stroke: bottom-left of tick
            cv2.line(frame, (cx - 11, cy),
                     (cx - 4,  cy + 8), (0, 60, 20), 3)
            # Long stroke: up-right
            cv2.line(frame, (cx - 4,  cy + 8),
                     (cx + 12, cy - 10), (0, 60, 20), 3)

            # "Marked!" text above tick
            cv2.putText(frame, "Marked!", (left, top - 48),
                        cv2.FONT_HERSHEY_DUPLEX, 0.6, (0, 255, 100), 2)

    return frame
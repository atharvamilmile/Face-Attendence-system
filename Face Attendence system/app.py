"""
app.py - FaceTrack Complete Web Application
Replaces main.py entirely. Runs everything in the browser.

Features:
  - Single login → Admin / Teacher / Student dashboards
  - Live camera feed via MJPEG stream (/video_feed)
  - Face recognition + attendance marking in background thread
  - Face enrollment (webcam capture) triggered from browser
  - Admin: full control, teachers, settings, attendance
  - Teacher: live camera, register student, manage students, attendance, manual mark
  - Student: own attendance view

Run: python app.py → http://localhost:5000
"""

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, Response, send_file, stream_with_context)
from datetime import date
import database, attendance as att, face_utils
import cv2, threading, time, os, logging
import numpy as np

app = Flask(__name__)
app.secret_key = "facetrack_2024_secret"
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # 24 hours

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("data/app.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

os.makedirs("data", exist_ok=True)
os.makedirs("attendance_records", exist_ok=True)

# ── Camera state (shared across threads) ──────────────────────────────────────
camera_lock      = threading.Lock()
camera_running   = False
camera_thread    = None
latest_frame     = None          # JPEG bytes of latest annotated frame
frame_lock       = threading.Lock()
known_students   = []
attendance_stats = {"today": 0, "present": 0}
spoof_alert      = ""

# Tracks student_ids that should show tick mark, with expiry time
# { student_id: expiry_timestamp }
_tick_display: dict = {}

# ── Enrollment state ──────────────────────────────────────────────────────────
enrollment_state = {
    "running":    False,
    "status":     "idle",      # idle | waiting_blink | capturing | done | failed
    "message":    "",
    "progress":   0,           # 0-5 poses captured
    "encodings":  None,
}
enrollment_lock = threading.Lock()


# ── Auth ───────────────────────────────────────────────────────────────────────

def require_role(*roles):
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "role" not in session or session["role"] not in roles:
                # Return JSON for AJAX calls, redirect for normal page loads
                if request.headers.get("X-Requested-With") == "XMLHttpRequest" \
                   or request.is_json \
                   or request.method == "POST":
                    return jsonify({"ok": False, "msg": "Session expired. Please log in again."}), 401
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ── Camera thread ──────────────────────────────────────────────────────────────

def _camera_worker():
    """
    Camera background thread.

    Performance optimizations:
    - Outside active slot: only encode/stream frames, NO face recognition
    - Inside active slot: run recognition every 3rd frame, cache results
    - Encoding cache rebuilt only when student list changes
    """
    global camera_running, latest_frame, known_students, attendance_stats, spoof_alert

    cam_idx = int(database.get_setting("camera_index", "0"))
    cap = cv2.VideoCapture(cam_idx)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    if not cap.isOpened():
        logger.error("Cannot open camera.")
        camera_running = False
        return

    logger.info("Camera thread started.")
    fc           = 0
    last_results = []
    active_slot  = None   # cached slot — refreshed every 60 frames (~2 sec)

    while camera_running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.03)
            continue

        fc += 1

        # ── Check active slot every 60 frames (~2 seconds) ───────────────────
        if fc % 60 == 0 or fc == 1:
            active_slot, _ = att.get_active_session()

        if active_slot and known_students:
            # ── Inside slot: run face recognition every 3rd frame ─────────────
            if fc % 3 == 0:
                try:
                    last_results = face_utils.recognize_faces_in_frame(
                        frame, known_students)
                    now_ts = time.time()

                    for r in last_results:
                        sid  = r.get("student_id", "")
                        name = r.get("name", "")
                        if not sid:
                            continue
                        ok, msg = att.mark_attendance(sid, name)
                        if ok:
                            _tick_display[sid] = now_ts + 3.0
                            attendance_stats["today"] = len(
                                att.get_today_attendance())
                            logger.info(f"Marked: {name} ({sid}) — {active_slot}")

                except Exception as e:
                    logger.error(f"Recognition error: {e}")

            # Draw cached annotations on every frame
            now_ts       = time.time()
            active_ticks = {s for s, exp in list(_tick_display.items())
                            if now_ts < exp}
            annotated = frame.copy()
            if last_results:
                face_utils.draw_face_annotations(
                    annotated, last_results, active_ticks)

            # Slot label on frame
            cv2.putText(annotated, f"Slot: {active_slot}",
                        (10, annotated.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 212, 255), 1)

        else:
            # ── Outside slot: just show video, no recognition ─────────────────
            last_results = []   # clear stale boxes
            annotated    = frame.copy()

            # Show "No Active Slot" watermark
            msg = "No Active Slot — Attendance Not Recording"
            cv2.putText(annotated, msg,
                        (10, annotated.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                        (80, 80, 160), 1)

        # ── Flip + encode ─────────────────────────────────────────────────────
        annotated = cv2.flip(annotated, 1)
        _, jpeg   = cv2.imencode(".jpg", annotated,
                                 [cv2.IMWRITE_JPEG_QUALITY, 80])
        with frame_lock:
            latest_frame = jpeg.tobytes()

    cap.release()
    with frame_lock:
        latest_frame = None
    _tick_display.clear()
    logger.info("Camera thread stopped.")


def _gen_frames():
    """Generator for MJPEG stream."""
    placeholder = _make_placeholder()
    consecutive_none = 0
    while True:
        with frame_lock:
            frame = latest_frame
        if frame is None:
            consecutive_none += 1
            yield (b"--frame\r\nContent-Type:image/jpeg\r\n\r\n"
                   + placeholder + b"\r\n")
            # Back off slowly if camera not started yet
            time.sleep(0.05 if consecutive_none < 20 else 0.2)
        else:
            consecutive_none = 0
            yield (b"--frame\r\nContent-Type:image/jpeg\r\n\r\n"
                   + frame + b"\r\n")
            time.sleep(0.030)


def _make_placeholder() -> bytes:
    """A simple dark placeholder image when camera is off."""
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:] = (13, 21, 38)
    cv2.putText(img, "Camera is offline", (170, 230),
                cv2.FONT_HERSHEY_DUPLEX, 1.0, (74, 104, 133), 2)
    cv2.putText(img, "Click  Start Camera  to begin", (120, 275),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (44, 74, 103), 1)
    _, jpeg = cv2.imencode(".jpg", img)
    return jpeg.tobytes()


# ── Enrollment thread ──────────────────────────────────────────────────────────

def _enrollment_worker(student_id, name, class_name, branch, dob):
    global enrollment_state
    with enrollment_lock:
        enrollment_state.update({"running": True, "status": "waiting_blink",
                                  "message": "Please BLINK to confirm liveness…",
                                  "progress": 0, "encodings": None})

    encodings = face_utils.capture_face_encoding(num_samples=5)

    with enrollment_lock:
        if encodings is None:
            enrollment_state.update({"running": False, "status": "failed",
                                      "message": "Capture cancelled or failed."})
            return
        ok = database.add_student(student_id, name, encodings, class_name, branch, dob)
        if ok:
            enrollment_state.update({"running": False, "status": "done",
                                      "message": f"{name} registered successfully!",
                                      "encodings": encodings})
            # Refresh known students
            global known_students
            known_students = database.get_all_students()
            logger.info(f"Enrollment done: {name} ({student_id})")
        else:
            enrollment_state.update({"running": False, "status": "failed",
                                      "message": "Registration failed — ID may already exist."})


# ── Routes: Auth ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "role" in session:
        return redirect(url_for(session["role"] + "_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        role     = request.form.get("role", "")
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "").strip()

        if role == "admin":
            if database.verify_admin(identity, password):
                session.update({"role": "admin", "username": identity})
                return redirect(url_for("admin_dashboard"))
            error = "Invalid admin username or password."

        elif role == "teacher":
            teacher = database.verify_teacher(identity, password)
            if teacher:
                session.update({"role": "teacher",
                                 "teacher_id": teacher["teacher_id"],
                                 "name": teacher["name"],
                                 "email": teacher["email"],
                                 "class_assigned": teacher["class_assigned"]})
                return redirect(url_for("teacher_dashboard"))
            error = "Invalid email or password."

        elif role == "student":
            dob = request.form.get("dob", "").strip()
            if database.verify_student(identity, dob):
                session.update({"role": "student",
                                 "student_id": identity.upper()})
                return redirect(url_for("student_dashboard"))
            error = "Invalid Student ID or Date of Birth."
        else:
            error = "Please select a role."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Routes: Camera ─────────────────────────────────────────────────────────────

@app.route("/video_feed")
@require_role("admin", "teacher")
def video_feed():
    return Response(stream_with_context(_gen_frames()),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/camera/start", methods=["POST"])
@require_role("admin", "teacher")
def camera_start():
    global camera_running, camera_thread, known_students
    if camera_running:
        return jsonify({"ok": True, "msg": "Camera already running."})
    known_students = database.get_all_students()
    face_utils._rebuild_cache(known_students)
    camera_running = True
    att.reset_cooldowns()
    camera_thread  = threading.Thread(target=_camera_worker, daemon=True)
    camera_thread.start()
    return jsonify({"ok": True, "msg": f"Camera started. {len(known_students)} students loaded."})


@app.route("/camera/stop", methods=["POST"])
@require_role("admin", "teacher")
def camera_stop():
    global camera_running
    camera_running = False
    return jsonify({"ok": True, "msg": "Camera stopped."})


@app.route("/camera/status")
@require_role("admin", "teacher")
def camera_status():
    today_recs = att.get_today_attendance()
    slot       = database.get_current_slot()
    return jsonify({
        "running":        camera_running,
        "today":          len(today_recs),
        "total_students": len(database.get_all_students_info()),
        "total_teachers": len(database.get_all_teachers()),
        "slot":           slot["label"] if slot else None,
        "spoof_alert":    spoof_alert,
        "records":        today_recs[-10:][::-1],
    })


# ── Routes: Admin ──────────────────────────────────────────────────────────────

@app.route("/admin")
@require_role("admin")
def admin_dashboard():
    today       = date.today().strftime("%Y-%m-%d")
    today_recs  = database.get_attendance_by_date(today)
    settings    = database.get_all_settings()
    return render_template("admin/dashboard.html",
        students        = database.get_all_students_info(),
        teachers        = database.get_all_teachers(),
        records         = database.get_all_attendance()[:200],
        today_records   = today_recs,
        today_count     = len(today_recs),
        total_students  = len(database.get_all_students_info()),
        total_teachers  = len(database.get_all_teachers()),
        slots           = database.get_slots(),
        classes         = database.get_all_classes(),
        settings        = settings,
        today           = today,
        camera_running  = camera_running,
    )


@app.route("/admin/add_teacher", methods=["POST"])
@require_role("admin")
def admin_add_teacher():
    ok = database.add_teacher(
        request.form["teacher_id"], request.form["name"],
        request.form["email"],      request.form["password"],
        request.form.get("subject",""), request.form.get("class_assigned",""))
    return jsonify({"ok": ok,
                    "msg": "Teacher added." if ok else "ID or Email already exists."})


@app.route("/admin/stats")
@require_role("admin")
def admin_stats():
    today      = date.today().strftime("%Y-%m-%d")
    today_recs = database.get_attendance_by_date(today)
    return jsonify({
        "total_students": len(database.get_all_students_info()),
        "total_teachers": len(database.get_all_teachers()),
        "today_count":    len(today_recs),
    })


@app.route("/admin/delete_teacher/<tid>", methods=["POST"])
@require_role("admin")
def admin_delete_teacher(tid):
    try:
        ok = database.delete_teacher(tid)
        msg = f"Teacher {tid} deleted." if ok else f"Teacher '{tid}' not found."
        logger.info(f"Delete teacher '{tid}': {ok}")
        return jsonify({"ok": ok, "msg": msg})
    except Exception as e:
        logger.error(f"Error deleting teacher '{tid}': {e}")
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/admin/delete_student/<sid>", methods=["POST"])
@require_role("admin")
def admin_delete_student(sid):
    try:
        ok = database.delete_student(sid)
        if ok:
            global known_students
            known_students = database.get_all_students()
        msg = f"Student {sid} deleted." if ok else f"Student '{sid}' not found."
        logger.info(f"Delete student '{sid}': {ok}")
        return jsonify({"ok": ok, "msg": msg})
    except Exception as e:
        logger.error(f"Error deleting student '{sid}': {e}")
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/admin/settings", methods=["POST"])
@require_role("admin")
def admin_save_settings():
    for key in ["day_start","day_end","slot_duration","break_start","break_end",
                "confidence_threshold","cooldown_minutes","camera_index"]:
        val = request.form.get(key,"").strip()
        if val:
            database.set_setting(key, val)
    try:
        face_utils.MIN_CONFIDENCE = float(
            database.get_setting("confidence_threshold","60"))
    except Exception:
        pass
    return jsonify({"ok": True, "msg": "Settings saved."})


@app.route("/admin/export")
@require_role("admin", "teacher")
def export_excel():
    path = att.export_to_excel()
    if path and os.path.exists(path):
        return send_file(path, as_attachment=True)
    return "No records to export.", 404


# ── Routes: Teacher ────────────────────────────────────────────────────────────

@app.route("/teacher")
@require_role("teacher")
def teacher_dashboard():
    today      = date.today().strftime("%Y-%m-%d")
    today_recs = database.get_attendance_by_date(today)
    return render_template("teacher/dashboard.html",
        teacher_name     = session.get("name",""),
        students_summary = database.get_all_students_summary(),
        classes          = database.get_all_classes(),
        slots            = database.get_slots(),
        today            = today,
        today_records    = today_recs,
        camera_running   = camera_running,
    )


@app.route("/teacher/attendance")
@require_role("teacher", "admin")
def teacher_attendance_api():
    filter_date  = request.args.get("date","").strip()
    filter_class = request.args.get("class","").strip()
    filter_slot  = request.args.get("slot","").strip()
    records = (database.get_attendance_by_date(filter_date)
               if filter_date else database.get_all_attendance())
    if filter_class:
        ids = {s["student_id"] for s in database.get_all_students_info()
               if s.get("class_name") == filter_class}
        records = [r for r in records if r["student_id"] in ids]
    if filter_slot:
        records = [r for r in records if r.get("slot","") == filter_slot]
    return jsonify(records)


@app.route("/teacher/student/<sid>")
@require_role("teacher", "admin")
def teacher_student_profile(sid):
    student = database.get_student_by_id(sid)
    if not student: return "Student not found", 404
    records     = database.get_student_attendance(sid)
    summary     = database.get_student_summary(sid)
    slots       = database.get_slots()
    today       = date.today().strftime("%Y-%m-%d")
    today_slots = {r.get("slot","") for r in records if r["date"] == today}
    months      = sorted({r["date"][:7] for r in records}, reverse=True)
    return render_template("teacher/student_profile.html",
        student      = student, records=records, summary=summary,
        slots        = slots,   today_slots=today_slots, months=months,
        teacher_name = session.get("name",""),
        back_url     = url_for("teacher_dashboard") if session.get("role")=="teacher"
                       else url_for("admin_dashboard"))


@app.route("/teacher/manual_mark", methods=["POST"])
@require_role("teacher", "admin")
def teacher_manual_mark():
    d = request.get_json()
    ok = database.manual_mark_attendance(
        d["student_id"], d["date"], d["slot"],
        marked_by=f"{session.get('role')}:{session.get('name', session.get('username',''))}")
    return jsonify({"ok": ok,
                    "msg": "Marked present." if ok else "Already marked or invalid."})


@app.route("/teacher/remove_mark", methods=["POST"])
@require_role("teacher", "admin")
def teacher_remove_mark():
    d = request.get_json()
    ok = database.remove_attendance(d["student_id"], d["date"], d["slot"])
    return jsonify({"ok": ok, "msg": "Removed." if ok else "Not found."})


# ── Routes: Student Enrollment (Teacher + Admin) ───────────────────────────────

@app.route("/enroll", methods=["GET"])
@require_role("teacher", "admin")
def enroll_page():
    return render_template("teacher/enroll.html",
        classes      = database.get_all_classes(),
        teacher_name = session.get("name", session.get("username","")),
        role         = session.get("role",""))


@app.route("/enroll/start", methods=["POST"])
@require_role("teacher", "admin")
def enroll_start():
    with enrollment_lock:
        if enrollment_state["running"]:
            return jsonify({"ok": False, "msg": "Enrollment already in progress."})

    data       = request.get_json()
    student_id = data.get("student_id","").strip().upper()
    name       = data.get("name","").strip().title()
    class_name = data.get("class_name","").strip()
    branch     = data.get("branch","").strip()
    dob        = data.get("dob","").strip()

    if not student_id or not name:
        return jsonify({"ok": False, "msg": "Student ID and Name are required."})
    if not dob:
        return jsonify({"ok": False, "msg": "Date of Birth is required (student login password)."})
    if database.student_exists(student_id):
        return jsonify({"ok": False, "msg": f"ID '{student_id}' already exists."})

    t = threading.Thread(target=_enrollment_worker,
                         args=(student_id, name, class_name, branch, dob),
                         daemon=True)
    t.start()
    return jsonify({"ok": True, "msg": "Enrollment started. Follow camera instructions."})


@app.route("/enroll/status")
@require_role("teacher", "admin")
def enroll_status():
    with enrollment_lock:
        return jsonify({
            "status":   enrollment_state["status"],
            "message":  enrollment_state["message"],
            "progress": enrollment_state["progress"],
        })


@app.route("/enroll/cancel", methods=["POST"])
@require_role("teacher", "admin")
def enroll_cancel():
    with enrollment_lock:
        enrollment_state.update({"running": False, "status": "idle",
                                  "message": "", "progress": 0})
    return jsonify({"ok": True})


# ── Routes: Student ────────────────────────────────────────────────────────────

@app.route("/student")
@require_role("student")
def student_dashboard():
    sid     = session["student_id"]
    student = database.get_student_by_id(sid)
    if not student:
        session.clear(); return redirect(url_for("login"))
    records     = database.get_student_attendance(sid)
    summary     = database.get_student_summary(sid)
    all_slots   = [s["label"] for s in database.get_slots()]
    today       = date.today().strftime("%Y-%m-%d")
    today_slots = {r.get("slot","") for r in records if r["date"] == today}
    months      = sorted({r["date"][:7] for r in records}, reverse=True)
    return render_template("student/dashboard.html",
        student=student, records=records, summary=summary,
        all_slots=all_slots, today_slots=today_slots, months=months)


if __name__ == "__main__":
    database.initialize_database()
    app.run(debug=False, port=5000, threaded=True)
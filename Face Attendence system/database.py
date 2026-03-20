"""
database.py - All database operations for FaceTrack.
Roles: Admin, Teacher (email+password), Student (ID+DOB)
"""

import sqlite3
import pickle
import os
import logging
import hashlib
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DB_PATH = os.path.join("data", "attendance_system.db")


def get_connection():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def initialize_database():
    conn = get_connection()
    c = conn.cursor()

    # Students
    c.execute("""CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        class_name TEXT DEFAULT '',
        branch TEXT DEFAULT '',
        dob TEXT DEFAULT '',
        encoding BLOB,
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Attendance (slot-based, no Late)
    c.execute("""CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        name TEXT NOT NULL,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        slot TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Present',
        marked_by TEXT DEFAULT 'auto',
        marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(student_id),
        UNIQUE(student_id, date, slot)
    )""")

    # Admins
    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Teachers
    c.execute("""CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_id TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        subject TEXT DEFAULT '',
        class_assigned TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Settings
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")

    # Migrations
    existing_att = {row[1] for row in c.execute("PRAGMA table_info(attendance)")}
    for col, defn in [("slot", "TEXT NOT NULL DEFAULT ''"),
                      ("marked_by", "TEXT DEFAULT 'auto'")]:
        if col not in existing_att:
            c.execute(f"ALTER TABLE attendance ADD COLUMN {col} {defn}")

    existing_stu = {row[1] for row in c.execute("PRAGMA table_info(students)")}
    for col, defn in [("class_name","TEXT DEFAULT ''"),
                      ("branch","TEXT DEFAULT ''"),
                      ("dob","TEXT DEFAULT ''")]:
        if col not in existing_stu:
            c.execute(f"ALTER TABLE students ADD COLUMN {col} {defn}")

    # Default admin
    c.execute("SELECT 1 FROM admins WHERE username='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO admins (username,password) VALUES (?,?)",
                  ("admin", _hash("admin123")))

    # Default settings
    defaults = {
        "day_start": "09:00", "day_end": "17:00",
        "slot_duration": "60",
        "break_start": "13:00", "break_end": "14:00",
        "confidence_threshold": "60",
        "cooldown_minutes": "50",
        "camera_index": "0",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))

    conn.commit()
    conn.close()
    logger.info("Database initialized.")


# ── Slots ──────────────────────────────────────────────────────────────────────

def get_slots() -> list:
    s = get_all_settings()
    from datetime import datetime, timedelta
    fmt = "%H:%M"
    cur = datetime.strptime(s.get("day_start", "09:00"), fmt)
    end = datetime.strptime(s.get("day_end",   "17:00"), fmt)
    b_s = datetime.strptime(s.get("break_start","13:00"), fmt)
    b_e = datetime.strptime(s.get("break_end",  "14:00"), fmt)
    delta = timedelta(minutes=int(s.get("slot_duration", "60")))
    slots = []
    while cur < end:
        nxt = min(cur + delta, end)
        if cur >= b_e or nxt <= b_s:
            slots.append({"label": f"{cur.strftime(fmt)}-{nxt.strftime(fmt)}",
                          "start": cur.strftime(fmt), "end": nxt.strftime(fmt)})
        cur = nxt
    return slots


def get_current_slot(now=None) -> dict | None:
    from datetime import datetime
    if now is None: now = datetime.now()
    current = now.strftime("%H:%M")
    for slot in get_slots():
        if slot["start"] <= current < slot["end"]:
            return slot
    return None


# ── Admin auth ─────────────────────────────────────────────────────────────────

def verify_admin(username: str, password: str) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT password FROM admins WHERE username=?", (username,))
    row = c.fetchone(); conn.close()
    return row is not None and row["password"] == _hash(password)

def get_all_admins() -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT id,username,created_at FROM admins ORDER BY username")
    rows = c.fetchall(); conn.close()
    return [dict(r) for r in rows]

def add_admin(username: str, password: str) -> bool:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("INSERT INTO admins (username,password) VALUES (?,?)",
                  (username, _hash(password)))
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError: return False

def change_admin_password(username: str, new_password: str) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("UPDATE admins SET password=? WHERE username=?",
              (_hash(new_password), username))
    changed = c.rowcount > 0; conn.commit(); conn.close(); return changed

def delete_admin(username: str) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("DELETE FROM admins WHERE username=?", (username,))
    deleted = c.rowcount > 0; conn.commit(); conn.close(); return deleted


# ── Teacher auth ───────────────────────────────────────────────────────────────

def verify_teacher(email: str, password: str) -> dict | None:
    """Returns teacher dict if valid, else None."""
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT * FROM teachers WHERE email=?", (email.strip().lower(),))
    row = c.fetchone(); conn.close()
    if row and row["password"] == _hash(password):
        return dict(row)
    return None

def add_teacher(teacher_id: str, name: str, email: str,
                password: str, subject: str = "", class_assigned: str = "") -> bool:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("""INSERT INTO teachers
                     (teacher_id,name,email,password,subject,class_assigned)
                     VALUES (?,?,?,?,?,?)""",
                  (teacher_id, name, email.lower(), _hash(password),
                   subject, class_assigned))
        conn.commit(); conn.close()
        logger.info(f"Teacher added: {name} ({email})")
        return True
    except sqlite3.IntegrityError as e:
        logger.warning(f"Teacher add failed: {e}"); return False

def get_all_teachers() -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT id,teacher_id,name,email,subject,class_assigned,created_at FROM teachers ORDER BY name")
    rows = c.fetchall(); conn.close()
    return [dict(r) for r in rows]

def update_teacher(teacher_id: str, name: str, email: str,
                   subject: str, class_assigned: str) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("""UPDATE teachers SET name=?,email=?,subject=?,class_assigned=?
                 WHERE teacher_id=?""",
              (name, email.lower(), subject, class_assigned, teacher_id))
    ok = c.rowcount > 0; conn.commit(); conn.close(); return ok

def change_teacher_password(teacher_id: str, new_password: str) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("UPDATE teachers SET password=? WHERE teacher_id=?",
              (_hash(new_password), teacher_id))
    ok = c.rowcount > 0; conn.commit(); conn.close(); return ok

def delete_teacher(teacher_id: str) -> bool:
    teacher_id = str(teacher_id).strip()
    if not teacher_id:
        return False
    conn = get_connection(); c = conn.cursor()
    c.execute("DELETE FROM teachers WHERE teacher_id=?", (teacher_id,))
    ok = c.rowcount > 0
    conn.commit(); conn.close()
    if ok: logger.info(f"Teacher deleted: {teacher_id}")
    else:  logger.warning(f"Teacher not found for delete: '{teacher_id}'")
    return ok


# ── Settings ───────────────────────────────────────────────────────────────────

def get_setting(key, default=None):
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_connection(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    conn.commit(); conn.close()

def get_all_settings() -> dict:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT key,value FROM settings")
    rows = c.fetchall(); conn.close()
    return {r["key"]: r["value"] for r in rows}


# ── Students ───────────────────────────────────────────────────────────────────

def add_student(student_id, name, encodings, class_name="", branch="", dob="") -> bool:
    if not encodings: return False
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("INSERT INTO students (student_id,name,class_name,branch,dob,encoding) VALUES (?,?,?,?,?,?)",
                  (student_id, name, class_name, branch, dob, pickle.dumps(encodings)))
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError: return False
    except Exception as e: logger.error(e); return False

def update_student(student_id, name, class_name, branch, dob) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("UPDATE students SET name=?,class_name=?,branch=?,dob=? WHERE student_id=?",
              (name, class_name, branch, dob, str(student_id)))
    ok = c.rowcount > 0; conn.commit(); conn.close(); return ok

def update_student_encoding(student_id, encodings) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("UPDATE students SET encoding=? WHERE student_id=?",
              (pickle.dumps(encodings), str(student_id)))
    ok = c.rowcount > 0; conn.commit(); conn.close(); return ok

def get_all_students() -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT student_id,name,class_name,branch,dob,encoding FROM students")
    rows = c.fetchall(); conn.close()
    result = []
    for row in rows:
        enc = row["encoding"]
        encodings = pickle.loads(enc) if enc else []
        if isinstance(encodings, np.ndarray): encodings = [encodings]
        result.append({"student_id": row["student_id"], "name": row["name"],
                       "class_name": row["class_name"], "branch": row["branch"],
                       "dob": row["dob"], "encoding": encodings})
    return result

def get_all_students_info() -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT student_id,name,class_name,branch,dob,registered_at FROM students ORDER BY name")
    rows = c.fetchall(); conn.close()
    return [dict(r) for r in rows]

def get_student_by_id(student_id) -> dict | None:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT student_id,name,class_name,branch,dob,registered_at FROM students WHERE student_id=?",
              (str(student_id),))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def student_exists(student_id) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT 1 FROM students WHERE student_id=?", (student_id,))
    r = c.fetchone(); conn.close(); return r is not None

def verify_student(student_id, dob) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT 1 FROM students WHERE student_id=? AND dob=?", (str(student_id), dob))
    r = c.fetchone(); conn.close(); return r is not None

def delete_student(student_id) -> bool:
    student_id = str(student_id)
    conn = None
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("DELETE FROM attendance WHERE student_id=?", (student_id,))
        c.execute("DELETE FROM students WHERE student_id=?", (student_id,))
        if c.rowcount > 0: conn.commit(); return True
        conn.rollback(); return False
    except Exception as e:
        if conn: conn.rollback()
        logger.error(e); return False
    finally:
        if conn: conn.close()

def get_all_classes() -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT DISTINCT class_name FROM students WHERE class_name!='' ORDER BY class_name")
    rows = c.fetchall(); conn.close()
    return [r["class_name"] for r in rows]


# ── Attendance ─────────────────────────────────────────────────────────────────

def insert_attendance(student_id, name, date, time_str, slot, marked_by="auto") -> bool:
    student_id = str(student_id)
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("SELECT 1 FROM attendance WHERE student_id=? AND date=? AND slot=?",
                  (student_id, date, slot))
        if c.fetchone(): conn.close(); return False
        c.execute("INSERT INTO attendance (student_id,name,date,time,slot,status,marked_by) VALUES (?,?,?,?,?,'Present',?)",
                  (student_id, name, date, time_str, slot, marked_by))
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError: return False
    except Exception as e: logger.error(e); return False

def get_attendance_by_date(query_date) -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT student_id,name,date,time,slot,status,marked_by FROM attendance WHERE date=? ORDER BY slot,time",
              (query_date,))
    rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]

def get_all_attendance() -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT student_id,name,date,time,slot,status,marked_by FROM attendance ORDER BY date DESC,slot,time DESC")
    rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]

def get_student_attendance(student_id) -> list:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT date,time,slot,status,marked_by FROM attendance WHERE student_id=? ORDER BY date DESC,slot",
              (str(student_id),))
    rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]

def get_student_summary(student_id) -> dict:
    records = get_student_attendance(student_id)
    total = len(records)
    present = sum(1 for r in records if r["status"] == "Present")
    pct = round(present / total * 100, 1) if total else 0.0
    return {"total": total, "present": present, "percentage": pct}

def get_all_students_summary() -> list:
    """Returns attendance summary for every student — used by teacher dashboard."""
    students = get_all_students_info()
    result = []
    for s in students:
        summary = get_student_summary(s["student_id"])
        result.append({**s, **summary})
    return result

def manual_mark_attendance(student_id, date, slot, marked_by="teacher") -> bool:
    """Teacher manually marks a student present for a specific slot."""
    student = get_student_by_id(student_id)
    if not student: return False
    from datetime import datetime
    time_str = datetime.now().strftime("%H:%M:%S")
    return insert_attendance(student_id, student["name"],
                             date, time_str, slot, marked_by)

def remove_attendance(student_id, date, slot) -> bool:
    """Teacher removes an attendance mark."""
    conn = get_connection(); c = conn.cursor()
    c.execute("DELETE FROM attendance WHERE student_id=? AND date=? AND slot=?",
              (str(student_id), date, slot))
    ok = c.rowcount > 0; conn.commit(); conn.close(); return ok
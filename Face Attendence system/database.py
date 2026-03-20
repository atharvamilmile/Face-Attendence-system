"""
database.py - All database operations for FaceTrack.

Slot system:
  - Slots are 1-hour blocks e.g. "09:00-10:00", "10:00-11:00" ... "16:00-17:00"
  - Break slot (default 13:00-14:00) is skipped — no attendance
  - Status is always "Present" — no Late concept
  - Unique constraint: (student_id, date, slot) — no duplicate per slot
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


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def initialize_database():
    conn = get_connection()
    c = conn.cursor()

    # ── Students ──────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id    TEXT    UNIQUE NOT NULL,
            name          TEXT    NOT NULL,
            class_name    TEXT    DEFAULT '',
            branch        TEXT    DEFAULT '',
            dob           TEXT    DEFAULT '',
            encoding      BLOB,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Attendance — slot-based ────────────────────────────────────────────────
    # 'slot' stores the hour block label e.g. "09:00-10:00"
    # UNIQUE on (student_id, date, slot) prevents duplicate marks
    c.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            name       TEXT NOT NULL,
            date       TEXT NOT NULL,
            time       TEXT NOT NULL,
            slot       TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'Present',
            marked_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students(student_id),
            UNIQUE(student_id, date, slot)
        )
    """)

    # ── Admins ────────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Settings ──────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # ── Migrate old DBs ───────────────────────────────────────────────────────
    existing_att = {row[1] for row in c.execute("PRAGMA table_info(attendance)")}
    # Replace old 'session' column usage with 'slot'
    if "slot" not in existing_att:
        try:
            c.execute("ALTER TABLE attendance ADD COLUMN slot TEXT NOT NULL DEFAULT ''")
            logger.info("Migrated: added 'slot' column to attendance.")
        except Exception:
            pass
    # Remove 'session' column reference safely (SQLite can't DROP columns in older versions)

    existing_stu = {row[1] for row in c.execute("PRAGMA table_info(students)")}
    for col, defn in [("class_name","TEXT DEFAULT ''"),
                      ("branch",    "TEXT DEFAULT ''"),
                      ("dob",       "TEXT DEFAULT ''")]:
        if col not in existing_stu:
            c.execute(f"ALTER TABLE students ADD COLUMN {col} {defn}")

    # ── Default admin ─────────────────────────────────────────────────────────
    c.execute("SELECT 1 FROM admins WHERE username='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO admins (username,password) VALUES (?,?)",
                  ("admin", _hash_password("admin123")))
        logger.info("Default admin created: admin / admin123")

    # ── Default settings ──────────────────────────────────────────────────────
    defaults = {
        "day_start":          "09:00",   # first slot starts at
        "day_end":            "17:00",   # last slot ends at
        "slot_duration":      "60",      # minutes per slot
        "break_start":        "13:00",   # break begins
        "break_end":          "14:00",   # break ends
        "confidence_threshold": "60",
        "cooldown_minutes":     "50",    # slightly under 60 so camera catches re-entry
        "camera_index":         "0",
    }
    for key, value in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (key, value))

    conn.commit()
    conn.close()
    logger.info("Database initialized.")


# ── Slot helpers ───────────────────────────────────────────────────────────────

def get_slots() -> list[dict]:
    """
    Build the list of hourly attendance slots for the day from settings.
    Break window is excluded.

    Returns list of dicts:
        { label: "09:00-10:00", start: "09:00", end: "10:00" }
    """
    s            = get_all_settings()
    day_start    = s.get("day_start",     "09:00")
    day_end      = s.get("day_end",       "17:00")
    slot_min     = int(s.get("slot_duration", "60"))
    break_start  = s.get("break_start",   "13:00")
    break_end    = s.get("break_end",     "14:00")

    from datetime import datetime, timedelta
    fmt   = "%H:%M"
    cur   = datetime.strptime(day_start, fmt)
    end   = datetime.strptime(day_end,   fmt)
    b_s   = datetime.strptime(break_start, fmt)
    b_e   = datetime.strptime(break_end,   fmt)
    delta = timedelta(minutes=slot_min)

    slots = []
    while cur < end:
        nxt = cur + delta
        if nxt > end:
            nxt = end
        # Skip if this slot overlaps the break window
        if not (cur >= b_e or nxt <= b_s):
            cur = nxt
            continue
        slots.append({
            "label": f"{cur.strftime(fmt)}-{nxt.strftime(fmt)}",
            "start": cur.strftime(fmt),
            "end":   nxt.strftime(fmt),
        })
        cur = nxt

    return slots


def get_current_slot(now=None) -> dict | None:
    """
    Return the slot dict that is currently active, or None if it's
    outside working hours or during break.
    """
    from datetime import datetime
    if now is None:
        now = datetime.now()
    current = now.strftime("%H:%M")
    for slot in get_slots():
        if slot["start"] <= current < slot["end"]:
            return slot
    return None


# ── Admin auth ────────────────────────────────────────────────────────────────

def verify_admin(username: str, password: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT password FROM admins WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    return row is not None and row["password"] == _hash_password(password)


def get_all_admins() -> list:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, created_at FROM admins ORDER BY username")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_admin(username: str, password: str) -> bool:
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("INSERT INTO admins (username,password) VALUES (?,?)",
                  (username, _hash_password(password)))
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError:
        return False


def change_admin_password(username: str, new_password: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE admins SET password=? WHERE username=?",
              (_hash_password(new_password), username))
    changed = c.rowcount > 0
    conn.commit(); conn.close(); return changed


def delete_admin(username: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE username=?", (username,))
    deleted = c.rowcount > 0
    conn.commit(); conn.close(); return deleted


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str, default=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    conn.commit(); conn.close()


def get_all_settings() -> dict:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT key,value FROM settings")
    rows = c.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ── Students ──────────────────────────────────────────────────────────────────

def add_student(student_id: str, name: str, encodings,
                class_name="", branch="", dob="") -> bool:
    if not encodings:
        logger.error("No encodings provided."); return False
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute(
            "INSERT INTO students (student_id,name,class_name,branch,dob,encoding) "
            "VALUES (?,?,?,?,?,?)",
            (student_id, name, class_name, branch, dob, pickle.dumps(encodings))
        )
        conn.commit(); conn.close()
        logger.info(f"Student added: {name} ({student_id})")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"ID '{student_id}' already exists."); return False
    except Exception as e:
        logger.error(f"Error adding student: {e}"); return False


def update_student(student_id: str, name: str,
                   class_name: str, branch: str, dob: str) -> bool:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("UPDATE students SET name=?,class_name=?,branch=?,dob=? WHERE student_id=?",
                  (name, class_name, branch, dob, str(student_id)))
        updated = c.rowcount > 0
        conn.commit(); conn.close(); return updated
    except Exception as e:
        logger.error(f"Error updating student: {e}"); return False


def update_student_encoding(student_id: str, encodings) -> bool:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("UPDATE students SET encoding=? WHERE student_id=?",
                  (pickle.dumps(encodings), str(student_id)))
        updated = c.rowcount > 0
        conn.commit(); conn.close(); return updated
    except Exception as e:
        logger.error(f"Error updating encoding: {e}"); return False


def get_all_students() -> list:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("SELECT student_id,name,class_name,branch,dob,encoding FROM students")
        rows = c.fetchall(); conn.close()
        students = []
        for row in rows:
            enc = row["encoding"]
            if enc:
                encodings = pickle.loads(enc)
                if isinstance(encodings, np.ndarray): encodings = [encodings]
            else:
                encodings = []
            students.append({
                "student_id": row["student_id"], "name":       row["name"],
                "class_name": row["class_name"], "branch":     row["branch"],
                "dob":        row["dob"],         "encoding":   encodings,
            })
        return students
    except Exception as e:
        logger.error(f"Error fetching students: {e}"); return []


def get_all_students_info() -> list:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("SELECT student_id,name,class_name,branch,dob,registered_at FROM students ORDER BY name")
        rows = c.fetchall(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error: {e}"); return []


def get_student_by_id(student_id: str) -> dict | None:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("SELECT student_id,name,class_name,branch,dob,registered_at FROM students WHERE student_id=?",
                  (str(student_id),))
        row = c.fetchone(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error: {e}"); return None


def student_exists(student_id: str) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT 1 FROM students WHERE student_id=?", (student_id,))
    result = c.fetchone(); conn.close()
    return result is not None


def verify_student(student_id: str, dob: str) -> bool:
    conn = get_connection(); c = conn.cursor()
    c.execute("SELECT 1 FROM students WHERE student_id=? AND dob=?",
              (str(student_id), dob))
    result = c.fetchone(); conn.close()
    return result is not None


def delete_student(student_id: str) -> bool:
    student_id = str(student_id)
    conn = None
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("DELETE FROM attendance WHERE student_id=?", (student_id,))
        c.execute("DELETE FROM students WHERE student_id=?",   (student_id,))
        if c.rowcount > 0:
            conn.commit(); return True
        conn.rollback(); return False
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"Error deleting '{student_id}': {e}"); return False
    finally:
        if conn: conn.close()


# ── Attendance ─────────────────────────────────────────────────────────────────

def insert_attendance(student_id: str, name: str, date: str,
                      time_str: str, slot: str) -> bool:
    """
    Insert one attendance record for a slot.
    Returns False if a record already exists for (student_id, date, slot).
    Status is always 'Present' — no Late concept.
    """
    student_id = str(student_id)
    try:
        conn = get_connection(); c = conn.cursor()
        # UNIQUE constraint handles duplicate prevention,
        # but check first for a cleaner return value
        c.execute("SELECT 1 FROM attendance WHERE student_id=? AND date=? AND slot=?",
                  (student_id, date, slot))
        if c.fetchone():
            conn.close(); return False
        c.execute(
            "INSERT INTO attendance (student_id,name,date,time,slot,status) "
            "VALUES (?,?,?,?,?,'Present')",
            (student_id, name, date, time_str, slot)
        )
        conn.commit(); conn.close()
        logger.info(f"Attendance: {name} [{student_id}] slot={slot} @ {time_str}")
        return True
    except sqlite3.IntegrityError:
        # Caught by UNIQUE constraint as backup
        conn.close(); return False
    except Exception as e:
        logger.error(f"Error inserting attendance: {e}"); return False


def get_attendance_by_date(query_date: str) -> list:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("""
            SELECT student_id, name, date, time, slot, status
            FROM attendance WHERE date=? ORDER BY slot, time
        """, (query_date,))
        rows = c.fetchall(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error: {e}"); return []


def get_all_attendance() -> list:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("""
            SELECT student_id, name, date, time, slot, status
            FROM attendance ORDER BY date DESC, slot, time DESC
        """)
        rows = c.fetchall(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error: {e}"); return []


def get_student_attendance(student_id: str) -> list:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("""
            SELECT date, time, slot, status
            FROM attendance WHERE student_id=?
            ORDER BY date DESC, slot
        """, (str(student_id),))
        rows = c.fetchall(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error: {e}"); return []


def get_attendance_by_class(class_name: str) -> list:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("""
            SELECT a.student_id, a.name, a.date, a.time, a.slot, a.status
            FROM attendance a
            JOIN students s ON a.student_id = s.student_id
            WHERE s.class_name=?
            ORDER BY a.date DESC, a.slot, a.time
        """, (class_name,))
        rows = c.fetchall(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error: {e}"); return []


def get_student_summary(student_id: str) -> dict:
    records = get_student_attendance(student_id)
    total   = len(records)
    present = sum(1 for r in records if r["status"] == "Present")
    # Total possible = number of slots per day × number of working days
    pct = round(present / total * 100, 1) if total else 0.0
    return {"total": total, "present": present, "percentage": pct}


def get_all_classes() -> list:
    try:
        conn = get_connection(); c = conn.cursor()
        c.execute("SELECT DISTINCT class_name FROM students WHERE class_name!='' ORDER BY class_name")
        rows = c.fetchall(); conn.close()
        return [r["class_name"] for r in rows]
    except Exception as e:
        logger.error(f"Error: {e}"); return []
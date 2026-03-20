"""
database.py - All database operations for FaceTrack.
Attendance logic: Present (camera detected) or Absent (auto at slot end).
"""

import sqlite3, pickle, os, logging, hashlib
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DB_PATH = os.path.join("data", "attendance_system.db")


def get_connection():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash(p): return hashlib.sha256(p.encode()).hexdigest()


def initialize_database():
    conn = get_connection(); c = conn.cursor()

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

    # Attendance: status = 'Present' or 'Absent'
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

    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

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

    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")

    # Migrations
    ea = {r[1] for r in c.execute("PRAGMA table_info(attendance)")}
    for col, defn in [("slot","TEXT NOT NULL DEFAULT ''"),
                      ("status","TEXT NOT NULL DEFAULT 'Present'"),
                      ("marked_by","TEXT DEFAULT 'auto'")]:
        if col not in ea:
            c.execute(f"ALTER TABLE attendance ADD COLUMN {col} {defn}")

    es = {r[1] for r in c.execute("PRAGMA table_info(students)")}
    for col, defn in [("class_name","TEXT DEFAULT ''"),
                      ("branch","TEXT DEFAULT ''"),("dob","TEXT DEFAULT ''")]:
        if col not in es:
            c.execute(f"ALTER TABLE students ADD COLUMN {col} {defn}")

    # Default admin
    c.execute("SELECT 1 FROM admins WHERE username='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO admins (username,password) VALUES (?,?)",
                  ("admin", _hash("admin123")))

    defaults = {
        "day_start":"09:00","day_end":"17:00","slot_duration":"60",
        "break_start":"13:00","break_end":"14:00",
        "confidence_threshold":"60","cooldown_minutes":"50","camera_index":"0",
    }
    for k,v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)",(k,v))

    conn.commit(); conn.close()
    logger.info("Database initialized.")


# ── Slot helpers ───────────────────────────────────────────────────────────────

def get_slots() -> list:
    """Return all working slots for the day excluding break."""
    s = get_all_settings()
    from datetime import datetime, timedelta
    fmt   = "%H:%M"
    cur   = datetime.strptime(s.get("day_start","09:00"), fmt)
    end   = datetime.strptime(s.get("day_end","17:00"), fmt)
    b_s   = datetime.strptime(s.get("break_start","13:00"), fmt)
    b_e   = datetime.strptime(s.get("break_end","14:00"), fmt)
    delta = timedelta(minutes=int(s.get("slot_duration","60")))
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


def get_all_slot_labels() -> list:
    """Return just the label strings for all slots."""
    return [s["label"] for s in get_slots()]


# ── Admin ──────────────────────────────────────────────────────────────────────

def verify_admin(u, p):
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT password FROM admins WHERE username=?",(u,))
    r=c.fetchone();conn.close()
    return r and r["password"]==_hash(p)

def get_all_admins():
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT id,username,created_at FROM admins ORDER BY username")
    r=c.fetchall();conn.close();return [dict(x) for x in r]

def add_admin(u,p):
    try:
        conn=get_connection();c=conn.cursor()
        c.execute("INSERT INTO admins (username,password) VALUES (?,?)",(u,_hash(p)))
        conn.commit();conn.close();return True
    except sqlite3.IntegrityError:return False

def change_admin_password(u,p):
    conn=get_connection();c=conn.cursor()
    c.execute("UPDATE admins SET password=? WHERE username=?",(_hash(p),u))
    ok=c.rowcount>0;conn.commit();conn.close();return ok

def delete_admin(u):
    conn=get_connection();c=conn.cursor()
    c.execute("DELETE FROM admins WHERE username=?",(u,))
    ok=c.rowcount>0;conn.commit();conn.close();return ok


# ── Teacher ────────────────────────────────────────────────────────────────────

def verify_teacher(email, password):
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT * FROM teachers WHERE email=?",(email.strip().lower(),))
    r=c.fetchone();conn.close()
    if r and r["password"]==_hash(password):return dict(r)
    return None

def add_teacher(tid,name,email,password,subject="",cls=""):
    try:
        conn=get_connection();c=conn.cursor()
        c.execute("INSERT INTO teachers (teacher_id,name,email,password,subject,class_assigned) VALUES (?,?,?,?,?,?)",
                  (tid,name,email.lower(),_hash(password),subject,cls))
        conn.commit();conn.close();return True
    except sqlite3.IntegrityError:return False

def get_all_teachers():
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT id,teacher_id,name,email,subject,class_assigned,created_at FROM teachers ORDER BY name")
    r=c.fetchall();conn.close();return [dict(x) for x in r]

def update_teacher(tid,name,email,subject,cls):
    conn=get_connection();c=conn.cursor()
    c.execute("UPDATE teachers SET name=?,email=?,subject=?,class_assigned=? WHERE teacher_id=?",
              (name,email.lower(),subject,cls,tid))
    ok=c.rowcount>0;conn.commit();conn.close();return ok

def change_teacher_password(tid,p):
    conn=get_connection();c=conn.cursor()
    c.execute("UPDATE teachers SET password=? WHERE teacher_id=?",(_hash(p),tid))
    ok=c.rowcount>0;conn.commit();conn.close();return ok

def delete_teacher(teacher_id):
    teacher_id=str(teacher_id).strip()
    if not teacher_id:return False
    conn=get_connection();c=conn.cursor()
    c.execute("DELETE FROM teachers WHERE teacher_id=?",(teacher_id,))
    ok=c.rowcount>0;conn.commit();conn.close()
    if ok:logger.info(f"Teacher deleted:{teacher_id}")
    return ok


# ── Settings ───────────────────────────────────────────────────────────────────

def get_setting(key,default=None):
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?",(key,))
    r=c.fetchone();conn.close();return r["value"] if r else default

def set_setting(key,value):
    conn=get_connection();c=conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",(key,value))
    conn.commit();conn.close()

def get_all_settings():
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT key,value FROM settings")
    r=c.fetchall();conn.close();return {x["key"]:x["value"] for x in r}


# ── Students ───────────────────────────────────────────────────────────────────

def add_student(sid,name,encodings,class_name="",branch="",dob=""):
    if not encodings:return False
    try:
        conn=get_connection();c=conn.cursor()
        c.execute("INSERT INTO students (student_id,name,class_name,branch,dob,encoding) VALUES (?,?,?,?,?,?)",
                  (sid,name,class_name,branch,dob,pickle.dumps(encodings)))
        conn.commit();conn.close();return True
    except sqlite3.IntegrityError:return False
    except Exception as e:logger.error(e);return False

def update_student(sid,name,class_name,branch,dob):
    conn=get_connection();c=conn.cursor()
    c.execute("UPDATE students SET name=?,class_name=?,branch=?,dob=? WHERE student_id=?",
              (name,class_name,branch,dob,str(sid)))
    ok=c.rowcount>0;conn.commit();conn.close();return ok

def update_student_encoding(sid,encodings):
    conn=get_connection();c=conn.cursor()
    c.execute("UPDATE students SET encoding=? WHERE student_id=?",
              (pickle.dumps(encodings),str(sid)))
    ok=c.rowcount>0;conn.commit();conn.close();return ok

def get_all_students():
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT student_id,name,class_name,branch,dob,encoding FROM students")
    rows=c.fetchall();conn.close()
    result=[]
    for row in rows:
        enc=row["encoding"]
        encodings=pickle.loads(enc) if enc else []
        if isinstance(encodings,np.ndarray):encodings=[encodings]
        result.append({"student_id":row["student_id"],"name":row["name"],
                       "class_name":row["class_name"],"branch":row["branch"],
                       "dob":row["dob"],"encoding":encodings})
    return result

def get_all_students_info():
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT student_id,name,class_name,branch,dob,registered_at FROM students ORDER BY name")
    r=c.fetchall();conn.close();return [dict(x) for x in r]

def get_student_by_id(sid):
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT student_id,name,class_name,branch,dob,registered_at FROM students WHERE student_id=?",
              (str(sid),))
    r=c.fetchone();conn.close();return dict(r) if r else None

def student_exists(sid):
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT 1 FROM students WHERE student_id=?",(sid,))
    r=c.fetchone();conn.close();return r is not None

def verify_student(sid,dob):
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT 1 FROM students WHERE student_id=? AND dob=?",(str(sid),dob))
    r=c.fetchone();conn.close();return r is not None

def delete_student(sid):
    sid=str(sid)
    conn=None
    try:
        conn=get_connection();c=conn.cursor()
        c.execute("DELETE FROM attendance WHERE student_id=?",(sid,))
        c.execute("DELETE FROM students WHERE student_id=?",(sid,))
        if c.rowcount>0:conn.commit();return True
        conn.rollback();return False
    except Exception as e:
        if conn:conn.rollback()
        logger.error(e);return False
    finally:
        if conn:conn.close()

def get_all_classes():
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT DISTINCT class_name FROM students WHERE class_name!='' ORDER BY class_name")
    r=c.fetchall();conn.close();return [x["class_name"] for x in r]


# ── Attendance ─────────────────────────────────────────────────────────────────

def insert_attendance(sid, name, date, time_str, slot,
                      marked_by="auto", status="Present") -> bool:
    """Insert one attendance record. Returns False if already exists."""
    sid = str(sid)
    try:
        conn=get_connection();c=conn.cursor()
        c.execute("SELECT 1 FROM attendance WHERE student_id=? AND date=? AND slot=?",
                  (sid,date,slot))
        if c.fetchone():conn.close();return False
        c.execute("INSERT INTO attendance (student_id,name,date,time,slot,status,marked_by) VALUES (?,?,?,?,?,?,?)",
                  (sid,name,date,time_str,slot,status,marked_by))
        conn.commit();conn.close();return True
    except sqlite3.IntegrityError:return False
    except Exception as e:logger.error(e);return False


def mark_absent_for_slot(date_str: str, slot_label: str) -> int:
    """
    Auto-mark Absent for every student NOT marked Present in given slot.
    Called automatically at end of each slot.
    Returns count of students marked absent.
    """
    students = get_all_students_info()
    now_str  = f"{slot_label.split('-')[1]}:00"  # slot end time
    count    = 0
    for s in students:
        sid  = s["student_id"]
        name = s["name"]
        # Check if already marked (Present or Absent)
        conn=get_connection();c=conn.cursor()
        c.execute("SELECT 1 FROM attendance WHERE student_id=? AND date=? AND slot=?",
                  (sid, date_str, slot_label))
        exists = c.fetchone(); conn.close()
        if not exists:
            saved = insert_attendance(sid, name, date_str, now_str,
                                      slot_label, "auto-absent", "Absent")
            if saved:
                count += 1
                logger.info(f"Auto-absent: {name} ({sid}) slot={slot_label}")
    logger.info(f"Auto-absent complete: {count} students marked absent for {slot_label} on {date_str}")
    return count


def get_attendance_by_date(query_date) -> list:
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT student_id,name,date,time,slot,status,marked_by FROM attendance WHERE date=? ORDER BY slot,name",
              (query_date,))
    r=c.fetchall();conn.close();return [dict(x) for x in r]


def get_all_attendance() -> list:
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT student_id,name,date,time,slot,status,marked_by FROM attendance ORDER BY date DESC,slot,name")
    r=c.fetchall();conn.close();return [dict(x) for x in r]


def get_student_attendance(sid) -> list:
    """Return all attendance records for student, ordered by date and slot."""
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT date,time,slot,status,marked_by FROM attendance WHERE student_id=? ORDER BY date DESC,slot",
              (str(sid),))
    r=c.fetchall();conn.close();return [dict(x) for x in r]


def get_student_daily_attendance(sid) -> list:
    """
    Return per-day attendance showing ALL slots for each day.
    - Past slots with no record → shown as Absent
    - Today's future slots → shown as Pending
    - Present/Absent → shown as recorded
    """
    from datetime import datetime
    records   = get_student_attendance(sid)
    all_slots = get_all_slot_labels()
    today_str = datetime.now().strftime("%Y-%m-%d")
    now_time  = datetime.now().strftime("%H:%M")

    # Group existing records by date → slot → status
    by_date = {}
    for rec in records:
        d = rec["date"]
        if d not in by_date:
            by_date[d] = {}
        by_date[d][rec["slot"]] = rec["status"]

    # Get all unique dates that have at least one record
    all_dates = sorted(by_date.keys(), reverse=True)

    # Also include today even if no records yet
    if today_str not in all_dates:
        all_dates = [today_str] + all_dates

    result = []
    for d in all_dates:
        slots_for_day = []
        present = 0
        absent  = 0

        for slot_label in all_slots:
            # Determine slot end time
            slot_end = slot_label.split("-")[1]  # e.g. "10:00"

            if d in by_date and slot_label in by_date[d]:
                # Has a record
                status = by_date[d][slot_label]
            elif d < today_str:
                # Past day, no record → Absent
                status = "Absent"
            elif d == today_str and slot_end <= now_time:
                # Today, slot already ended, no record → Absent
                status = "Absent"
            else:
                # Today's future/current slot → Pending
                status = "Pending"

            slots_for_day.append({"slot": slot_label, "status": status})

            if status == "Present":
                present += 1
            elif status == "Absent":
                absent += 1

        total = present + absent  # pending slots not counted in total
        result.append({
            "date":    d,
            "slots":   slots_for_day,
            "present": present,
            "absent":  absent,
            "total":   total,
        })

    return result


def get_student_summary(sid) -> dict:
    """
    Overall attendance summary.
    Uses same logic as get_student_daily_attendance() so stats
    match exactly what the student sees in the day-wise view.

    - Present: camera detected (DB record status=Present)
    - Absent:  DB record status=Absent OR past slot with no record
    - Pending: today's future slots (not counted in % denominator)
    - percentage: Present / (Present + Absent) * 100
    """
    from datetime import datetime

    all_slots = get_all_slot_labels()
    today_str = datetime.now().strftime("%Y-%m-%d")
    now_time  = datetime.now().strftime("%H:%M")

    # Get all DB records for this student
    db_records = get_student_attendance(sid)

    # Build lookup: {date: {slot: status}}
    by_date = {}
    for rec in db_records:
        d = rec["date"]
        if d not in by_date:
            by_date[d] = {}
        by_date[d][rec["slot"]] = rec["status"]

    # All dates that have at least one record + today
    all_dates = set(by_date.keys())
    all_dates.add(today_str)

    total_present = 0
    total_absent  = 0
    monthly       = {}

    for d in all_dates:
        month = d[:7]
        if month not in monthly:
            monthly[month] = {"present": 0, "absent": 0}

        for slot_label in all_slots:
            slot_end = slot_label.split("-")[1]

            if d in by_date and slot_label in by_date[d]:
                status = by_date[d][slot_label]
            elif d < today_str:
                status = "Absent"   # past day, no record → Absent
            elif d == today_str and slot_end <= now_time:
                status = "Absent"   # today's ended slot, no record → Absent
            else:
                status = "Pending"  # today's future slot → not counted

            if status == "Present":
                total_present += 1
                monthly[month]["present"] += 1
            elif status == "Absent":
                total_absent += 1
                monthly[month]["absent"] += 1
            # Pending → skip

    total_slots  = total_present + total_absent
    pct          = round(total_present / total_slots * 100, 1) if total_slots else 0.0

    # Monthly percentages
    for m in monthly:
        p = monthly[m]["present"]
        a = monthly[m]["absent"]
        t = p + a
        monthly[m]["total"] = t
        monthly[m]["pct"]   = round(p / t * 100, 1) if t else 0.0

    return {
        "total":      total_slots,
        "present":    total_present,
        "absent":     total_absent,
        "percentage": pct,
        "monthly":    monthly,
    }


def get_all_students_summary() -> list:
    """Summary for all students — used by teacher dashboard."""
    students = get_all_students_info()
    result   = []
    for s in students:
        summary = get_student_summary(s["student_id"])
        result.append({**s, **summary})
    return result


def manual_mark_attendance(sid, date, slot, marked_by="teacher") -> bool:
    student = get_student_by_id(sid)
    if not student: return False
    from datetime import datetime
    time_str = datetime.now().strftime("%H:%M:%S")
    # If already Absent, update to Present
    conn=get_connection();c=conn.cursor()
    c.execute("SELECT status FROM attendance WHERE student_id=? AND date=? AND slot=?",
              (str(sid), date, slot))
    existing = c.fetchone(); conn.close()
    if existing:
        if existing["status"] == "Absent":
            conn=get_connection();c=conn.cursor()
            c.execute("UPDATE attendance SET status='Present', marked_by=?, time=? WHERE student_id=? AND date=? AND slot=?",
                      (marked_by, time_str, str(sid), date, slot))
            conn.commit();conn.close()
            return True
        return False  # already Present
    return insert_attendance(sid, student["name"], date, time_str,
                             slot, marked_by, "Present")


def remove_attendance(sid, date, slot) -> bool:
    """Remove a Present mark — sets back to Absent."""
    conn=get_connection();c=conn.cursor()
    c.execute("UPDATE attendance SET status='Absent', marked_by='teacher-removed' WHERE student_id=? AND date=? AND slot=?",
              (str(sid), date, slot))
    ok=c.rowcount>0;conn.commit();conn.close();return ok
"""
attendance.py - Slot-based attendance logic.
Status is always Present. No Late concept.
"""

import os, csv, logging
from datetime import datetime, date, timedelta
import database

logger = logging.getLogger(__name__)
ATTENDANCE_DIR = "attendance_records"

_cooldown_tracker: dict = {}

def _ensure_dir(): os.makedirs(ATTENDANCE_DIR, exist_ok=True)

def _get_cooldown() -> int:
    return int(database.get_setting("cooldown_minutes", "50"))

def _is_on_cooldown(student_id, slot_label) -> bool:
    last = _cooldown_tracker.get((student_id, slot_label))
    if last is None: return False
    return (datetime.now() - last) < timedelta(minutes=_get_cooldown())

def _set_cooldown(student_id, slot_label):
    _cooldown_tracker[(student_id, slot_label)] = datetime.now()

def reset_cooldowns():
    _cooldown_tracker.clear()

def get_active_session(now=None):
    slot = database.get_current_slot(now)
    return (slot["label"], "Present") if slot else (None, None)

def mark_attendance(student_id, name) -> tuple[bool, str]:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%H:%M:%S")
    slot = database.get_current_slot(now)
    if slot is None:
        return False, f"No active slot at {now_str}."
    slot_label = slot["label"]
    if _is_on_cooldown(student_id, slot_label):
        return False, f"{name}: cooldown active."
    saved = database.insert_attendance(student_id, name, today, now_str, slot_label, "auto")
    _set_cooldown(student_id, slot_label)
    if not saved:
        return False, f"{name} already marked for slot {slot_label}."
    _write_csv(student_id, name, today, now_str, slot_label)
    msg = f"{name} marked Present — {slot_label} @ {now_str}"
    logger.info(msg)
    return True, msg

def _write_csv(student_id, name, att_date, att_time, slot):
    _ensure_dir()
    path = os.path.join(ATTENDANCE_DIR, f"attendance_{att_date}.csv")
    new_file = not os.path.isfile(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["Student ID","Name","Date","Time","Slot","Status"])
        w.writerow([student_id, name, att_date, att_time, slot, "Present"])

def get_today_attendance() -> list:
    return database.get_attendance_by_date(date.today().strftime("%Y-%m-%d"))

def get_attendance_by_date(query_date) -> list:
    return database.get_attendance_by_date(query_date)

def get_all_attendance() -> list:
    return database.get_all_attendance()

def get_slots() -> list:
    return database.get_slots()

def export_to_excel(output_path=None) -> str:
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        return ""
    records = get_all_attendance()
    if not records: return ""
    _ensure_dir()
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(ATTENDANCE_DIR, f"attendance_export_{ts}.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Attendance"
    headers = ["Student ID","Name","Date","Slot","Time","Status","Marked By"]
    hf = Font(bold=True, color="FFFFFF")
    hfill = PatternFill("solid", fgColor="0d3b66")
    alt   = PatternFill("solid", fgColor="e8f4fd")
    prs   = PatternFill("solid", fgColor="d4edda")
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = hf; cell.fill = hfill
        cell.alignment = Alignment(horizontal="center")
    for ri, rec in enumerate(records, 2):
        vals = [rec["student_id"], rec["name"], rec["date"],
                rec.get("slot",""), rec["time"],
                rec.get("status","Present"), rec.get("marked_by","auto")]
        for ci, v in enumerate(vals, 1):
            cell = ws.cell(row=ri, column=ci, value=v)
            cell.alignment = Alignment(horizontal="center")
            if ri % 2 == 0: cell.fill = alt
        ws.cell(row=ri, column=6).fill = prs
    for col in ws.columns:
        w = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = w + 4
    wb.save(output_path)
    return output_path
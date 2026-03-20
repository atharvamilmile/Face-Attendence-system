"""
attendance.py - Slot-based attendance logic for FaceTrack.

Rules:
  - Working hours: 09:00 – 17:00 (configurable in Settings)
  - Slots are 1-hour blocks: 09:00-10:00, 10:00-11:00 … 16:00-17:00
  - Break (default 13:00-14:00) is excluded — no slot, no attendance
  - Status is always "Present" — no Late concept
  - One mark per student per slot per day — duplicate silently ignored
  - Cooldown: 50 min in-memory guard (just under slot duration) to stop
    the camera re-marking someone who lingers after being recorded
"""

import os
import csv
import logging
from datetime import datetime, date, timedelta

import database

logger = logging.getLogger(__name__)
ATTENDANCE_DIR = "attendance_records"


def _ensure_dir():
    os.makedirs(ATTENDANCE_DIR, exist_ok=True)


# ── Cooldown (in-memory) ───────────────────────────────────────────────────────
# Key: (student_id, slot_label)  Value: datetime of last mark
_cooldown_tracker: dict = {}


def _get_cooldown_minutes() -> int:
    return int(database.get_setting("cooldown_minutes", "50"))


def _is_on_cooldown(student_id: str, slot_label: str) -> bool:
    key  = (student_id, slot_label)
    last = _cooldown_tracker.get(key)
    if last is None:
        return False
    return (datetime.now() - last) < timedelta(minutes=_get_cooldown_minutes())


def _set_cooldown(student_id: str, slot_label: str):
    _cooldown_tracker[(student_id, slot_label)] = datetime.now()


def reset_cooldowns():
    """Clear all cooldown entries (call at midnight or manually)."""
    _cooldown_tracker.clear()
    logger.info("Cooldown tracker reset.")


# ── Active slot ────────────────────────────────────────────────────────────────

def get_active_session(now: datetime = None):
    """
    Return (slot_label, "Present") if a slot is active right now,
    or (None, None) if it's outside working hours or during break.

    Named 'get_active_session' for backward compatibility with main.py.
    """
    slot = database.get_current_slot(now)
    if slot:
        return slot["label"], "Present"
    return None, None


# ── Mark attendance ────────────────────────────────────────────────────────────

def mark_attendance(student_id: str, name: str) -> tuple[bool, str]:
    """
    Attempt to mark attendance for student in the currently active slot.

    Returns (True, message) on success.
    Returns (False, reason) if outside slot, on cooldown, or duplicate.
    """
    now     = datetime.now()
    today   = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%H:%M:%S")

    slot = database.get_current_slot(now)
    if slot is None:
        return False, f"No active slot at {now_str}."

    slot_label = slot["label"]

    # Cooldown guard — prevents camera spam within same slot
    if _is_on_cooldown(student_id, slot_label):
        return False, f"{name}: cooldown active for slot {slot_label}."

    saved = database.insert_attendance(student_id, name, today, now_str, slot_label)
    _set_cooldown(student_id, slot_label)

    if not saved:
        return False, f"{name} already marked for slot {slot_label} today."

    _write_csv(student_id, name, today, now_str, slot_label)
    msg = f"{name} marked Present — slot {slot_label} @ {now_str}"
    logger.info(msg)
    return True, msg


# ── CSV helper ─────────────────────────────────────────────────────────────────

def _write_csv(student_id, name, att_date, att_time, slot):
    _ensure_dir()
    path     = os.path.join(ATTENDANCE_DIR, f"attendance_{att_date}.csv")
    new_file = not os.path.isfile(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["Student ID", "Name", "Date", "Time", "Slot", "Status"])
        w.writerow([student_id, name, att_date, att_time, slot, "Present"])


# ── Queries ────────────────────────────────────────────────────────────────────

def get_today_attendance() -> list:
    return database.get_attendance_by_date(date.today().strftime("%Y-%m-%d"))


def get_attendance_by_date(query_date: str) -> list:
    return database.get_attendance_by_date(query_date)


def get_all_attendance() -> list:
    return database.get_all_attendance()


def get_slots() -> list:
    """Return list of all slot dicts for the day (break excluded)."""
    return database.get_slots()


# ── Excel export ───────────────────────────────────────────────────────────────

def export_to_excel(output_path: str = None) -> str:
    """
    Export all attendance to a styled .xlsx file.
    Columns: Student ID | Name | Date | Slot | Time | Status
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        logger.error("openpyxl not installed. Run: pip install openpyxl")
        return ""

    records = get_all_attendance()
    if not records:
        logger.warning("No records to export.")
        return ""

    _ensure_dir()
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(ATTENDANCE_DIR, f"attendance_export_{ts}.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance"

    headers  = ["Student ID", "Name", "Date", "Slot", "Time", "Status"]
    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="0d3b66")
    alt_fill = PatternFill("solid", fgColor="e8f4fd")
    prs_fill = PatternFill("solid", fgColor="d4edda")

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center")

    for ri, rec in enumerate(records, 2):
        vals = [rec["student_id"], rec["name"], rec["date"],
                rec.get("slot", ""), rec["time"], rec.get("status", "Present")]
        for ci, v in enumerate(vals, 1):
            cell = ws.cell(row=ri, column=ci, value=v)
            cell.alignment = Alignment(horizontal="center")
            if ri % 2 == 0:
                cell.fill = alt_fill
        # Green fill for status cell
        ws.cell(row=ri, column=6).fill = prs_fill

    for col in ws.columns:
        w = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = w + 4

    wb.save(output_path)
    logger.info(f"Exported to: {output_path}")
    return output_path
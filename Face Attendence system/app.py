"""
app.py - Flask Web Server for FaceTrack Student Portal.
Run: python app.py  → http://localhost:5000

Routes:
  /          → redirect to login
  /login     → student login (ID + DOB)
  /logout    → clear session
  /dashboard → student attendance dashboard (slot-based, no Late)
"""

from flask import (Flask, render_template, request,
                   redirect, url_for, session, jsonify)
from datetime import date
import database
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)   # set a fixed string in production


# ── Auth decorator ─────────────────────────────────────────────────────────────

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "student_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        sid = request.form.get("student_id", "").strip().upper()
        dob = request.form.get("dob", "").strip()
        if database.verify_student(sid, dob):
            session["student_id"] = sid
            return redirect(url_for("dashboard"))
        error = "Invalid Student ID or Date of Birth."
    return render_template("student/login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    sid     = session["student_id"]
    student = database.get_student_by_id(sid)
    if not student:
        session.clear()
        return redirect(url_for("login"))

    records = database.get_student_attendance(sid)
    summary = database.get_student_summary(sid)

    # All defined slots for the day (break excluded)
    all_slots = [s["label"] for s in database.get_slots()]

    # Slots the student attended today
    today_str   = date.today().strftime("%Y-%m-%d")
    today_recs  = [r for r in records if r["date"] == today_str]
    today_slots = {r.get("slot", r.get("session", "")) for r in today_recs}

    # Month list for filter dropdown
    months = sorted({r["date"][:7] for r in records}, reverse=True)

    return render_template(
        "student/dashboard.html",
        student    = student,
        records    = records,
        summary    = summary,
        all_slots  = all_slots,
        today_slots= today_slots,
        months     = months,
    )


@app.route("/api/attendance")
@login_required
def api_attendance():
    """JSON endpoint for AJAX filtering."""
    sid     = session["student_id"]
    month   = request.args.get("month", "")
    slot    = request.args.get("slot",  "")
    records = database.get_student_attendance(sid)
    if month:
        records = [r for r in records if r["date"].startswith(month)]
    if slot:
        records = [r for r in records
                   if r.get("slot", r.get("session", "")) == slot]
    return jsonify(records)


if __name__ == "__main__":
    database.initialize_database()
    app.run(debug=True, port=5000)
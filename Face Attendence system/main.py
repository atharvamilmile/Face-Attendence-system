"""
main.py - FaceTrack Attendance System Desktop App
Admin Panel added with: Login, Student Management, Settings
"""

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
from PIL import Image, ImageTk
import threading
import logging
import os
from datetime import datetime

import database
import face_utils
import attendance as att

os.makedirs("data", exist_ok=True)
os.makedirs("attendance_records", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("data/app.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ── Design tokens ──────────────────────────────────────────────────────────────
C = {
    "bg":          "#080d1a", "surface":     "#0d1526",
    "glass":       "#111d35", "glass2":      "#162240",
    "border":      "#1e3258", "border_hi":   "#2a4a7f",
    "accent":      "#00d4ff", "accent2":     "#0099cc",
    "accent3":     "#ff4d6d", "accent4":     "#00ff9d",
    "accent5":     "#f0b429", "purple":      "#7c3aed",
    "text":        "#e8f4f8", "text2":       "#8baac4",
    "text3":       "#4a6885", "sidebar":     "#0a1220",
    "sidebar_sel": "#0d1f3c", "row_alt":     "#0f1e38",
    "present":     "#00ff9d", "late":        "#f0b429",
    "spoof":       "#ff6b35",
}
FONT = {
    "display": ("Segoe UI", 24, "bold"), "title":   ("Segoe UI", 16, "bold"),
    "heading": ("Segoe UI", 12, "bold"), "body":    ("Segoe UI", 11),
    "small":   ("Segoe UI", 9),          "mono":    ("Consolas", 10),
    "nav":     ("Segoe UI", 11, "bold"), "counter": ("Segoe UI", 32, "bold"),
}
NAV_ITEMS = [
    ("live",     "▶", "Live Attendance"),
    ("register", "+", "Register Student"),
    ("records",  "≡", "Attendance Records"),
    ("students", "⊞", "Manage Students"),
    ("admin",    "⚙", "Admin Panel"),
]


# ── Reusable widgets ───────────────────────────────────────────────────────────

class GlassFrame(tk.Frame):
    def __init__(self, parent, **kw):
        bg = kw.pop("bg", C["glass"])
        super().__init__(parent, bg=bg, **kw)

class Divider(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C["border"], height=1, **kw)

class AnimatedButton(tk.Button):
    def __init__(self, parent, text, command=None,
                 normal_bg=None, hover_bg=None, fg=None, **kw):
        self._nbg = normal_bg or C["glass2"]
        self._hbg = hover_bg  or C["accent2"]
        self._fg  = fg        or C["text"]
        kw.setdefault("font", FONT["body"]); kw.setdefault("padx", 12)
        kw.setdefault("pady", 6);            kw.setdefault("cursor", "hand2")
        super().__init__(parent, text=text, command=command,
                         bg=self._nbg, fg=self._fg,
                         activebackground=self._hbg, activeforeground="white",
                         relief="flat", borderwidth=0, **kw)
        self.bind("<Enter>", lambda _: self.config(bg=self._hbg, fg="white"))
        self.bind("<Leave>", lambda _: self.config(bg=self._nbg, fg=self._fg))

class PrimaryButton(AnimatedButton):
    def __init__(self, parent, text, command=None, **kw):
        kw.setdefault("font", FONT["heading"]); kw.setdefault("padx", 20); kw.setdefault("pady", 8)
        super().__init__(parent, text=text, command=command,
                         normal_bg=C["accent2"], hover_bg=C["accent"], fg="white", **kw)

class DangerButton(tk.Button):
    def __init__(self, parent, text, command=None, **kw):
        kw.setdefault("font", FONT["heading"]); kw.setdefault("padx", 20)
        kw.setdefault("pady", 8);               kw.setdefault("cursor", "hand2")
        super().__init__(parent, text=text, command=command,
                         bg="#3d1520", fg=C["accent3"],
                         activebackground=C["accent3"], activeforeground="white",
                         relief="flat", borderwidth=0, **kw)
        self.bind("<Enter>", lambda _: self.config(bg=C["accent3"], fg="white"))
        self.bind("<Leave>", lambda _: self.config(bg="#3d1520",    fg=C["accent3"]))

class ModernEntry(tk.Frame):
    def __init__(self, parent, label, var, width=28, show=None, **kw):
        super().__init__(parent, bg=C["glass"], **kw)
        tk.Label(self, text=label, font=FONT["small"],
                 bg=C["glass"], fg=C["text2"]).pack(anchor="w", pady=(0,2))
        inner = tk.Frame(self, bg=C["glass2"])
        inner.pack(fill="x")
        opts = dict(textvariable=var, font=FONT["body"], bg=C["glass2"], fg=C["text"],
                    insertbackground=C["accent"], relief="flat", bd=8, width=width)
        if show: opts["show"] = show
        self.entry = tk.Entry(inner, **opts)
        self.entry.pack(fill="x")
        self._bar = tk.Frame(self, bg=C["border"], height=2)
        self._bar.pack(fill="x")
        self.entry.bind("<FocusIn>",  lambda _: self._bar.config(bg=C["accent"]))
        self.entry.bind("<FocusOut>", lambda _: self._bar.config(bg=C["border"]))

class StatCard(tk.Frame):
    def __init__(self, parent, label, value="0", color=None, **kw):
        color = color or C["accent"]
        super().__init__(parent, bg=C["glass2"], padx=24, pady=16, **kw)
        self._var = tk.StringVar(value=str(value))
        tk.Label(self, textvariable=self._var, font=FONT["counter"],
                 bg=C["glass2"], fg=color).pack()
        tk.Label(self, text=label, font=FONT["small"],
                 bg=C["glass2"], fg=C["text2"]).pack()
    def set(self, v): self._var.set(str(v))


def _apply_tree_style(name, row_height=32):
    s = ttk.Style()
    s.configure(f"{name}.Treeview",
                background=C["surface"], foreground=C["text"],
                rowheight=row_height, fieldbackground=C["surface"],
                font=FONT["body"], borderwidth=0)
    s.configure(f"{name}.Treeview.Heading",
                background=C["glass2"], foreground=C["accent"],
                font=FONT["heading"], relief="flat")
    s.map(f"{name}.Treeview",
          background=[("selected", C["glass2"])],
          foreground=[("selected", C["accent"])])

def _make_tree(parent, cols: dict, style_name: str):
    frame = tk.Frame(parent, bg=C["bg"])
    frame.pack(fill="both", expand=True, padx=20, pady=(0,16))
    vsb = ttk.Scrollbar(frame, orient="vertical")
    vsb.pack(side="right", fill="y")
    tree = ttk.Treeview(frame, columns=list(cols.keys()),
                        show="headings", yscrollcommand=vsb.set,
                        style=f"{style_name}.Treeview")
    vsb.config(command=tree.yview)
    for col, (label, width) in cols.items():
        tree.heading(col, text=label)
        tree.column(col, width=width, anchor="center")
    tree.tag_configure("alt",     background=C["row_alt"])
    tree.tag_configure("present", foreground=C["present"])
    tree.tag_configure("late",    foreground=C["late"])
    tree.pack(fill="both", expand=True)
    return tree


# ── Admin Login Dialog ─────────────────────────────────────────────────────────

class AdminLoginDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Admin Login")
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        self.grab_set()
        self.result = False

        self.geometry("360x280")
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width()  - 360) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 280) // 2
        self.geometry(f"+{x}+{y}")

        card = GlassFrame(self, padx=32, pady=28)
        card.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(card, text="🔐  Admin Login", font=FONT["title"],
                 bg=C["glass"], fg=C["accent"]).pack(pady=(0, 20))

        self._user = tk.StringVar()
        self._pass = tk.StringVar()
        ModernEntry(card, "USERNAME", self._user, width=24).pack(fill="x", pady=6)
        ModernEntry(card, "PASSWORD", self._pass, width=24, show="●").pack(fill="x", pady=6)

        self._msg = tk.Label(card, text="", font=FONT["small"],
                             bg=C["glass"], fg=C["accent3"])
        self._msg.pack(pady=(8, 0))

        PrimaryButton(card, "Login", self._login).pack(pady=(14, 0))
        self.bind("<Return>", lambda _: self._login())

    def _login(self):
        u = self._user.get().strip()
        p = self._pass.get().strip()
        if database.verify_admin(u, p):
            self.result = True
            self.destroy()
        else:
            self._msg.config(text="Invalid username or password.")
            self._pass.set("")


# ── Main App ───────────────────────────────────────────────────────────────────

class FaceAttendanceApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("FaceTrack — Attendance System")
        self.geometry("1320x820")
        self.minsize(1100, 700)
        self.configure(bg=C["bg"])

        database.initialize_database()

        self.cap = None
        self.camera_running  = False
        self.known_students  = []
        self.marked_today: set = set()
        self._current_page   = "live"
        self._admin_logged_in = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick_clock()
        self._show_page("live")

    def refresh_known_students(self):
        self.known_students = database.get_all_students()
        logger.info(f"Refreshed: {len(self.known_students)} students.")

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        sb = tk.Frame(self, bg=C["sidebar"], width=230)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)
        self._sidebar_frame = sb

        right = tk.Frame(self, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        self._build_sidebar()
        self._build_topbar(right)

        self._content = tk.Frame(right, bg=C["bg"])
        self._content.pack(fill="both", expand=True)

        self._pages = {key: tk.Frame(self._content, bg=C["bg"])
                       for key, _, _ in NAV_ITEMS}

        self._build_live_page()
        self._build_register_page()
        self._build_records_page()
        self._build_students_page()
        self._build_admin_page()

    # ── Sidebar ────────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = self._sidebar_frame
        logo = tk.Frame(sb, bg=C["sidebar"], pady=28)
        logo.pack(fill="x")
        tk.Label(logo, text="🎓", font=("Segoe UI", 30),
                 bg=C["sidebar"], fg=C["accent"]).pack()
        tk.Label(logo, text="FaceTrack", font=("Segoe UI", 15, "bold"),
                 bg=C["sidebar"], fg=C["text"]).pack()
        tk.Label(logo, text="Attendance System", font=FONT["small"],
                 bg=C["sidebar"], fg=C["text3"]).pack()
        Divider(sb).pack(fill="x", padx=18, pady=(0, 10))
        self._nav_btns = {key: self._make_nav_item(sb, key, icon, label)
                          for key, icon, label in NAV_ITEMS}
        Divider(sb).pack(fill="x", padx=18, pady=16)
        self.clock_lbl = tk.Label(sb, text="", font=FONT["small"],
                                  bg=C["sidebar"], fg=C["text3"])
        self.clock_lbl.pack(side="bottom", pady=14)
        tk.Label(sb, text="v2.1  Pro", font=FONT["small"],
                 bg=C["sidebar"], fg=C["text3"]).pack(side="bottom")

    def _make_nav_item(self, parent, key, icon, label):
        row = tk.Frame(parent, bg=C["sidebar"], cursor="hand2")
        row.pack(fill="x", padx=10, pady=2)
        bar = tk.Frame(row, bg=C["sidebar"], width=4)
        bar.pack(side="left", fill="y")
        ic  = tk.Label(row, text=icon, font=("Segoe UI", 13),
                       bg=C["sidebar"], fg=C["text2"], width=3)
        ic.pack(side="left", padx=6, pady=12)
        lbl = tk.Label(row, text=label, font=FONT["nav"],
                       bg=C["sidebar"], fg=C["text2"], anchor="w")
        lbl.pack(side="left", fill="x", expand=True)
        row._bar = bar; row._ic = ic; row._lbl = lbl

        def on_click(_=None):
            if key == "admin" and not self._admin_logged_in:
                dlg = AdminLoginDialog(self)
                self.wait_window(dlg)
                if dlg.result:
                    self._admin_logged_in = True
                    self._show_page("admin")
                return
            self._show_page(key)

        def on_enter(_):
            if self._current_page != key:
                for w in (row, ic, lbl, bar): w.config(bg=C["glass"])
        def on_leave(_):
            if self._current_page != key:
                for w in (row, ic, lbl, bar): w.config(bg=C["sidebar"])

        for w in (row, bar, ic, lbl):
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
        return row

    def _update_nav(self, active):
        for key, row in self._nav_btns.items():
            if key == active:
                for w in (row, row._ic, row._lbl, row._bar): w.config(bg=C["sidebar_sel"])
                row._ic.config(fg=C["accent"]); row._lbl.config(fg=C["text"])
                row._bar.config(bg=C["accent"])
            else:
                for w in (row, row._ic, row._lbl, row._bar): w.config(bg=C["sidebar"])
                row._ic.config(fg=C["text2"]); row._lbl.config(fg=C["text2"])

    # ── Topbar ─────────────────────────────────────────────────────────────────

    def _build_topbar(self, parent):
        bar = tk.Frame(parent, bg=C["surface"], height=54)
        bar.pack(fill="x"); bar.pack_propagate(False)
        self._page_title = tk.Label(bar, text="", font=FONT["title"],
                                    bg=C["surface"], fg=C["text"])
        self._page_title.pack(side="left", padx=24)
        self._status_dot = tk.Label(bar, text="●", font=("Segoe UI", 10),
                                    bg=C["surface"], fg=C["text3"])
        self._status_dot.pack(side="left", padx=(0, 5))
        self._status_txt = tk.Label(bar, text="Ready", font=FONT["small"],
                                    bg=C["surface"], fg=C["text3"])
        self._status_txt.pack(side="left")
        self._session_badge = tk.Label(bar, text="", font=FONT["small"],
                                       bg=C["glass2"], fg=C["accent5"], padx=10, pady=3)
        self._session_badge.pack(side="left", padx=16)
        self._date_lbl = tk.Label(bar, text="", font=FONT["small"],
                                  bg=C["surface"], fg=C["text2"])
        self._date_lbl.pack(side="right", padx=24)

    def _tick_clock(self):
        now = datetime.now()
        self.clock_lbl.config(text=now.strftime("%H:%M:%S"))
        self._date_lbl.config(text=now.strftime("%a, %d %b %Y  |  %H:%M"))
        session, status = att.get_active_session(now)
        if session:
            self._session_badge.config(text=f"  ● Slot: {session}  ",
                                       fg=C["present"], bg=C["glass2"])
        else:
            self._session_badge.config(text="  ○ No Active Slot  ",
                                       fg=C["text3"], bg=C["glass2"])
        self.after(1000, self._tick_clock)

    def _set_status(self, text, color=None):
        c = color or C["text3"]
        self._status_dot.config(fg=c); self._status_txt.config(text=text, fg=c)

    def _show_page(self, key):
        self._current_page = key
        self._update_nav(key)
        for f in self._pages.values(): f.pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        titles = {"live": "Live Attendance", "register": "Register Student",
                  "records": "Attendance Records", "students": "Manage Students",
                  "admin": "Admin Panel"}
        self._page_title.config(text=titles.get(key, ""))
        if key == "records":  self._load_all_records()
        if key == "students": self._load_students_list()
        if key == "admin":    self._refresh_admin_students()

    # ── Live page ───────────────────────────────────────────────────────────────

    def _build_live_page(self):
        page = self._pages["live"]
        stats_row = tk.Frame(page, bg=C["bg"], pady=14)
        stats_row.pack(fill="x", padx=20)
        self.stat_today   = StatCard(stats_row, "Marked Today",   "0",  C["accent"])
        self.stat_total   = StatCard(stats_row, "Total Students", "0",  C["accent4"])
        self.stat_rate    = StatCard(stats_row, "Rate Today",     "0%", C["accent5"])
        self.stat_present = StatCard(stats_row, "Present",        "0",  C["present"])
        self.stat_late    = StatCard(stats_row, "Active Slot",    "—",  C["accent2"])
        for w in (self.stat_today, self.stat_total, self.stat_rate,
                  self.stat_present, self.stat_late):
            w.pack(side="left", padx=(0, 12))

        body = tk.Frame(page, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        cam_card = GlassFrame(body, padx=10, pady=10)
        cam_card.pack(side="left", fill="both", expand=True)
        cam_border = tk.Frame(cam_card, bg=C["accent"], padx=2, pady=2)
        cam_border.pack(fill="both", expand=True, pady=(0, 10))
        self.video_canvas = tk.Canvas(cam_border, bg="#000810", width=740, height=430)
        self.video_canvas.pack(fill="both", expand=True)
        self._draw_placeholder()

        ctrl = tk.Frame(cam_card, bg=C["glass"])
        ctrl.pack(fill="x")
        self.btn_start = PrimaryButton(ctrl, "▶  Start Camera", self._start_camera)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop = DangerButton(ctrl, "⏹  Stop", self._stop_camera)
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_stop.config(state="disabled")
        AnimatedButton(ctrl, "⟳  Refresh", command=self.refresh_known_students,
                       normal_bg=C["glass2"], hover_bg=C["purple"]).pack(side="left")
        self.cam_lbl = tk.Label(ctrl, text="● Offline", font=FONT["small"],
                                bg=C["glass"], fg=C["text3"])
        self.cam_lbl.pack(side="right", padx=10)

        log = GlassFrame(body, bg=C["glass"], width=280, padx=14, pady=14)
        log.pack(side="right", fill="y", padx=(12, 0))
        log.pack_propagate(False)
        tk.Label(log, text="TODAY'S LOG", font=FONT["small"],
                 bg=C["glass"], fg=C["accent"]).pack(anchor="w", pady=(0, 6))
        Divider(log).pack(fill="x", pady=(0, 8))
        lf = tk.Frame(log, bg=C["glass"]); lf.pack(fill="both", expand=True)
        sb2 = tk.Scrollbar(lf, bg=C["border"], troughcolor=C["glass"],
                           relief="flat", bd=0, width=5)
        sb2.pack(side="right", fill="y")
        self.today_lb = tk.Listbox(lf, bg=C["glass"], fg=C["text"],
                                   font=FONT["small"], relief="flat",
                                   selectbackground=C["glass2"],
                                   selectforeground=C["accent"],
                                   borderwidth=0, highlightthickness=0,
                                   yscrollcommand=sb2.set, activestyle="none")
        self.today_lb.pack(fill="both", expand=True)
        sb2.config(command=self.today_lb.yview)
        key_row = tk.Frame(log, bg=C["glass"]); key_row.pack(fill="x", pady=(6,0))
        tk.Label(key_row, text="● Present", font=FONT["small"],
                 bg=C["glass"], fg=C["present"]).pack(side="left", padx=(0, 8))
        self.live_msg = tk.Label(log, text="", font=FONT["small"],
                                 bg=C["glass"], fg=C["accent4"],
                                 wraplength=250, justify="left")
        self.live_msg.pack(pady=(6, 0), fill="x")
        self._refresh_today_list()
        self._refresh_stats()

    def _draw_placeholder(self):
        self.video_canvas.delete("all")
        w, h = 740, 430
        self.video_canvas.create_rectangle(0, 0, w, h, fill="#000810", outline="")
        for x in range(0, w, 55):
            self.video_canvas.create_line(x, 0, x, h, fill="#090f20", width=1)
        for y in range(0, h, 55):
            self.video_canvas.create_line(0, y, w, y, fill="#090f20", width=1)
        cx, cy = w//2, h//2
        self.video_canvas.create_oval(cx-56, cy-56, cx+56, cy+56,
                                      outline=C["border_hi"], width=2)
        self.video_canvas.create_text(cx, cy, text="📷",
                                      font=("Segoe UI", 34), fill=C["text3"])
        self.video_canvas.create_text(cx, cy+80, text="Click  ▶ Start Camera  to begin",
                                      font=FONT["body"], fill=C["text3"])

    def _start_camera(self):
        if self.camera_running: return
        session, _ = att.get_active_session()
        if session is None:
            if not messagebox.askyesno("No Active Slot",
                                       "No attendance slot is active right now.\n"
                                       "Faces will be detected but attendance won't be recorded.\n\n"
                                       "Start camera anyway?"):
                return
        self.cap = cv2.VideoCapture(
            int(database.get_setting("camera_index", "0"))
        )
        if not self.cap.isOpened():
            messagebox.showerror("Camera Error", "Cannot open webcam.")
            return
        self.camera_running = True
        self.btn_start.config(state="disabled"); self.btn_stop.config(state="normal")
        self.cam_lbl.config(text="● Live", fg=C["accent4"])
        self._set_status("Camera streaming", C["accent4"])
        self.refresh_known_students()
        threading.Thread(target=self._camera_loop, daemon=True).start()

    def _stop_camera(self):
        self.camera_running = False
        self.btn_start.config(state="normal"); self.btn_stop.config(state="disabled")
        self.cam_lbl.config(text="● Offline", fg=C["text3"])
        self._set_status("Camera stopped", C["text3"])
        self._draw_placeholder()

    def _camera_loop(self):
        fc = 0
        while self.camera_running:
            ret, frame = self.cap.read()
            if not ret: break
            fc += 1
            if fc % 3 == 0:
                results = face_utils.recognize_faces_in_frame(frame, self.known_students)
                face_utils.draw_face_annotations(frame, results)
                for r in results:
                    sid = r["student_id"]
                    if not sid: continue
                    ok, msg = att.mark_attendance(sid, r["name"])
                    if ok:
                        self.after(0, self._refresh_today_list)
                        self.after(0, self._refresh_stats)
                        self.after(0, lambda m=msg:
                                   self.live_msg.config(text=f"✓ {m}", fg=C["present"]))
            frame = cv2.flip(frame, 1)
            img   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img   = Image.fromarray(img).resize((740, 430), Image.LANCZOS)
            imgtk = ImageTk.PhotoImage(image=img)
            self.after(0, self._update_canvas, imgtk)
        if self.cap: self.cap.release()

    def _update_canvas(self, imgtk):
        self.video_canvas.imgtk = imgtk
        self.video_canvas.create_image(0, 0, anchor="nw", image=imgtk)

    def _refresh_today_list(self):
        records = att.get_today_attendance()
        self.today_lb.delete(0, tk.END)
        present_count = 0
        for rec in records:
            slot = rec.get("slot", rec.get("session", ""))
            self.today_lb.insert(tk.END,
                f"  ✓  {rec['name']}  [{slot}]  {rec['time']}")
            idx = self.today_lb.size() - 1
            self.today_lb.itemconfig(idx, fg=C["present"])
            present_count += 1
        self.stat_today.set(len(records))
        self.stat_present.set(present_count)
        # Show current active slot name
        slot = database.get_current_slot()
        self.stat_late.set(slot["label"] if slot else "—")

    def _refresh_stats(self):
        students = database.get_all_students_info()
        total = len(students); today = len(att.get_today_attendance())
        rate  = f"{int(today/total*100)}%" if total else "0%"
        self.stat_total.set(total); self.stat_today.set(today); self.stat_rate.set(rate)

    # ── Register page ───────────────────────────────────────────────────────────

    def _build_register_page(self):
        page = self._pages["register"]
        outer = tk.Frame(page, bg=C["bg"])
        outer.place(relx=0.5, rely=0.5, anchor="center")
        card = GlassFrame(outer, padx=52, pady=40); card.pack()
        tk.Label(card, text="Register New Student", font=FONT["display"],
                 bg=C["glass"], fg=C["text"]).pack(pady=(0, 4))
        tk.Label(card, text="Multi-angle capture + blink liveness check",
                 font=FONT["small"], bg=C["glass"], fg=C["text2"]).pack(pady=(0, 18))
        Divider(card).pack(fill="x", pady=(0, 18))

        form = tk.Frame(card, bg=C["glass"]); form.pack()
        self.reg_id_var     = tk.StringVar()
        self.reg_name_var   = tk.StringVar()
        self.reg_class_var  = tk.StringVar()
        self.reg_branch_var = tk.StringVar()
        self.reg_dob_var    = tk.StringVar()

        left_col = tk.Frame(form, bg=C["glass"]); left_col.pack(side="left", padx=(0,20))
        right_col = tk.Frame(form, bg=C["glass"]); right_col.pack(side="left")

        ModernEntry(left_col,  "STUDENT ID", self.reg_id_var,     width=26).pack(fill="x", pady=6)
        ModernEntry(left_col,  "FULL NAME",  self.reg_name_var,   width=26).pack(fill="x", pady=6)
        ModernEntry(left_col,  "CLASS",      self.reg_class_var,  width=26).pack(fill="x", pady=6)
        ModernEntry(right_col, "BRANCH",     self.reg_branch_var, width=26).pack(fill="x", pady=6)
        ModernEntry(right_col, "DATE OF BIRTH (YYYY-MM-DD)", self.reg_dob_var, width=26).pack(fill="x", pady=6)

        tips_row = tk.Frame(card, bg=C["glass2"], padx=18, pady=10)
        tips_row.pack(fill="x", pady=16)
        for icon, tip in [("👁","Blink first"),("➡","5 poses"),("🔒","Liveness check")]:
            col = tk.Frame(tips_row, bg=C["glass2"]); col.pack(side="left", padx=14)
            tk.Label(col, text=icon, font=("Segoe UI", 18), bg=C["glass2"],
                     fg=C["accent"]).pack()
            tk.Label(col, text=tip, font=FONT["small"], bg=C["glass2"],
                     fg=C["text2"]).pack()

        PrimaryButton(card, "📸   Capture & Register",
                      self._register_student).pack(pady=(0, 12))
        self.reg_status = tk.Label(card, text="", font=FONT["body"],
                                   bg=C["glass"], fg=C["accent4"])
        self.reg_status.pack()

    def _register_student(self):
        sid    = self.reg_id_var.get().strip().upper()
        name   = self.reg_name_var.get().strip().title()
        cls    = self.reg_class_var.get().strip()
        branch = self.reg_branch_var.get().strip()
        dob    = self.reg_dob_var.get().strip()

        if not sid or not name:
            messagebox.showwarning("Validation", "Student ID and Name are required.")
            return
        if not dob:
            messagebox.showwarning("Validation",
                "Date of Birth is required — it is the student's login password.")
            return
        if database.student_exists(sid):
            messagebox.showerror("Duplicate", f"ID '{sid}' already exists.")
            return

        self.reg_status.config(text="⏳  Opening camera for blink + pose capture...",
                               fg=C["accent5"])
        self.update()
        encoding = face_utils.capture_face_encoding(num_samples=5)
        if encoding is None:
            self.reg_status.config(text="✗  Capture cancelled.", fg=C["accent3"])
            return

        if database.add_student(sid, name, encoding, cls, branch, dob):
            self.reg_status.config(
                text=f"✓  {name} ({sid}) registered with {len(encoding)} encodings!",
                fg=C["accent4"])
            for v in (self.reg_id_var, self.reg_name_var, self.reg_class_var,
                      self.reg_branch_var, self.reg_dob_var):
                v.set("")
            self._refresh_stats()
        else:
            self.reg_status.config(text="✗  Failed. Check logs.", fg=C["accent3"])

    # ── Records page ────────────────────────────────────────────────────────────

    def _build_records_page(self):
        page = self._pages["records"]
        tb = tk.Frame(page, bg=C["bg"], pady=14); tb.pack(fill="x", padx=20)
        fg_frame = GlassFrame(tb, padx=12, pady=8); fg_frame.pack(side="left")

        tk.Label(fg_frame, text="DATE", font=FONT["small"],
                 bg=C["glass"], fg=C["text3"]).pack(side="left", padx=(0,8))
        self.filter_date_var = tk.StringVar()
        PH = "YYYY-MM-DD"
        de = tk.Entry(fg_frame, textvariable=self.filter_date_var,
                      font=FONT["mono"], bg=C["glass2"], fg=C["text"],
                      insertbackground=C["accent"], relief="flat", bd=6, width=14)
        de.pack(side="left", padx=(0,8)); de.insert(0, PH)
        de.bind("<FocusIn>",  lambda e: de.delete(0,"end") if de.get()==PH else None)
        de.bind("<FocusOut>", lambda e: (de.insert(0,PH) if not de.get() else None))

        tk.Label(fg_frame, text="SLOT", font=FONT["small"],
                 bg=C["glass"], fg=C["text3"]).pack(side="left", padx=(12,6))
        self.filter_slot_var = tk.StringVar(value="All")
        slot_values = ["All"] + [s["label"] for s in att.get_slots()]
        self._slot_cb = ttk.Combobox(fg_frame, textvariable=self.filter_slot_var,
                                      values=slot_values, width=14,
                                      state="readonly", font=FONT["small"])
        self._slot_cb.pack(side="left", padx=(0,8))

        tk.Label(fg_frame, text="CLASS", font=FONT["small"],
                 bg=C["glass"], fg=C["text3"]).pack(side="left", padx=(8,6))
        self.filter_class_var = tk.StringVar(value="All")
        self._class_cb = ttk.Combobox(fg_frame, textvariable=self.filter_class_var,
                                       values=["All"], width=10, state="readonly",
                                       font=FONT["small"])
        self._class_cb.pack(side="left", padx=(0,8))

        AnimatedButton(fg_frame, "🔍 Filter", command=self._filter_records,
                       normal_bg=C["glass2"], hover_bg=C["accent2"]).pack(side="left", padx=(0,6))
        AnimatedButton(fg_frame, "All", command=self._load_all_records,
                       normal_bg=C["glass2"], hover_bg=C["border_hi"],
                       fg=C["text2"]).pack(side="left")

        PrimaryButton(tb, "📊  Export Excel", self._export_excel).pack(side="right")
        self.rec_count_lbl = tk.Label(tb, text="", font=FONT["small"],
                                      bg=C["bg"], fg=C["text3"])
        self.rec_count_lbl.pack(side="right", padx=14)

        _apply_tree_style("R")
        self.records_tree = _make_tree(page, cols={
            "student_id": ("Student ID", 130), "name":    ("Name",    200),
            "date":       ("Date",       120),  "slot":    ("Slot",    160),
            "time":       ("Time",       100),  "class_name": ("Class", 90),
        }, style_name="R")

    def _populate_records_tree(self, records):
        students = {s["student_id"]: s.get("class_name","")
                    for s in database.get_all_students_info()}
        self.records_tree.delete(*self.records_tree.get_children())
        for i, r in enumerate(records):
            tags = ("alt",) if i % 2 else ()
            slot = r.get("slot", r.get("session", ""))
            cls  = students.get(r["student_id"], "")
            self.records_tree.insert("", "end", tags=tags,
                                     values=(r["student_id"], r["name"],
                                             r["date"], slot,
                                             r["time"], cls))
        self.rec_count_lbl.config(text=f"{len(records)} record(s)")

    def _load_all_records(self):
        self.filter_date_var.set("")
        self.filter_slot_var.set("All")
        self.filter_class_var.set("All")
        classes = ["All"] + database.get_all_classes()
        self._class_cb["values"] = classes
        slot_values = ["All"] + [s["label"] for s in att.get_slots()]
        self._slot_cb["values"] = slot_values
        self._populate_records_tree(att.get_all_attendance())

    def _filter_records(self):
        d    = self.filter_date_var.get().strip()
        slot = self.filter_slot_var.get()
        cls  = self.filter_class_var.get()
        records = att.get_all_attendance() if not d or d == "YYYY-MM-DD" \
                  else att.get_attendance_by_date(d)
        if slot != "All":
            records = [r for r in records if r.get("slot", r.get("session","")) == slot]
        if cls != "All":
            students = {s["student_id"] for s in database.get_all_students_info()
                        if s.get("class_name") == cls}
            records = [r for r in records if r["student_id"] in students]
        self._populate_records_tree(records)

    def _export_excel(self):
        path = att.export_to_excel()
        if path: messagebox.showinfo("Export OK", f"Saved to:\n{path}")
        else:    messagebox.showerror("Export Failed", "pip install openpyxl")

    # ── Students page ────────────────────────────────────────────────────────────

    def _build_students_page(self):
        page = self._pages["students"]
        tb = tk.Frame(page, bg=C["bg"], pady=14); tb.pack(fill="x", padx=20)
        self.stu_count_lbl = tk.Label(tb, text="", font=FONT["small"],
                                      bg=C["bg"], fg=C["text3"])
        self.stu_count_lbl.pack(side="left")
        AnimatedButton(tb, "⟳  Refresh", command=self._load_students_list,
                       normal_bg=C["glass2"], hover_bg=C["border_hi"]).pack(side="right", padx=(8,0))
        DangerButton(tb, "🗑  Delete Selected",
                     self._delete_selected_student).pack(side="right")

        _apply_tree_style("S", row_height=34)
        self.students_tree = _make_tree(page, cols={
            "student_id":    ("Student ID",    160),
            "name":          ("Name",          240),
            "class_name":    ("Class",         120),
            "branch":        ("Branch",        140),
            "registered_at": ("Registered At", 180),
        }, style_name="S")
        self.students_tree.bind("<Double-1>", lambda _: self._delete_selected_student())

    def _load_students_list(self):
        students = database.get_all_students_info()
        self.students_tree.delete(*self.students_tree.get_children())
        for i, s in enumerate(students):
            self.students_tree.insert("", "end", iid=s["student_id"],
                tags=("alt",) if i%2 else (),
                values=(s["student_id"], s["name"],
                        s.get("class_name",""), s.get("branch",""),
                        s["registered_at"]))
        self.stu_count_lbl.config(text=f"{len(students)} student(s) registered")
        self._refresh_stats()

    def _delete_selected_student(self):
        sel = self.students_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a student row first.")
            return
        student_id = sel[0]
        name = self.students_tree.item(sel[0])["values"][1]
        if messagebox.askyesno("Confirm Delete",
                               f"Permanently delete:\n  ID: {student_id}\n  Name: {name}\n\n"
                               f"All attendance records will also be removed."):
            if database.delete_student(student_id):
                messagebox.showinfo("Deleted", f"✓ {name} removed.")
                self._load_students_list(); self.refresh_known_students()
            else:
                messagebox.showerror("Error", "Could not delete. Check app.log.")

    # ── Admin Panel page ────────────────────────────────────────────────────────

    def _build_admin_page(self):
        page = self._pages["admin"]

        nb = ttk.Notebook(page)
        nb.pack(fill="both", expand=True, padx=16, pady=12)

        self._tab_stu      = tk.Frame(nb, bg=C["bg"])
        self._tab_settings = tk.Frame(nb, bg=C["bg"])
        self._tab_admins   = tk.Frame(nb, bg=C["bg"])

        nb.add(self._tab_stu,      text="  👤  Student Management  ")
        nb.add(self._tab_settings, text="  ⚙   Settings  ")
        nb.add(self._tab_admins,   text="  🔑  Admin Accounts  ")

        self._build_admin_students_tab()
        self._build_settings_tab()
        self._build_admins_tab()

    # ── Admin → Student Management ──────────────────────────────────────────────

    def _build_admin_students_tab(self):
        tab = self._tab_stu
        # Left: list  |  Right: edit form
        left  = tk.Frame(tab, bg=C["bg"], width=520)
        left.pack(side="left", fill="both", expand=True, padx=(16,8), pady=12)
        right = GlassFrame(tab, width=380, padx=24, pady=24)
        right.pack(side="right", fill="y", padx=(0,16), pady=12)
        right.pack_propagate(False)

        # ── List panel ──
        tb = tk.Frame(left, bg=C["bg"]); tb.pack(fill="x", pady=(0,8))
        tk.Label(tb, text="All Students", font=FONT["title"],
                 bg=C["bg"], fg=C["text"]).pack(side="left")
        AnimatedButton(tb, "⟳", command=self._refresh_admin_students,
                       normal_bg=C["glass2"], hover_bg=C["border_hi"],
                       padx=10).pack(side="right")

        _apply_tree_style("AS", row_height=30)
        af = tk.Frame(left, bg=C["bg"]); af.pack(fill="both", expand=True)
        vsb = ttk.Scrollbar(af, orient="vertical"); vsb.pack(side="right", fill="y")
        self.admin_stu_tree = ttk.Treeview(af,
            columns=("student_id","name","class_name","branch","dob"),
            show="headings", yscrollcommand=vsb.set, style="AS.Treeview")
        vsb.config(command=self.admin_stu_tree.yview)
        for col, lbl, w in [("student_id","ID",90),("name","Name",180),
                             ("class_name","Class",90),("branch","Branch",110),
                             ("dob","DOB",110)]:
            self.admin_stu_tree.heading(col, text=lbl)
            self.admin_stu_tree.column(col, width=w, anchor="center")
        self.admin_stu_tree.tag_configure("alt", background=C["row_alt"])
        self.admin_stu_tree.pack(fill="both", expand=True)
        self.admin_stu_tree.bind("<<TreeviewSelect>>", self._on_admin_student_select)

        # ── Edit form panel ──
        tk.Label(right, text="Edit Student", font=FONT["title"],
                 bg=C["glass"], fg=C["accent"]).pack(pady=(0,14))
        Divider(right).pack(fill="x", pady=(0,14))

        self.edit_id_var     = tk.StringVar()
        self.edit_name_var   = tk.StringVar()
        self.edit_class_var  = tk.StringVar()
        self.edit_branch_var = tk.StringVar()
        self.edit_dob_var    = tk.StringVar()

        ModernEntry(right, "STUDENT ID (read-only)", self.edit_id_var, width=24).pack(fill="x", pady=5)
        self.edit_id_entry = right.winfo_children()[-1].entry
        self.edit_id_entry.config(state="readonly")

        ModernEntry(right, "FULL NAME",   self.edit_name_var,   width=24).pack(fill="x", pady=5)
        ModernEntry(right, "CLASS",       self.edit_class_var,  width=24).pack(fill="x", pady=5)
        ModernEntry(right, "BRANCH",      self.edit_branch_var, width=24).pack(fill="x", pady=5)
        ModernEntry(right, "DATE OF BIRTH (YYYY-MM-DD)", self.edit_dob_var, width=24).pack(fill="x", pady=5)

        self.edit_status_lbl = tk.Label(right, text="", font=FONT["small"],
                                         bg=C["glass"], fg=C["accent4"])
        self.edit_status_lbl.pack(pady=(8,0))

        btn_row = tk.Frame(right, bg=C["glass"]); btn_row.pack(pady=(12,0))
        PrimaryButton(btn_row, "💾 Save Changes",
                      self._save_student_edits, padx=14, pady=7).pack(side="left", padx=(0,8))
        AnimatedButton(btn_row, "📸 Re-capture Face",
                       command=self._recapture_face,
                       normal_bg=C["purple"], hover_bg=C["accent2"],
                       padx=12, pady=7).pack(side="left")

        DangerButton(right, "🗑  Delete Student",
                     self._delete_admin_student, padx=14, pady=7).pack(pady=(10,0))

    def _refresh_admin_students(self):
        students = database.get_all_students_info()
        self.admin_stu_tree.delete(*self.admin_stu_tree.get_children())
        for i, s in enumerate(students):
            self.admin_stu_tree.insert("", "end", iid=s["student_id"],
                tags=("alt",) if i%2 else (),
                values=(s["student_id"], s["name"],
                        s.get("class_name",""), s.get("branch",""),
                        s.get("dob","")))

    def _on_admin_student_select(self, _=None):
        sel = self.admin_stu_tree.selection()
        if not sel: return
        s = database.get_student_by_id(sel[0])
        if not s: return
        self.edit_id_var.set(s["student_id"])
        self.edit_name_var.set(s["name"])
        self.edit_class_var.set(s.get("class_name",""))
        self.edit_branch_var.set(s.get("branch",""))
        self.edit_dob_var.set(s.get("dob",""))
        self.edit_status_lbl.config(text="")

    def _save_student_edits(self):
        sid    = self.edit_id_var.get().strip()
        name   = self.edit_name_var.get().strip().title()
        cls    = self.edit_class_var.get().strip()
        branch = self.edit_branch_var.get().strip()
        dob    = self.edit_dob_var.get().strip()
        if not sid or not name:
            self.edit_status_lbl.config(text="ID and Name are required.", fg=C["accent3"])
            return
        if database.update_student(sid, name, cls, branch, dob):
            self.edit_status_lbl.config(text="✓ Saved successfully.", fg=C["accent4"])
            self._refresh_admin_students()
            self._load_students_list()
        else:
            self.edit_status_lbl.config(text="✗ Update failed.", fg=C["accent3"])

    def _recapture_face(self):
        sid = self.edit_id_var.get().strip()
        if not sid:
            messagebox.showwarning("No Student", "Select a student first.")
            return
        self.edit_status_lbl.config(text="⏳ Opening camera...", fg=C["accent5"])
        self.update()
        encoding = face_utils.capture_face_encoding(num_samples=5)
        if encoding is None:
            self.edit_status_lbl.config(text="✗ Capture cancelled.", fg=C["accent3"])
            return
        if database.update_student_encoding(sid, encoding):
            self.edit_status_lbl.config(text="✓ Face updated!", fg=C["accent4"])
            self.refresh_known_students()
        else:
            self.edit_status_lbl.config(text="✗ Update failed.", fg=C["accent3"])

    def _delete_admin_student(self):
        sid  = self.edit_id_var.get().strip()
        name = self.edit_name_var.get().strip()
        if not sid:
            messagebox.showwarning("No Student", "Select a student first.")
            return
        if messagebox.askyesno("Confirm Delete",
                               f"Delete {name} ({sid}) and all their attendance records?"):
            if database.delete_student(sid):
                messagebox.showinfo("Deleted", f"✓ {name} removed.")
                for v in (self.edit_id_var, self.edit_name_var, self.edit_class_var,
                          self.edit_branch_var, self.edit_dob_var):
                    v.set("")
                self._refresh_admin_students()
                self._load_students_list()
                self.refresh_known_students()
            else:
                messagebox.showerror("Error", "Delete failed.")

    # ── Admin → Settings ────────────────────────────────────────────────────────

    def _build_settings_tab(self):
        tab = self._tab_settings
        canvas = tk.Canvas(tab, bg=C["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=C["bg"])
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))

        def section(title):
            tk.Label(inner, text=title, font=FONT["heading"],
                     bg=C["bg"], fg=C["accent"]).pack(anchor="w", padx=24, pady=(18,4))
            Divider(inner).pack(fill="x", padx=24)

        def row(parent, label, key, width=14):
            f = tk.Frame(parent, bg=C["bg"]); f.pack(fill="x", padx=24, pady=4)
            tk.Label(f, text=label, font=FONT["body"], bg=C["bg"],
                     fg=C["text"], width=30, anchor="w").pack(side="left")
            var = tk.StringVar(value=database.get_setting(key, ""))
            e = tk.Entry(f, textvariable=var, font=FONT["mono"],
                         bg=C["glass2"], fg=C["text"],
                         insertbackground=C["accent"],
                         relief="flat", bd=6, width=width)
            e.pack(side="left", padx=(0,12))
            return var, key

        self._setting_vars = []

        section("🕐  Working Hours & Slots")
        self._setting_vars.append(row(inner, "Day starts (HH:MM)",          "day_start"))
        self._setting_vars.append(row(inner, "Day ends (HH:MM)",            "day_end"))
        self._setting_vars.append(row(inner, "Slot duration (minutes)",     "slot_duration"))

        section("🍽  Break Time")
        self._setting_vars.append(row(inner, "Break starts (HH:MM)",        "break_start"))
        self._setting_vars.append(row(inner, "Break ends (HH:MM)",          "break_end"))

        section("⚙  Recognition & Camera")
        self._setting_vars.append(row(inner, "Min confidence % (0–100)",    "confidence_threshold"))
        self._setting_vars.append(row(inner, "Cooldown minutes",            "cooldown_minutes"))
        self._setting_vars.append(row(inner, "Camera index (0, 1, …)",      "camera_index"))

        save_row = tk.Frame(inner, bg=C["bg"]); save_row.pack(pady=20, padx=24, anchor="w")
        self._settings_msg = tk.Label(save_row, text="", font=FONT["small"],
                                      bg=C["bg"], fg=C["accent4"])
        self._settings_msg.pack(side="right", padx=16)
        PrimaryButton(save_row, "💾  Save All Settings",
                      self._save_settings).pack(side="left")

    def _save_settings(self):
        for var, key in self._setting_vars:
            database.set_setting(key, var.get().strip())
        # Update face_utils confidence threshold live
        try:
            face_utils.MIN_CONFIDENCE = float(
                database.get_setting("confidence_threshold", "60"))
        except ValueError:
            pass
        self._settings_msg.config(text="✓ Settings saved!", fg=C["accent4"])
        self.after(3000, lambda: self._settings_msg.config(text=""))

    # ── Admin → Admin Accounts ──────────────────────────────────────────────────

    def _build_admins_tab(self):
        tab = self._tab_admins
        left  = tk.Frame(tab, bg=C["bg"]); left.pack(side="left", fill="both",
                                                      expand=True, padx=(16,8), pady=16)
        right = GlassFrame(tab, width=320, padx=24, pady=24)
        right.pack(side="right", fill="y", padx=(0,16), pady=16)
        right.pack_propagate(False)

        tk.Label(left, text="Admin Accounts", font=FONT["title"],
                 bg=C["bg"], fg=C["text"]).pack(anchor="w", pady=(0,8))

        _apply_tree_style("ADM")
        af = tk.Frame(left, bg=C["bg"]); af.pack(fill="both", expand=True)
        vsb = ttk.Scrollbar(af, orient="vertical"); vsb.pack(side="right", fill="y")
        self.admins_tree = ttk.Treeview(af, columns=("username","created_at"),
                                         show="headings", yscrollcommand=vsb.set,
                                         style="ADM.Treeview")
        vsb.config(command=self.admins_tree.yview)
        self.admins_tree.heading("username",   text="Username")
        self.admins_tree.heading("created_at", text="Created At")
        self.admins_tree.column("username",   width=200, anchor="center")
        self.admins_tree.column("created_at", width=200, anchor="center")
        self.admins_tree.pack(fill="both", expand=True)
        self._refresh_admins_list()

        # Add admin form
        tk.Label(right, text="Add New Admin", font=FONT["title"],
                 bg=C["glass"], fg=C["accent"]).pack(pady=(0,14))
        Divider(right).pack(fill="x", pady=(0,14))

        self.new_admin_user = tk.StringVar()
        self.new_admin_pass = tk.StringVar()
        self.new_admin_pass2 = tk.StringVar()
        ModernEntry(right, "USERNAME", self.new_admin_user, width=22).pack(fill="x", pady=5)
        ModernEntry(right, "PASSWORD", self.new_admin_pass, width=22, show="●").pack(fill="x", pady=5)
        ModernEntry(right, "CONFIRM PASSWORD", self.new_admin_pass2, width=22, show="●").pack(fill="x", pady=5)

        self.admin_form_msg = tk.Label(right, text="", font=FONT["small"],
                                        bg=C["glass"], fg=C["accent4"])
        self.admin_form_msg.pack(pady=(8,0))

        PrimaryButton(right, "➕ Add Admin",
                      self._add_admin, padx=14, pady=7).pack(pady=(10,0))
        Divider(right).pack(fill="x", pady=14)

        tk.Label(right, text="Change Password", font=FONT["heading"],
                 bg=C["glass"], fg=C["text2"]).pack(anchor="w")
        self.chg_pass_new = tk.StringVar()
        ModernEntry(right, "NEW PASSWORD (selected user)",
                    self.chg_pass_new, width=22, show="●").pack(fill="x", pady=6)
        AnimatedButton(right, "🔑 Change Password",
                       command=self._change_password,
                       normal_bg=C["glass2"], hover_bg=C["purple"],
                       padx=12, pady=7).pack(pady=(4,0))

        Divider(right).pack(fill="x", pady=14)
        DangerButton(right, "🗑  Delete Selected Admin",
                     self._delete_admin, padx=12, pady=7).pack()

    def _refresh_admins_list(self):
        admins = database.get_all_admins()
        self.admins_tree.delete(*self.admins_tree.get_children())
        for a in admins:
            self.admins_tree.insert("", "end", iid=a["username"],
                                    values=(a["username"], a["created_at"]))

    def _add_admin(self):
        u  = self.new_admin_user.get().strip()
        p  = self.new_admin_pass.get().strip()
        p2 = self.new_admin_pass2.get().strip()
        if not u or not p:
            self.admin_form_msg.config(text="Username and password required.", fg=C["accent3"])
            return
        if p != p2:
            self.admin_form_msg.config(text="Passwords do not match.", fg=C["accent3"])
            return
        if database.add_admin(u, p):
            self.admin_form_msg.config(text=f"✓ Admin '{u}' added.", fg=C["accent4"])
            self.new_admin_user.set(""); self.new_admin_pass.set(""); self.new_admin_pass2.set("")
            self._refresh_admins_list()
        else:
            self.admin_form_msg.config(text="Username already exists.", fg=C["accent3"])

    def _change_password(self):
        sel = self.admins_tree.selection()
        if not sel:
            messagebox.showwarning("Select Admin", "Select an admin row first.")
            return
        username = sel[0]
        new_pass = self.chg_pass_new.get().strip()
        if not new_pass:
            messagebox.showwarning("Empty", "Enter a new password.")
            return
        if database.change_admin_password(username, new_pass):
            messagebox.showinfo("Done", f"Password changed for '{username}'.")
            self.chg_pass_new.set("")
        else:
            messagebox.showerror("Error", "Password change failed.")

    def _delete_admin(self):
        sel = self.admins_tree.selection()
        if not sel:
            messagebox.showwarning("Select Admin", "Select an admin row first.")
            return
        username = sel[0]
        if username == "admin":
            messagebox.showwarning("Protected", "Cannot delete the default admin account.")
            return
        if messagebox.askyesno("Confirm", f"Delete admin account '{username}'?"):
            if database.delete_admin(username):
                self._refresh_admins_list()
            else:
                messagebox.showerror("Error", "Delete failed.")

    # ── Cleanup ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        self._stop_camera()
        self.after(300, self.destroy)


if __name__ == "__main__":
    app = FaceAttendanceApp()
    app.mainloop()
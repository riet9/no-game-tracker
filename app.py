import json
import os
import shutil
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

from tracker_core import (
    BACKGROUND_PENALTY_PER_HOUR,
    BASE_DIR,
    LOG_PATH,
    PENALTY_PER_HOUR,
    format_duration,
    load_config,
    load_control,
    load_recent_sessions,
    load_state,
    load_stats,
    manual_export_sessions_to_csv,
    save_config,
    save_control,
)


class ToolTip:
    def __init__(self, widget, text: str, delay_ms: int = 900):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.tip_window = None
        self.after_id = None
        self.widget.bind("<Enter>", self.schedule)
        self.widget.bind("<Leave>", self.hide)
        self.widget.bind("<ButtonPress>", self.hide)

    def schedule(self, _event=None):
        self.cancel()
        self.after_id = self.widget.after(self.delay_ms, self.show)

    def cancel(self):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def show(self):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#1f3125",
            foreground="#eef4f0",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
        )
        label.pack()

    def hide(self, _event=None):
        self.cancel()
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class NotebookToolTip:
    def __init__(self, notebook: ttk.Notebook, texts: dict[int, str], delay_ms: int = 900):
        self.notebook = notebook
        self.texts = texts
        self.delay_ms = delay_ms
        self.after_id = None
        self.tip_window = None
        self.current_index = None
        notebook.bind("<Motion>", self.on_motion)
        notebook.bind("<Leave>", self.hide)
        notebook.bind("<ButtonPress>", self.hide)

    def on_motion(self, event):
        try:
            index = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            self.hide()
            return

        if index != self.current_index:
            self.hide()
            self.current_index = index
            self.after_id = self.notebook.after(self.delay_ms, lambda: self.show(event.x_root, event.y_root))

    def show(self, x_root: int, y_root: int):
        text = self.texts.get(self.current_index, "")
        if not text or self.tip_window:
            return
        self.tip_window = tw = tk.Toplevel(self.notebook)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x_root + 12}+{y_root + 18}")
        label = tk.Label(
            tw,
            text=text,
            justify="left",
            background="#1f3125",
            foreground="#eef4f0",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
        )
        label.pack()

    def hide(self, _event=None):
        if self.after_id:
            self.notebook.after_cancel(self.after_id)
            self.after_id = None
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None
        self.current_index = None


class NoGameTrackerUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("No Game Tracker Control Panel")
        self.root.geometry("1240x860")
        self.root.minsize(1120, 760)
        self.icon_path = BASE_DIR / "assets" / "app_icon.png"
        self.icon_ico_path = BASE_DIR / "assets" / "app_icon.ico"

        self.config = load_config()
        self.tray_icon = None
        self.tray_thread = None
        self.games_data = list(self.config.get("games", []))
        self.window_icon = None

        self.agent_status_var = tk.StringVar(value="Unknown")
        self.pause_status_var = tk.StringVar(value="Unknown")
        self.active_game_var = tk.StringVar(value="No active game")
        self.process_var = tk.StringVar(value="-")
        self.window_var = tk.StringVar(value="-")
        self.last_seen_var = tk.StringVar(value="-")
        self.running_var = tk.StringVar(value="0h 0m")
        self.foreground_var = tk.StringVar(value="0h 0m")
        self.background_var = tk.StringVar(value="0h 0m")
        self.penalty_var = tk.StringVar(value="0.00 EUR")
        self.all_sessions_var = tk.StringVar(value="0")
        self.all_hours_var = tk.StringVar(value="0.00")
        self.all_penalty_var = tk.StringVar(value="0.00 EUR")
        self.today_var = tk.StringVar(value="0.00h / 0.00 EUR")
        self.week_var = tk.StringVar(value="0.00h / 0.00 EUR")
        self.error_var = tk.StringVar(value="-")

        self._build_styles()
        self._load_window_icon()
        self._build_layout()
        self._attach_tooltips()
        self._load_config_into_form()
        self.refresh_all()
        self.root.after(3000, self.poll_state)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Root.TFrame", background="#eef4f0")
        style.configure("Panel.TFrame", background="#f9fcf7")
        style.configure("Panel.TLabelframe", background="#f9fcf7")
        style.configure("Panel.TLabelframe.Label", background="#f9fcf7", foreground="#1d2f23", font=("Segoe UI Semibold", 11))
        style.configure("Header.TLabel", background="#eef4f0", foreground="#173423", font=("Segoe UI Semibold", 24))
        style.configure("Sub.TLabel", background="#eef4f0", foreground="#4a6656", font=("Segoe UI", 10))
        style.configure("Body.TLabel", background="#f9fcf7", foreground="#1f3125", font=("Segoe UI", 10))
        style.configure("Value.TLabel", background="#f9fcf7", foreground="#0d6b52", font=("Consolas", 14, "bold"))

    def _load_window_icon(self) -> None:
        if self.icon_ico_path.exists():
            try:
                self.root.iconbitmap(str(self.icon_ico_path))
            except tk.TclError:
                pass
        if not self.icon_path.exists():
            return
        try:
            self.window_icon = tk.PhotoImage(file=str(self.icon_path))
            self.root.iconphoto(True, self.window_icon)
        except tk.TclError:
            self.window_icon = None

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="Root.TFrame", padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Root.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="No Game Tracker Control Panel", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Background agent, tray-ready UI, SQLite stats, Telegram control, and editable game rules.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(4, 12))
        ttk.Label(
            header,
            text=f"Penalty rates: foreground {PENALTY_PER_HOUR:.0f} EUR/hour ({PENALTY_PER_HOUR / 2:.0f} each), background {BACKGROUND_PENALTY_PER_HOUR:.0f} EUR/hour.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        toolbar = ttk.Frame(outer, style="Root.TFrame")
        toolbar.pack(fill="x", pady=(0, 12))
        self.pause_button = ttk.Button(toolbar, text="Pause Agent", command=self.pause_agent)
        self.pause_button.pack(side="left")
        self.resume_button = ttk.Button(toolbar, text="Resume Agent", command=self.resume_agent)
        self.resume_button.pack(side="left", padx=8)
        self.start_agent_button = ttk.Button(toolbar, text="Start Agent Now", command=self.start_agent_now)
        self.start_agent_button.pack(side="left")
        self.refresh_button = ttk.Button(toolbar, text="Refresh", command=self.refresh_all)
        self.refresh_button.pack(side="left", padx=8)
        self.export_button = ttk.Button(toolbar, text="Export CSV", command=self.export_csv)
        self.export_button.pack(side="left")
        self.open_folder_button = ttk.Button(toolbar, text="Open Folder", command=self.open_project_folder)
        self.open_folder_button.pack(side="left", padx=8)
        self.save_button = ttk.Button(toolbar, text="Save Settings", command=self.save_settings)
        self.save_button.pack(side="right")

        top = ttk.Frame(outer, style="Root.TFrame")
        top.pack(fill="x", pady=(0, 12))

        agent_frame = ttk.LabelFrame(top, text="Agent Status", style="Panel.TLabelframe", padding=14)
        agent_frame.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self._info_row(agent_frame, "Agent", self.agent_status_var)
        self._info_row(agent_frame, "Paused", self.pause_status_var)
        self._info_row(agent_frame, "Active game", self.active_game_var)
        self._info_row(agent_frame, "Last heartbeat", self.last_seen_var)
        self._info_row(agent_frame, "Error", self.error_var)

        live_frame = ttk.LabelFrame(top, text="Live Counters", style="Panel.TLabelframe", padding=14)
        live_frame.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self._info_row(live_frame, "Running", self.running_var)
        self._info_row(live_frame, "Foreground", self.foreground_var)
        self._info_row(live_frame, "Background", self.background_var)
        self._info_row(live_frame, "Penalty", self.penalty_var)
        self._info_row(live_frame, "Process", self.process_var)
        self._info_row(live_frame, "Window", self.window_var)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self.dashboard_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.settings_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.games_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.reports_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.logs_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.games_tab, text="Game Editor")
        self.notebook.add(self.reports_tab, text="Reports")
        self.notebook.add(self.logs_tab, text="Logs")

        self._build_dashboard_tab()
        self._build_settings_tab()
        self._build_games_tab()
        self._build_reports_tab()
        self._build_logs_tab()

    def _attach_tooltips(self) -> None:
        ToolTip(self.pause_button, "Temporarily stop tracking without shutting down the background agent.")
        ToolTip(self.resume_button, "Resume tracking after a pause.")
        ToolTip(self.start_agent_button, "Start the background agent right now if it is not already running.")
        ToolTip(self.refresh_button, "Reload live state, stats, reports, and logs from disk.")
        ToolTip(self.export_button, "Export finished sessions from SQLite into game_log.csv for Excel or sharing.")
        ToolTip(self.open_folder_button, "Open the project folder with the database, logs, config, and exports.")
        ToolTip(self.save_button, "Write current settings and edited games into config.json.")
        NotebookToolTip(
            self.notebook,
            {
                0: "Live status and overall totals from the background agent and database.",
                1: "Telegram, polling, filters, launcher processes, and detection exclusions.",
                2: "Add, edit, or remove tracked games without manual JSON editing.",
                3: "Recent sessions and a simple daily chart from the SQLite history.",
                4: "Technical log output for troubleshooting the agent and UI.",
            },
        )

    def _info_row(self, parent: ttk.Widget, label: str, variable: tk.StringVar) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, style="Body.TLabel", width=16).pack(side="left")
        ttk.Label(row, textvariable=variable, style="Value.TLabel").pack(side="left", fill="x", expand=True)

    def _build_dashboard_tab(self) -> None:
        summary = ttk.LabelFrame(self.dashboard_tab, text="Database Summary", style="Panel.TLabelframe", padding=14)
        summary.pack(fill="x")
        self._info_row(summary, "Sessions", self.all_sessions_var)
        self._info_row(summary, "Running hours", self.all_hours_var)
        self._info_row(summary, "Penalty", self.all_penalty_var)
        self._info_row(summary, "Today", self.today_var)
        self._info_row(summary, "This week", self.week_var)

        active = ttk.LabelFrame(self.dashboard_tab, text="Currently Tracked Games", style="Panel.TLabelframe", padding=14)
        active.pack(fill="both", expand=True, pady=(12, 0))
        self.games_tree = ttk.Treeview(
            active,
            columns=("game", "state", "running", "foreground", "background", "penalty"),
            show="headings",
            height=10,
        )
        for col, text, width in [
            ("game", "Game", 240),
            ("state", "State", 110),
            ("running", "Running", 120),
            ("foreground", "Foreground", 120),
            ("background", "Background", 120),
            ("penalty", "Penalty", 120),
        ]:
            self.games_tree.heading(col, text=text)
            self.games_tree.column(col, width=width, anchor="center")
        self.games_tree.column("game", anchor="w")
        self.games_tree.pack(fill="both", expand=True)

    def _build_settings_tab(self) -> None:
        form = ttk.LabelFrame(self.settings_tab, text="Telegram and Agent Settings", style="Panel.TLabelframe", padding=14)
        form.pack(fill="x")
        ttk.Label(form, text="Bot token", style="Body.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.bot_token_entry = ttk.Entry(form, width=70)
        self.bot_token_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Chat ID", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.chat_id_entry = ttk.Entry(form, width=70)
        self.chat_id_entry.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Poll seconds", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.poll_entry = ttk.Entry(form, width=12)
        self.poll_entry.grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(form, text="Close grace polls", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        self.close_grace_entry = ttk.Entry(form, width=12)
        self.close_grace_entry.grid(row=3, column=1, sticky="w", pady=4)

        self.track_background_var = tk.BooleanVar(value=True)
        self.track_foreground_var = tk.BooleanVar(value=True)
        self.telegram_events_var = tk.BooleanVar(value=True)
        self.telegram_commands_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Track background-open games", variable=self.track_background_var).grid(row=4, column=1, sticky="w", pady=4)
        ttk.Checkbutton(form, text="Track active foreground games", variable=self.track_foreground_var).grid(row=5, column=1, sticky="w", pady=4)
        ttk.Checkbutton(form, text="Send Telegram on state changes", variable=self.telegram_events_var).grid(row=6, column=1, sticky="w", pady=4)
        ttk.Checkbutton(form, text="Enable Telegram commands", variable=self.telegram_commands_var).grid(row=7, column=1, sticky="w", pady=4)

        ttk.Label(form, text="Launcher processes", style="Body.TLabel").grid(row=8, column=0, sticky="nw", pady=4)
        self.launchers_text = ScrolledText(form, width=56, height=5, font=("Consolas", 10))
        self.launchers_text.grid(row=8, column=1, sticky="ew", pady=4)
        ttk.Label(form, text="Excluded processes", style="Body.TLabel").grid(row=9, column=0, sticky="nw", pady=4)
        self.excluded_text = ScrolledText(form, width=56, height=7, font=("Consolas", 10))
        self.excluded_text.grid(row=9, column=1, sticky="ew", pady=4)
        ttk.Label(form, text="Excluded window keywords", style="Body.TLabel").grid(row=10, column=0, sticky="nw", pady=4)
        self.excluded_windows_text = ScrolledText(form, width=56, height=5, font=("Consolas", 10))
        self.excluded_windows_text.grid(row=10, column=1, sticky="ew", pady=4)
        form.columnconfigure(1, weight=1)

    def _build_games_tab(self) -> None:
        wrapper = ttk.Frame(self.games_tab, style="Panel.TFrame")
        wrapper.pack(fill="both", expand=True)

        left = ttk.LabelFrame(wrapper, text="Games", style="Panel.TLabelframe", padding=12)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.editor_tree = ttk.Treeview(left, columns=("name", "processes", "keywords"), show="headings", height=18)
        self.editor_tree.heading("name", text="Name")
        self.editor_tree.heading("processes", text="Processes")
        self.editor_tree.heading("keywords", text="Keywords")
        self.editor_tree.column("name", width=240, anchor="w")
        self.editor_tree.column("processes", width=220, anchor="w")
        self.editor_tree.column("keywords", width=260, anchor="w")
        self.editor_tree.pack(fill="both", expand=True)
        self.editor_tree.bind("<<TreeviewSelect>>", self.on_game_select)

        right = ttk.LabelFrame(wrapper, text="Editor", style="Panel.TLabelframe", padding=12)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Name", style="Body.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.game_name_entry = ttk.Entry(right, width=40)
        self.game_name_entry.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(right, text="Process names", style="Body.TLabel").grid(row=1, column=0, sticky="nw", pady=4)
        self.game_processes_text = ScrolledText(right, width=42, height=6, font=("Consolas", 10))
        self.game_processes_text.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(right, text="Title keywords", style="Body.TLabel").grid(row=2, column=0, sticky="nw", pady=4)
        self.game_keywords_text = ScrolledText(right, width=42, height=6, font=("Consolas", 10))
        self.game_keywords_text.grid(row=2, column=1, sticky="ew", pady=4)
        right.columnconfigure(1, weight=1)
        button_row = ttk.Frame(right, style="Panel.TFrame")
        button_row.grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Button(button_row, text="Add New", command=self.add_game).pack(side="left")
        ttk.Button(button_row, text="Update Selected", command=self.update_selected_game).pack(side="left", padx=8)
        ttk.Button(button_row, text="Delete Selected", command=self.delete_selected_game).pack(side="left")

    def _build_reports_tab(self) -> None:
        summary = ttk.LabelFrame(self.reports_tab, text="Recent Sessions", style="Panel.TLabelframe", padding=12)
        summary.pack(fill="both", expand=True)
        self.sessions_tree = ttk.Treeview(
            summary,
            columns=("game", "ended", "running", "foreground", "background", "penalty"),
            show="headings",
            height=10,
        )
        for col, text, width in [
            ("game", "Game", 220),
            ("ended", "Ended", 170),
            ("running", "Running", 120),
            ("foreground", "Foreground", 120),
            ("background", "Background", 120),
            ("penalty", "Penalty", 120),
        ]:
            self.sessions_tree.heading(col, text=text)
            self.sessions_tree.column(col, width=width, anchor="center")
        self.sessions_tree.column("game", anchor="w")
        self.sessions_tree.pack(fill="both", expand=True)

        chart_frame = ttk.LabelFrame(self.reports_tab, text="Last 14 Days", style="Panel.TLabelframe", padding=12)
        chart_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.chart_canvas = tk.Canvas(chart_frame, height=240, bg="#f9fcf7", highlightthickness=0)
        self.chart_canvas.pack(fill="both", expand=True)

    def _build_logs_tab(self) -> None:
        ttk.Label(self.logs_tab, text="tracker.log preview", style="Body.TLabel").pack(anchor="w", pady=(0, 8))
        self.log_text = ScrolledText(self.logs_tab, font=("Consolas", 10), state="disabled")
        self.log_text.pack(fill="both", expand=True)

    def _load_config_into_form(self) -> None:
        self.config = load_config()
        settings = self.config.get("settings", {})
        self.games_data = list(self.config.get("games", []))
        self.bot_token_entry.delete(0, tk.END)
        self.bot_token_entry.insert(0, self.config.get("telegram", {}).get("bot_token", ""))
        self.chat_id_entry.delete(0, tk.END)
        self.chat_id_entry.insert(0, self.config.get("telegram", {}).get("chat_id", ""))
        self.poll_entry.delete(0, tk.END)
        self.poll_entry.insert(0, str(settings.get("poll_interval_seconds", 10)))
        self.close_grace_entry.delete(0, tk.END)
        self.close_grace_entry.insert(0, str(settings.get("close_grace_polls", 2)))
        self.track_background_var.set(bool(settings.get("track_background_games", True)))
        self.track_foreground_var.set(bool(settings.get("track_foreground_games", True)))
        self.telegram_events_var.set(bool(settings.get("telegram_on_state_change", True)))
        self.telegram_commands_var.set(bool(settings.get("telegram_commands_enabled", True)))
        self.launchers_text.delete("1.0", tk.END)
        self.launchers_text.insert("1.0", "\n".join(self.config.get("launcher_processes", [])))
        self.excluded_text.delete("1.0", tk.END)
        self.excluded_text.insert("1.0", "\n".join(self.config.get("excluded_processes", [])))
        self.excluded_windows_text.delete("1.0", tk.END)
        self.excluded_windows_text.insert("1.0", "\n".join(self.config.get("excluded_window_keywords", [])))
        self.refresh_editor_tree()

    def build_config_from_form(self) -> dict:
        return {
            "telegram": {
                "bot_token": self.bot_token_entry.get().strip(),
                "chat_id": self.chat_id_entry.get().strip(),
            },
            "settings": {
                "poll_interval_seconds": int(self.poll_entry.get().strip() or "10"),
                "close_grace_polls": int(self.close_grace_entry.get().strip() or "2"),
                "track_background_games": bool(self.track_background_var.get()),
                "track_foreground_games": bool(self.track_foreground_var.get()),
                "telegram_on_state_change": bool(self.telegram_events_var.get()),
                "telegram_commands_enabled": bool(self.telegram_commands_var.get()),
            },
            "launcher_processes": [
                line.strip() for line in self.launchers_text.get("1.0", tk.END).splitlines() if line.strip()
            ],
            "excluded_processes": [
                line.strip() for line in self.excluded_text.get("1.0", tk.END).splitlines() if line.strip()
            ],
            "excluded_window_keywords": [
                line.strip() for line in self.excluded_windows_text.get("1.0", tk.END).splitlines() if line.strip()
            ],
            "games": list(self.games_data),
        }

    def save_settings(self) -> None:
        try:
            config = self.build_config_from_form()
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        save_config(config)
        self.config = config
        messagebox.showinfo("Saved", "Settings saved. The background agent will pick them up automatically.")

    def refresh_all(self) -> None:
        state = load_state()
        all_stats = load_stats("all")
        today_stats = load_stats("today")
        week_stats = load_stats("week")
        control = load_control()

        self.agent_status_var.set(self.compute_agent_status(state))
        self.pause_status_var.set("Paused" if control.get("paused") else "Live")
        self.active_game_var.set(state.get("active_game") or "No active game")
        self.process_var.set(state.get("foreground_process") or "-")
        self.window_var.set(state.get("foreground_window") or "-")
        self.last_seen_var.set(state.get("last_seen_at") or "-")
        self.error_var.set(state.get("last_error") or "-")

        totals = state.get("totals", {})
        self.running_var.set(format_duration(int(totals.get("running_seconds", 0))))
        self.foreground_var.set(format_duration(int(totals.get("foreground_seconds", 0))))
        self.background_var.set(format_duration(int(totals.get("background_seconds", 0))))
        self.penalty_var.set(f"{float(totals.get('penalty_eur', 0)):.2f} EUR")

        self.all_sessions_var.set(str(all_stats["sessions"]))
        self.all_hours_var.set(f"{all_stats['running_hours']:.2f}")
        self.all_penalty_var.set(f"{all_stats['total_penalty']:.2f} EUR")
        self.today_var.set(f"{today_stats['running_hours']:.2f}h / {today_stats['total_penalty']:.2f} EUR")
        self.week_var.set(f"{week_stats['running_hours']:.2f}h / {week_stats['total_penalty']:.2f} EUR")

        for item in self.games_tree.get_children():
            self.games_tree.delete(item)
        for game_name, payload in sorted(state.get("games", {}).items()):
            self.games_tree.insert(
                "",
                tk.END,
                values=(
                    game_name,
                    payload.get("state", "-"),
                    format_duration(int(payload.get("running_seconds", 0))),
                    format_duration(int(payload.get("foreground_seconds", 0))),
                    format_duration(int(payload.get("background_seconds", 0))),
                    f"{float(payload.get('penalty_eur', 0)):.2f} EUR",
                ),
            )

        self.refresh_recent_sessions()
        self.draw_chart(all_stats["daily"])

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        if LOG_PATH.exists():
            self.log_text.insert("1.0", LOG_PATH.read_text(encoding="utf-8", errors="replace")[-16000:])
        self.log_text.configure(state="disabled")

    def refresh_recent_sessions(self) -> None:
        for item in self.sessions_tree.get_children():
            self.sessions_tree.delete(item)
        for session in load_recent_sessions(20):
            self.sessions_tree.insert(
                "",
                tk.END,
                values=(
                    session["game_name"],
                    session["ended_at"].replace("T", " "),
                    format_duration(int(session["running_seconds"])),
                    format_duration(int(session["foreground_seconds"])),
                    format_duration(int(session["background_seconds"])),
                    f"{session['penalty_eur']:.2f} EUR",
                ),
            )

    def draw_chart(self, daily_rows: list[dict]) -> None:
        self.chart_canvas.delete("all")
        if not daily_rows:
            self.chart_canvas.create_text(220, 100, text="No sessions yet", fill="#4a6656", font=("Segoe UI", 14))
            return

        rows = list(reversed(daily_rows[:10]))
        max_hours = max(row["running_hours"] for row in rows) or 1
        width = max(self.chart_canvas.winfo_width(), 760)
        height = max(self.chart_canvas.winfo_height(), 220)
        left = 70
        bottom = height - 40
        bar_area = width - left - 30
        bar_width = max(24, int(bar_area / max(len(rows), 1) * 0.55))
        gap = max(10, int(bar_area / max(len(rows), 1) * 0.45))

        self.chart_canvas.create_line(left, 20, left, bottom, fill="#9ab6a6", width=2)
        self.chart_canvas.create_line(left, bottom, width - 20, bottom, fill="#9ab6a6", width=2)

        for idx, row in enumerate(rows):
            x0 = left + 20 + idx * (bar_width + gap)
            bar_height = int((row["running_hours"] / max_hours) * (bottom - 50))
            y0 = bottom - bar_height
            self.chart_canvas.create_rectangle(x0, y0, x0 + bar_width, bottom, fill="#0d6b52", outline="")
            self.chart_canvas.create_text(x0 + bar_width / 2, y0 - 10, text=f"{row['running_hours']:.1f}h", fill="#173423", font=("Segoe UI", 9))
            self.chart_canvas.create_text(x0 + bar_width / 2, bottom + 12, text=row["day"][5:], fill="#4a6656", font=("Segoe UI", 9))

    def refresh_editor_tree(self) -> None:
        for item in self.editor_tree.get_children():
            self.editor_tree.delete(item)
        for idx, game in enumerate(self.games_data):
            self.editor_tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    game.get("name", ""),
                    ", ".join(game.get("process_names", [])),
                    ", ".join(game.get("title_keywords", [])),
                ),
            )

    def on_game_select(self, _event=None) -> None:
        selection = self.editor_tree.selection()
        if not selection:
            return
        game = self.games_data[int(selection[0])]
        self.game_name_entry.delete(0, tk.END)
        self.game_name_entry.insert(0, game.get("name", ""))
        self.game_processes_text.delete("1.0", tk.END)
        self.game_processes_text.insert("1.0", "\n".join(game.get("process_names", [])))
        self.game_keywords_text.delete("1.0", tk.END)
        self.game_keywords_text.insert("1.0", "\n".join(game.get("title_keywords", [])))

    def build_game_from_form(self) -> dict:
        name = self.game_name_entry.get().strip()
        if not name:
            raise ValueError("Game name is required.")
        process_names = [line.strip() for line in self.game_processes_text.get("1.0", tk.END).splitlines() if line.strip()]
        title_keywords = [line.strip() for line in self.game_keywords_text.get("1.0", tk.END).splitlines() if line.strip()]
        if not process_names and not title_keywords:
            raise ValueError("Add at least one process name or title keyword.")
        return {"name": name, "process_names": process_names, "title_keywords": title_keywords}

    def add_game(self) -> None:
        try:
            game = self.build_game_from_form()
        except ValueError as exc:
            messagebox.showerror("Invalid game", str(exc))
            return
        self.games_data.append(game)
        self.refresh_editor_tree()

    def update_selected_game(self) -> None:
        selection = self.editor_tree.selection()
        if not selection:
            messagebox.showinfo("Select game", "Select a game first.")
            return
        try:
            game = self.build_game_from_form()
        except ValueError as exc:
            messagebox.showerror("Invalid game", str(exc))
            return
        self.games_data[int(selection[0])] = game
        self.refresh_editor_tree()

    def delete_selected_game(self) -> None:
        selection = self.editor_tree.selection()
        if not selection:
            return
        del self.games_data[int(selection[0])]
        self.refresh_editor_tree()
        self.game_name_entry.delete(0, tk.END)
        self.game_processes_text.delete("1.0", tk.END)
        self.game_keywords_text.delete("1.0", tk.END)

    def compute_agent_status(self, state: dict) -> str:
        if not state.get("agent_running"):
            return "Offline"
        heartbeat = state.get("last_seen_at")
        if not heartbeat:
            return "Unknown"
        try:
            last_seen = datetime.fromisoformat(heartbeat)
        except ValueError:
            return "Unknown"
        poll_seconds = int(state.get("poll_interval_seconds", 10) or 10)
        max_age = max(20, poll_seconds * 3)
        age_seconds = (datetime.now() - last_seen).total_seconds()
        return "Stale" if age_seconds > max_age else "Running"

    def poll_state(self) -> None:
        self.refresh_all()
        self.root.after(3000, self.poll_state)

    def pause_agent(self) -> None:
        control = load_control()
        control["paused"] = True
        save_control(control)
        self.refresh_all()

    def resume_agent(self) -> None:
        control = load_control()
        control["paused"] = False
        save_control(control)
        self.refresh_all()

    def start_agent_now(self) -> None:
        built_agent = BASE_DIR / "dist" / "NoGameTrackerAgent" / "NoGameTrackerAgent.exe"
        if built_agent.exists():
            subprocess.Popen([str(built_agent)], cwd=str(BASE_DIR))
            messagebox.showinfo("Agent", "Background agent exe launch requested.")
            return

        pythonw = shutil.which("pythonw.exe") or shutil.which("python.exe") or shutil.which("py")
        if not pythonw:
            messagebox.showerror("Python not found", "No built agent exe found, and pythonw.exe/python.exe/py was not found in PATH.")
            return

        args = [pythonw, str(BASE_DIR / "agent.py")] if pythonw.lower().endswith(".exe") else [pythonw, "agent.py"]
        subprocess.Popen(args, cwd=str(BASE_DIR))
        messagebox.showinfo("Agent", "Background agent launch requested.")

    def export_csv(self) -> None:
        ok, message = manual_export_sessions_to_csv()
        if ok:
            messagebox.showinfo("CSV export", message)
        else:
            messagebox.showerror("CSV export", message)

    def open_project_folder(self) -> None:
        os.startfile(str(BASE_DIR))

    def on_close(self) -> None:
        if pystray and Image and ImageDraw:
            self.hide_to_tray()
            return
        self.root.destroy()

    def hide_to_tray(self) -> None:
        self.root.withdraw()
        if self.tray_icon:
            return
        self.tray_thread = threading.Thread(target=self._run_tray_icon, daemon=True)
        self.tray_thread.start()

    def _run_tray_icon(self) -> None:
        icon = self.create_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Open UI", self.on_tray_open),
            pystray.MenuItem("Pause", self.on_tray_pause),
            pystray.MenuItem("Resume", self.on_tray_resume),
            pystray.MenuItem("Quit", self.on_tray_quit),
        )
        self.tray_icon = pystray.Icon("NoGameTracker", icon, "No Game Tracker", menu)
        self.tray_icon.run()

    def create_tray_icon(self):
        if self.icon_path.exists():
            try:
                return Image.open(self.icon_path)
            except Exception:
                pass
        image = Image.new("RGB", (64, 64), "#173423")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill="#0d6b52")
        draw.text((18, 18), "NG", fill="#eef4f0")
        return image

    def on_tray_open(self, _icon=None, _item=None) -> None:
        self.root.after(0, self.show_from_tray)

    def show_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None

    def on_tray_pause(self, _icon=None, _item=None) -> None:
        self.root.after(0, self.pause_agent)

    def on_tray_resume(self, _icon=None, _item=None) -> None:
        self.root.after(0, self.resume_agent)

    def on_tray_quit(self, _icon=None, _item=None) -> None:
        self.root.after(0, self.quit_app)

    def quit_app(self) -> None:
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    NoGameTrackerUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

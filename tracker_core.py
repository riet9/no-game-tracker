import csv
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib import error, parse, request

import psutil
import win32gui
import win32process


def resolve_base_dir() -> Path:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path(__file__).resolve().parent)

    sentinels = {"install_autostart.ps1", "BOT_GUIDE.txt", "config.json", "config.example.json"}
    for start in candidates:
        for path in [start, *start.parents]:
            if any((path / sentinel).exists() for sentinel in sentinels):
                return path

    override = os.environ.get("NO_GAME_TRACKER_HOME")
    if override:
        return Path(override).resolve()

    return Path(__file__).resolve().parent


BASE_DIR = resolve_base_dir()
CONFIG_PATH = BASE_DIR / "config.json"
CSV_PATH = BASE_DIR / "game_log.csv"
DB_PATH = BASE_DIR / "tracker.db"
LOG_PATH = BASE_DIR / "tracker.log"
STATE_PATH = BASE_DIR / "state.json"
CONTROL_PATH = BASE_DIR / "control.json"
LOCK_PATH = BASE_DIR / "agent.lock"
ACTIVE_SESSIONS_PATH = BASE_DIR / "active_sessions.json"
DEFAULT_INTERVAL_SECONDS = 10
EURO_PER_HOUR = 60
FRIENDS_MULTIPLIER = 2
PENALTY_PER_HOUR = EURO_PER_HOUR * FRIENDS_MULTIPLIER
BACKGROUND_PENALTY_DIVISOR = 10
BACKGROUND_PENALTY_PER_HOUR = PENALTY_PER_HOUR / BACKGROUND_PENALTY_DIVISOR
TITLE_MATCH_BLOCKLIST = {
    "dwm.exe",
    "applicationframehost.exe",
    "shellexperiencehost.exe",
    "explorer.exe",
    "python.exe",
    "pythonw.exe",
    "codex.exe",
    "code.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
    "discord.exe",
    "telegram.exe",
    "powershell.exe",
    "windowsterminal.exe",
    "cmd.exe",
}
DEFAULT_EXCLUDED_PROCESSES = sorted(
    TITLE_MATCH_BLOCKLIST.union(
        {
            "notepad.exe",
            "notepad++.exe",
            "winword.exe",
            "powerpnt.exe",
            "excel.exe",
            "onenote.exe",
            "outlook.exe",
            "acrord32.exe",
            "mspaint.exe",
            "devenv.exe",
            "idea64.exe",
            "pycharm64.exe",
            "studio64.exe",
            "obsidian.exe",
            "slack.exe",
            "steamwebhelper.exe",
        }
    )
)
DEFAULT_EXCLUDED_WINDOW_KEYWORDS = [
    "downloads",
    "documents",
    "desktop",
    "powerpoint",
    "microsoft word",
    "notes",
    "notepad",
]


@dataclass
class DetectionResult:
    game_name: str
    process_name: str
    window_title: str
    matched_by: str


@dataclass
class WindowInfo:
    process_name: str
    window_title: str


def default_config() -> dict:
    return {
        "telegram": {
            "bot_token": "replace_with_new_token",
            "chat_id": "2047828228",
        },
        "settings": {
            "poll_interval_seconds": DEFAULT_INTERVAL_SECONDS,
            "close_grace_polls": 2,
            "track_background_games": True,
            "track_foreground_games": True,
            "telegram_on_state_change": True,
            "telegram_commands_enabled": True,
        },
        "excluded_processes": list(DEFAULT_EXCLUDED_PROCESSES),
        "excluded_window_keywords": list(DEFAULT_EXCLUDED_WINDOW_KEYWORDS),
        "launcher_processes": ["steam.exe", "epicgameslauncher.exe"],
        "games": [
            {
                "name": "Counter-Strike 2",
                "process_names": ["cs2.exe"],
                "title_keywords": ["counter-strike 2", "cs2"],
            },
            {
                "name": "Dota 2",
                "process_names": ["dota2.exe"],
                "title_keywords": ["dota 2"],
            },
            {
                "name": "Fortnite",
                "process_names": ["fortniteclient-win64-shipping.exe"],
                "title_keywords": ["fortnite"],
            },
        ],
    }


def save_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def ensure_config_exists() -> None:
    if not CONFIG_PATH.exists():
        save_json(CONFIG_PATH, default_config())


def load_config() -> dict:
    ensure_config_exists()
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        config = json.load(config_file)

    config.setdefault("telegram", {})
    settings = config.setdefault("settings", {})
    settings.setdefault("poll_interval_seconds", DEFAULT_INTERVAL_SECONDS)
    settings.setdefault("close_grace_polls", 2)
    settings.setdefault("track_background_games", True)
    settings.setdefault("track_foreground_games", True)
    settings.setdefault("telegram_on_state_change", True)
    settings.setdefault("telegram_commands_enabled", True)
    config.setdefault("excluded_processes", list(DEFAULT_EXCLUDED_PROCESSES))
    config.setdefault("excluded_window_keywords", list(DEFAULT_EXCLUDED_WINDOW_KEYWORDS))
    config.setdefault("launcher_processes", [])
    config.setdefault("games", [])
    return config


def save_config(config: dict) -> None:
    save_json(CONFIG_PATH, config)


def default_control() -> dict:
    return {"paused": False, "telegram_offset": 0}


def ensure_control_exists() -> None:
    if not CONTROL_PATH.exists():
        save_json(CONTROL_PATH, default_control())


def load_control() -> dict:
    ensure_control_exists()
    with CONTROL_PATH.open("r", encoding="utf-8") as control_file:
        return json.load(control_file)


def save_control(control: dict) -> None:
    save_json(CONTROL_PATH, control)


def default_state() -> dict:
    return {
        "agent_running": False,
        "paused": False,
        "agent_pid": None,
        "last_seen_at": None,
        "poll_interval_seconds": DEFAULT_INTERVAL_SECONDS,
        "active_game": None,
        "foreground_process": "",
        "foreground_window": "",
        "last_error": "",
        "games": {},
        "totals": {
            "running_seconds": 0,
            "foreground_seconds": 0,
            "background_seconds": 0,
            "penalty_eur": 0.0,
        },
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return default_state()

    with STATE_PATH.open("r", encoding="utf-8") as state_file:
        return json.load(state_file)


def save_active_sessions_snapshot(payload: dict) -> None:
    save_json(ACTIVE_SESSIONS_PATH, payload)


def load_active_sessions_snapshot() -> dict:
    if not ACTIVE_SESSIONS_PATH.exists():
        return {"saved_at": None, "sessions": {}}
    with ACTIVE_SESSIONS_PATH.open("r", encoding="utf-8") as snapshot_file:
        return json.load(snapshot_file)


def clear_active_sessions_snapshot() -> None:
    try:
        if ACTIVE_SESSIONS_PATH.exists():
            ACTIVE_SESSIONS_PATH.unlink()
    except OSError:
        pass


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def ensure_csv_header() -> None:
    expected_header = ["date", "game", "running_hours", "foreground_hours", "background_hours", "penalty_eur"]
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="", encoding="utf-8") as csv_file:
            csv.writer(csv_file).writerow(expected_header)
        return

    with CSV_PATH.open("r", newline="", encoding="utf-8") as csv_file:
        rows = list(csv.reader(csv_file))

    if not rows:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as csv_file:
            csv.writer(csv_file).writerow(expected_header)
        return

    current_header = rows[0]
    if current_header == expected_header:
        return

    if current_header == ["date", "game", "hours", "penalty_eur"]:
        migrated_rows = [expected_header]
        for row in rows[1:]:
            if len(row) < 4:
                continue
            migrated_rows.append([row[0], row[1], row[2], row[2], "0.00", row[3]])
        with CSV_PATH.open("w", newline="", encoding="utf-8") as csv_file:
            csv.writer(csv_file).writerows(migrated_rows)
        return

    raise ValueError(f"Unsupported CSV format in {CSV_PATH}")


def export_sessions_to_csv() -> None:
    ensure_database()
    ensure_csv_header()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT ended_at, game_name, running_seconds, foreground_seconds, background_seconds, penalty_eur
            FROM sessions
            ORDER BY ended_at
            """
        ).fetchall()

    temp_path = CSV_PATH.with_suffix(".csv.tmp")
    try:
        with temp_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["date", "game", "running_hours", "foreground_hours", "background_hours", "penalty_eur"])
            for ended_at, game_name, running_seconds, foreground_seconds, background_seconds, penalty_eur in rows:
                date_value = ended_at.split("T", 1)[0] if ended_at else datetime.now().strftime("%Y-%m-%d")
                writer.writerow(
                    [
                        date_value,
                        game_name,
                        f"{running_seconds / 3600:.2f}",
                        f"{foreground_seconds / 3600:.2f}",
                        f"{background_seconds / 3600:.2f}",
                        f"{penalty_eur:.2f}",
                    ]
                )
        temp_path.replace(CSV_PATH)
    except PermissionError:
        logging.warning("CSV export skipped because %s is locked by another program.", CSV_PATH)
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def manual_export_sessions_to_csv() -> tuple[bool, str]:
    try:
        export_sessions_to_csv()
        return True, f"CSV exported to {CSV_PATH}"
    except Exception as exc:
        return False, str(exc)


def ensure_database() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                running_seconds INTEGER NOT NULL,
                foreground_seconds INTEGER NOT NULL,
                background_seconds INTEGER NOT NULL,
                penalty_eur REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                game_name TEXT,
                payload TEXT
            )
            """
        )
        conn.commit()


def record_session(
    game_name: str,
    started_at: str,
    ended_at: str,
    running_seconds: int,
    foreground_seconds: int,
    background_seconds: int,
) -> None:
    ensure_database()
    penalty = calculate_penalty_eur(foreground_seconds, background_seconds)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                game_name, started_at, ended_at, running_seconds, foreground_seconds, background_seconds, penalty_eur
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_name,
                started_at,
                ended_at,
                int(running_seconds),
                int(foreground_seconds),
                int(background_seconds),
                penalty,
            ),
        )
        conn.commit()
    export_sessions_to_csv()


def log_event(event_type: str, game_name: Optional[str] = None, payload: Optional[dict] = None) -> None:
    ensure_database()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO event_log (created_at, event_type, game_name, payload) VALUES (?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                event_type,
                game_name,
                json.dumps(payload or {}, ensure_ascii=True),
            ),
        )
        conn.commit()


def _period_start(period: str) -> Optional[str]:
    now = datetime.now()
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if period == "week":
        monday = now - timedelta(days=now.weekday())
        return monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(timespec="seconds")
    return None


def load_stats(period: str = "all") -> dict:
    ensure_database()
    where_sql = ""
    params: tuple = ()
    start = _period_start(period)
    if start:
        where_sql = "WHERE ended_at >= ?"
        params = (start,)

    with sqlite3.connect(DB_PATH) as conn:
        total_row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(running_seconds), 0),
                COALESCE(SUM(foreground_seconds), 0),
                COALESCE(SUM(background_seconds), 0),
                COALESCE(SUM(penalty_eur), 0),
                COUNT(*)
            FROM sessions
            {where_sql}
            """,
            params,
        ).fetchone()
        by_game_rows = conn.execute(
            f"""
            SELECT
                game_name,
                COALESCE(SUM(running_seconds), 0),
                COALESCE(SUM(foreground_seconds), 0),
                COALESCE(SUM(background_seconds), 0),
                COALESCE(SUM(penalty_eur), 0)
            FROM sessions
            {where_sql}
            GROUP BY game_name
            ORDER BY SUM(running_seconds) DESC, game_name
            """,
            params,
        ).fetchall()
        daily_rows = conn.execute(
            f"""
            SELECT
                substr(ended_at, 1, 10) AS day,
                COALESCE(SUM(running_seconds), 0),
                COALESCE(SUM(penalty_eur), 0)
            FROM sessions
            {where_sql}
            GROUP BY day
            ORDER BY day DESC
            LIMIT 14
            """,
            params,
        ).fetchall()

    running_seconds, foreground_seconds, background_seconds, penalty, sessions = total_row
    return {
        "running_hours": round(running_seconds / 3600, 2),
        "foreground_hours": round(foreground_seconds / 3600, 2),
        "background_hours": round(background_seconds / 3600, 2),
        "total_penalty": round(float(penalty or 0), 2),
        "sessions": int(sessions or 0),
        "by_game": {
            row[0]: {
                "running_hours": round(row[1] / 3600, 2),
                "foreground_hours": round(row[2] / 3600, 2),
                "background_hours": round(row[3] / 3600, 2),
                "penalty_eur": round(float(row[4] or 0), 2),
            }
            for row in by_game_rows
        },
        "daily": [
            {"day": row[0], "running_hours": round(row[1] / 3600, 2), "penalty_eur": round(float(row[2] or 0), 2)}
            for row in daily_rows
        ],
    }


def load_stats_from_csv() -> dict:
    return load_stats("all")


def load_recent_sessions(limit: int = 25) -> list[dict]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT game_name, started_at, ended_at, running_seconds, foreground_seconds, background_seconds, penalty_eur
            FROM sessions
            ORDER BY ended_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "game_name": row[0],
            "started_at": row[1],
            "ended_at": row[2],
            "running_seconds": row[3],
            "foreground_seconds": row[4],
            "background_seconds": row[5],
            "penalty_eur": round(float(row[6] or 0), 2),
        }
        for row in rows
    ]


def normalize(value: str) -> str:
    return value.strip().lower()


def format_duration(seconds: int) -> str:
    duration = timedelta(seconds=seconds)
    total_minutes = int(duration.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m"


def format_duration_compact(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def calculate_penalty_eur(foreground_seconds: int, background_seconds: int) -> float:
    foreground_penalty = (foreground_seconds / 3600) * PENALTY_PER_HOUR
    background_penalty = (background_seconds / 3600) * BACKGROUND_PENALTY_PER_HOUR
    return round(foreground_penalty + background_penalty, 2)


def get_foreground_process_info() -> tuple[Optional[str], Optional[str]]:
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None, None

    title = win32gui.GetWindowText(hwnd) or ""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if not pid:
        return None, title

    try:
        process_name = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None, title

    return process_name, title


def list_top_level_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    def callback(hwnd, _lparam):
        if not win32gui.IsWindow(hwnd):
            return True
        if not win32gui.IsWindowVisible(hwnd):
            return True

        title = (win32gui.GetWindowText(hwnd) or "").strip()
        if not title:
            return True

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return True
            process_name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True

        windows.append(WindowInfo(process_name=process_name, window_title=title))
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def build_process_index() -> set[str]:
    names: set[str] = set()
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info.get("name")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name:
            names.add(normalize(name))
    return names


def detect_running_games(config: dict, process_index: set[str]) -> dict[str, set[str]]:
    running_games: dict[str, set[str]] = {}
    for entry in config.get("games", []):
        process_names = {normalize(item) for item in entry.get("process_names", [])}
        matched = process_names.intersection(process_index)
        if matched:
            running_games[entry["name"]] = set(matched)
    return running_games


def detect_games_from_open_windows(config: dict, windows: list[WindowInfo]) -> dict[str, set[str]]:
    matches: dict[str, set[str]] = {}
    excluded_processes = {normalize(item) for item in config.get("excluded_processes", DEFAULT_EXCLUDED_PROCESSES)}
    excluded_window_keywords = [normalize(item) for item in config.get("excluded_window_keywords", DEFAULT_EXCLUDED_WINDOW_KEYWORDS)]
    launcher_processes = {normalize(item) for item in config.get("launcher_processes", [])}

    for window in windows:
        process_name = normalize(window.process_name)
        window_title = normalize(window.window_title)
        if process_name in excluded_processes:
            continue
        if any(keyword in window_title for keyword in excluded_window_keywords):
            continue
        if launcher_processes and process_name not in launcher_processes:
            continue

        for entry in config.get("games", []):
            title_keywords = [normalize(item) for item in entry.get("title_keywords", [])]
            if title_keywords and any(keyword in window_title for keyword in title_keywords):
                matches.setdefault(entry["name"], set()).add(process_name)

    return matches


def detect_foreground_game(config: dict, process_name: Optional[str], window_title: Optional[str]) -> Optional[DetectionResult]:
    if not process_name and not window_title:
        return None

    process_name = normalize(process_name or "")
    window_title = normalize(window_title or "")
    excluded_processes = {normalize(item) for item in config.get("excluded_processes", DEFAULT_EXCLUDED_PROCESSES)}
    excluded_window_keywords = [normalize(item) for item in config.get("excluded_window_keywords", DEFAULT_EXCLUDED_WINDOW_KEYWORDS)]
    allow_title_match = process_name not in excluded_processes and not any(keyword in window_title for keyword in excluded_window_keywords)

    for entry in config.get("games", []):
        process_names = [normalize(item) for item in entry.get("process_names", [])]
        title_keywords = [normalize(item) for item in entry.get("title_keywords", [])]

        if process_name and process_name in process_names:
            return DetectionResult(entry["name"], process_name, window_title, "process_name")
        if allow_title_match and window_title and any(keyword in window_title for keyword in title_keywords):
            return DetectionResult(entry["name"], process_name, window_title, "window_title")

    launchers = [normalize(item) for item in config.get("launcher_processes", [])]
    if process_name in launchers and window_title:
        for entry in config.get("games", []):
            title_keywords = [normalize(item) for item in entry.get("title_keywords", [])]
            if any(keyword in window_title for keyword in title_keywords):
                return DetectionResult(entry["name"], process_name, window_title, "launcher_window_title")
    return None


def build_started_message(game_name: str) -> str:
    return f"Game started: {game_name}"


def build_stopped_message(game_name: str, running_seconds: int, foreground_seconds: int, background_seconds: int) -> str:
    penalty = calculate_penalty_eur(foreground_seconds, background_seconds)
    return (
        f"Game stopped: {game_name}\n"
        f"Running: {format_duration_compact(running_seconds)}\n"
        f"Foreground: {format_duration_compact(foreground_seconds)}\n"
        f"Background: {format_duration_compact(background_seconds)}\n"
        f"Penalty: {penalty:.2f} EUR"
    )


def build_focus_message(game_name: str, state: str) -> str:
    if state == "foreground":
        return f"Game in foreground: {game_name}"
    return f"Game moved to background: {game_name}"


def send_telegram_message(config: dict, text: str) -> None:
    telegram = config.get("telegram", {})
    bot_token = telegram.get("bot_token")
    chat_id = telegram.get("chat_id")
    if not bot_token or not chat_id or "replace_with" in bot_token:
        logging.debug("Telegram credentials are missing; skipping message.")
        return

    payload = parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    req = request.Request(url, data=payload, method="POST")
    try:
        with request.urlopen(req, timeout=15) as response:
            response.read()
    except error.URLError as exc:
        logging.warning("Failed to send Telegram message: %s", exc)


def telegram_api_request(config: dict, method: str, payload: Optional[dict] = None) -> dict:
    telegram = config.get("telegram", {})
    bot_token = telegram.get("bot_token")
    if not bot_token or "replace_with" in bot_token:
        return {"ok": False, "result": []}

    payload = payload or {}
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = parse.urlencode(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logging.warning("Telegram API %s failed: %s", method, exc)
        return {"ok": False, "result": []}

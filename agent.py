import logging
import os
import time
from datetime import datetime

import psutil

from tracker_core import (
    ACTIVE_SESSIONS_PATH,
    CONFIG_PATH,
    CONTROL_PATH,
    DEFAULT_EXCLUDED_PROCESSES,
    LOCK_PATH,
    STATE_PATH,
    build_focus_message,
    build_process_index,
    build_started_message,
    build_stopped_message,
    calculate_penalty_eur,
    clear_active_sessions_snapshot,
    default_state,
    detect_foreground_game,
    detect_games_from_open_windows,
    detect_running_games,
    ensure_config_exists,
    ensure_control_exists,
    ensure_csv_header,
    ensure_database,
    get_foreground_process_info,
    list_top_level_windows,
    load_active_sessions_snapshot,
    load_config,
    load_control,
    load_recent_sessions,
    load_stats,
    log_event,
    record_session,
    save_active_sessions_snapshot,
    save_control,
    save_json,
    send_telegram_message,
    setup_logging,
    telegram_api_request,
    format_duration_compact,
)


class BackgroundAgent:
    def __init__(self) -> None:
        self.config = load_config()
        self.state = default_state()
        self.sessions: dict[str, dict] = {}
        self.last_foreground_game: str | None = None
        self.last_config_mtime = 0.0
        self.last_control_mtime = 0.0
        self.was_paused = False
        self.lock_acquired = False

    def recover_interrupted_sessions(self) -> None:
        snapshot = load_active_sessions_snapshot()
        sessions = snapshot.get("sessions", {})
        if not sessions:
            return

        ended_at = datetime.now().isoformat(timespec="seconds")
        for game_name, session in sessions.items():
            started_at = session.get("started_at") or ended_at
            running_seconds = int(session.get("running_seconds", 0))
            foreground_seconds = int(session.get("foreground_seconds", 0))
            background_seconds = int(session.get("background_seconds", 0))
            if running_seconds <= 0:
                continue
            record_session(
                game_name,
                started_at,
                ended_at,
                running_seconds,
                foreground_seconds,
                background_seconds,
            )
            log_event(
                "interrupted",
                game_name,
                {
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "running_seconds": running_seconds,
                    "foreground_seconds": foreground_seconds,
                    "background_seconds": background_seconds,
                },
            )
        clear_active_sessions_snapshot()

    def persist_active_sessions(self) -> None:
        payload = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "sessions": {
                game_name: {
                    "started_at": session.get("started_at"),
                    "running_seconds": int(session.get("running_seconds", 0)),
                    "foreground_seconds": int(session.get("foreground_seconds", 0)),
                    "background_seconds": int(session.get("background_seconds", 0)),
                }
                for game_name, session in self.sessions.items()
            },
        }
        save_active_sessions_snapshot(payload)

    def acquire_lock(self) -> bool:
        if LOCK_PATH.exists():
            try:
                existing_pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                existing_pid = 0

            if existing_pid and existing_pid != os.getpid() and psutil.pid_exists(existing_pid):
                logging.info("Another agent is already running with pid %s", existing_pid)
                return False

            try:
                LOCK_PATH.unlink()
            except OSError:
                pass

        LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
        self.lock_acquired = True
        return True

    def release_lock(self) -> None:
        if not self.lock_acquired:
            return
        try:
            if LOCK_PATH.exists() and LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
                LOCK_PATH.unlink()
        except OSError:
            pass
        self.lock_acquired = False

    def reload_if_changed(self) -> None:
        config_mtime = os.path.getmtime(CONFIG_PATH)
        if config_mtime != self.last_config_mtime:
            self.config = load_config()
            self.last_config_mtime = config_mtime

        control_mtime = os.path.getmtime(CONTROL_PATH)
        if control_mtime != self.last_control_mtime:
            control = load_control()
            self.state["paused"] = bool(control.get("paused", False))
            self.last_control_mtime = control_mtime

    def handle_telegram_commands(self) -> None:
        settings = self.config.get("settings", {})
        if not settings.get("telegram_commands_enabled", True):
            return

        control = load_control()
        offset = int(control.get("telegram_offset", 0))
        response = telegram_api_request(self.config, "getUpdates", {"offset": offset + 1, "timeout": 0})
        if not response.get("ok"):
            return

        allowed_chat = str(self.config.get("telegram", {}).get("chat_id", "")).strip()
        for item in response.get("result", []):
            update_id = int(item.get("update_id", 0))
            message = item.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id", ""))
            text = (message.get("text") or "").strip()
            if not text or chat_id != allowed_chat:
                offset = max(offset, update_id)
                continue

            reply = self.build_command_reply(text)
            if reply:
                send_telegram_message(self.config, reply)
            offset = max(offset, update_id)

        control["telegram_offset"] = offset
        save_control(control)

    def build_command_reply(self, text: str) -> str:
        command = text.split()[0].lower()
        if command == "/help":
            return (
                "Available commands:\n"
                "/status - live agent state\n"
                "/today - today's totals\n"
                "/week - this week's totals\n"
                "/topgames - top tracked games\n"
                "/recent - recent sessions\n"
                "/pause - pause tracking\n"
                "/resume - resume tracking"
            )
        if command == "/status":
            totals = self.state.get("totals", {})
            active_games = ", ".join(sorted(self.state.get("games", {}).keys())) or "none"
            return (
                f"Agent: {'paused' if self.state.get('paused') else 'live'}\n"
                f"Active game: {self.state.get('active_game') or 'none'}\n"
                f"Tracked games: {active_games}\n"
                f"Running: {format_duration_compact(int(totals.get('running_seconds', 0)))}\n"
                f"Foreground: {format_duration_compact(int(totals.get('foreground_seconds', 0)))}\n"
                f"Background: {format_duration_compact(int(totals.get('background_seconds', 0)))}\n"
                f"Penalty: {float(totals.get('penalty_eur', 0)):.2f} EUR"
            )
        if command == "/today":
            stats = load_stats("today")
            return self.render_stats_reply("Today", stats)
        if command == "/week":
            stats = load_stats("week")
            return self.render_stats_reply("This week", stats)
        if command == "/topgames":
            stats = load_stats("all")
            if not stats["by_game"]:
                return "No tracked game history yet."
            lines = ["Top games:"]
            for idx, (game_name, payload) in enumerate(list(stats["by_game"].items())[:5], start=1):
                lines.append(
                    f"{idx}. {game_name} - {payload['running_hours']:.2f}h, {payload['penalty_eur']:.2f} EUR"
                )
            return "\n".join(lines)
        if command == "/pause":
            control = load_control()
            control["paused"] = True
            save_control(control)
            self.state["paused"] = True
            return "Agent paused."
        if command == "/resume":
            control = load_control()
            control["paused"] = False
            save_control(control)
            self.state["paused"] = False
            return "Agent resumed."
        if command == "/recent":
            sessions = load_recent_sessions(5)
            if not sessions:
                return "No recorded sessions yet."
            lines = ["Recent sessions:"]
            for session in sessions:
                lines.append(
                    f"{session['game_name']}: {format_duration_compact(session['running_seconds'])} "
                    f"({session['ended_at'].replace('T', ' ')})"
                )
            return "\n".join(lines)
        if command.startswith("/"):
            return "Unknown command. Send /help to see available commands."
        return ""

    def render_stats_reply(self, label: str, stats: dict) -> str:
        if not stats["sessions"]:
            return f"{label}: no sessions recorded."
        top_game = next(iter(stats["by_game"]), "none")
        return (
            f"{label}\n"
            f"Sessions: {stats['sessions']}\n"
            f"Running: {stats['running_hours']:.2f}h\n"
            f"Foreground: {stats['foreground_hours']:.2f}h\n"
            f"Background: {stats['background_hours']:.2f}h\n"
            f"Penalty: {stats['total_penalty']:.2f} EUR\n"
            f"Top game: {top_game}"
        )

    def emit_events(self, running_games: set[str], foreground_game: str | None) -> None:
        if not self.config.get("settings", {}).get("telegram_on_state_change", True):
            self.last_foreground_game = foreground_game
            return

        for game_name in sorted(running_games):
            if game_name not in self.sessions:
                send_telegram_message(self.config, build_started_message(game_name))
                log_event("started", game_name)

        if foreground_game != self.last_foreground_game:
            if self.last_foreground_game:
                send_telegram_message(self.config, build_focus_message(self.last_foreground_game, "background"))
                log_event("background", self.last_foreground_game)
            if foreground_game:
                send_telegram_message(self.config, build_focus_message(foreground_game, "foreground"))
                log_event("foreground", foreground_game)

        self.last_foreground_game = foreground_game

    def extend_running_games_with_session_processes(self, running_games: dict[str, set[str]], process_index: set[str]) -> dict[str, set[str]]:
        extended = {game_name: set(processes) for game_name, processes in running_games.items()}
        for game_name, session in self.sessions.items():
            observed_processes = {name for name in session.get("observed_processes", []) if name in process_index}
            if observed_processes:
                extended.setdefault(game_name, set()).update(observed_processes)
        return extended

    def update_game_seconds(
        self,
        running_games: dict[str, set[str]],
        foreground_game: str | None,
        foreground_process: str | None,
        interval_seconds: int,
    ) -> None:
        track_background = bool(self.config.get("settings", {}).get("track_background_games", True))
        track_foreground = bool(self.config.get("settings", {}).get("track_foreground_games", True))
        excluded_processes = {item.lower() for item in self.config.get("excluded_processes", DEFAULT_EXCLUDED_PROCESSES)}

        for game_name in sorted(running_games):
            session = self.sessions.setdefault(
                game_name,
                {
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "running_seconds": 0,
                    "foreground_seconds": 0,
                    "background_seconds": 0,
                    "observed_processes": [],
                    "missing_polls": 0,
                },
            )
            observed_processes = {name.lower() for name in session.get("observed_processes", [])}
            observed_processes.update(running_games.get(game_name, set()))
            if foreground_game == game_name and foreground_process:
                normalized_process = foreground_process.lower()
                if normalized_process not in excluded_processes:
                    observed_processes.add(normalized_process)
            session["observed_processes"] = sorted(observed_processes)
            session["missing_polls"] = 0
            session["running_seconds"] += interval_seconds

            if foreground_game == game_name and track_foreground:
                session["foreground_seconds"] += interval_seconds
            elif track_background:
                session["background_seconds"] += interval_seconds

        grace_polls = int(self.config.get("settings", {}).get("close_grace_polls", 2))
        grace_polls = max(1, min(grace_polls, 6))
        for game_name in [name for name in list(self.sessions) if name not in running_games]:
            session = self.sessions.get(game_name)
            if not session:
                continue
            session["missing_polls"] = int(session.get("missing_polls", 0)) + 1
            if session["missing_polls"] >= grace_polls:
                self.flush_game(game_name)

    def flush_game(self, game_name: str) -> None:
        session = self.sessions.pop(game_name, None)
        if not session:
            return

        ended_at = datetime.now().isoformat(timespec="seconds")
        record_session(
            game_name,
            session["started_at"],
            ended_at,
            session["running_seconds"],
            session["foreground_seconds"],
            session["background_seconds"],
        )
        if self.config.get("settings", {}).get("telegram_on_state_change", True):
            send_telegram_message(
                self.config,
                build_stopped_message(
                    game_name,
                    session["running_seconds"],
                    session["foreground_seconds"],
                    session["background_seconds"],
                ),
            )
        log_event(
            "stopped",
            game_name,
            {
                "started_at": session["started_at"],
                "ended_at": ended_at,
                "running_seconds": session["running_seconds"],
                "foreground_seconds": session["foreground_seconds"],
                "background_seconds": session["background_seconds"],
            },
        )
        logging.info("Flushed session for %s", game_name)
        self.persist_active_sessions()

    def flush_all_sessions(self) -> None:
        for game_name in list(self.sessions):
            self.flush_game(game_name)
        if not self.sessions:
            clear_active_sessions_snapshot()

    def rebuild_state(self, foreground_process: str | None, foreground_window: str | None, foreground_game: str | None) -> None:
        games_payload = {}
        running_total = 0
        foreground_total = 0
        background_total = 0

        for game_name, session in self.sessions.items():
            running_seconds = int(session["running_seconds"])
            foreground_seconds = int(session["foreground_seconds"])
            background_seconds = int(session["background_seconds"])
            penalty = calculate_penalty_eur(foreground_seconds, background_seconds)
            games_payload[game_name] = {
                "started_at": session["started_at"],
                "running_seconds": running_seconds,
                "foreground_seconds": foreground_seconds,
                "background_seconds": background_seconds,
                "state": "foreground" if foreground_game == game_name else "background",
                "penalty_eur": penalty,
                "observed_processes": session.get("observed_processes", []),
            }
            running_total += running_seconds
            foreground_total += foreground_seconds
            background_total += background_seconds

        self.state.update(
            {
                "agent_running": True,
                "paused": bool(self.state.get("paused", False)),
                "agent_pid": os.getpid(),
                "last_seen_at": datetime.now().isoformat(timespec="seconds"),
                "poll_interval_seconds": int(self.config.get("settings", {}).get("poll_interval_seconds", 10)),
                "active_game": foreground_game,
                "foreground_process": foreground_process or "",
                "foreground_window": foreground_window or "",
                "games": games_payload,
                "totals": {
                    "running_seconds": running_total,
                    "foreground_seconds": foreground_total,
                    "background_seconds": background_total,
                    "penalty_eur": calculate_penalty_eur(foreground_total, background_total),
                },
            }
        )
        save_json(STATE_PATH, self.state)

    def run(self) -> None:
        ensure_config_exists()
        ensure_control_exists()
        ensure_csv_header()
        ensure_database()
        if not self.acquire_lock():
            return
        self.recover_interrupted_sessions()
        self.last_config_mtime = os.path.getmtime(CONFIG_PATH)
        self.last_control_mtime = os.path.getmtime(CONTROL_PATH)
        self.state["paused"] = bool(load_control().get("paused", False))
        logging.info("Background agent started with pid %s", os.getpid())

        try:
            while True:
                try:
                    self.reload_if_changed()
                    self.handle_telegram_commands()
                    if self.state.get("paused") and not self.was_paused:
                        self.flush_all_sessions()
                        self.last_foreground_game = None
                    self.was_paused = bool(self.state.get("paused"))

                    interval = int(self.config.get("settings", {}).get("poll_interval_seconds", 10))
                    interval = max(5, min(interval, 60))

                    foreground_process, foreground_window = get_foreground_process_info()
                    process_index = build_process_index()
                    running_games = detect_running_games(self.config, process_index)
                    window_matches = detect_games_from_open_windows(self.config, list_top_level_windows())
                    for game_name, processes in window_matches.items():
                        running_games.setdefault(game_name, set()).update(processes)
                    foreground_detection = detect_foreground_game(self.config, foreground_process, foreground_window)
                    foreground_game = foreground_detection.game_name if foreground_detection else None

                    if foreground_game:
                        running_games.setdefault(foreground_game, set())
                        if foreground_process:
                            normalized_process = foreground_process.lower()
                            excluded_processes = {
                                item.lower() for item in self.config.get("excluded_processes", DEFAULT_EXCLUDED_PROCESSES)
                            }
                            if normalized_process not in excluded_processes:
                                running_games[foreground_game].add(normalized_process)

                    if not self.state.get("paused"):
                        running_games = self.extend_running_games_with_session_processes(running_games, process_index)
                        self.emit_events(set(running_games.keys()), foreground_game)
                        self.update_game_seconds(running_games, foreground_game, foreground_process, interval)
                        self.persist_active_sessions()
                    else:
                        self.persist_active_sessions()

                    self.state["last_error"] = ""
                    self.rebuild_state(foreground_process, foreground_window, foreground_game)
                    time.sleep(interval)
                except Exception as exc:
                    logging.exception("Background agent failed: %s", exc)
                    self.state["last_error"] = str(exc)
                    self.state["agent_running"] = True
                    save_json(STATE_PATH, self.state)
                    time.sleep(5)
        finally:
            if self.sessions:
                self.persist_active_sessions()
            else:
                clear_active_sessions_snapshot()
            self.release_lock()


def main() -> None:
    setup_logging()
    agent = BackgroundAgent()
    agent.run()


if __name__ == "__main__":
    main()

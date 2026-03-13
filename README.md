# No Game Tracker

<p align="center">
  <img src="assets/app_icon.png" alt="No Game Tracker icon" width="128" height="128">
</p>

<p align="center">
  Windows background game tracker with Telegram alerts, SQLite history, reports, tray UI, and foreground/background penalty tracking.
</p>

## Overview

No Game Tracker is a Windows-first tracker with two separate parts:

- `agent.py` runs as the always-on background agent
- `app.py` is the control panel UI

It can start with Windows, detect games that are active or just left open in the background, log sessions to SQLite and CSV, and send updates to Telegram.

## Highlights

- Windows autostart through Task Scheduler
- Separate background agent and control-panel UI
- System tray support for the UI
- SQLite history database plus CSV export
- Low-overhead polling every 10 seconds by default
- Detection of games that are merely running in the background
- Detection of games that are active in the foreground
- Per-game counters for `running`, `foreground`, and `background` time
- Telegram notifications for start, stop, and focus changes
- Telegram commands: `/help`, `/status`, `/today`, `/week`, `/topgames`, `/pause`, `/resume`, `/recent`
- Game editor in the UI
- Reports tab with recent sessions and a 14-day chart
- Extra filters for excluded processes and excluded window keywords
- PyInstaller build script for `.exe`
- Recovery of interrupted sessions after shutdown or reboot

## Penalty Rules

- Foreground: `120 EUR/hour (60 each)`
- Background: `12 EUR/hour`

## Quick Start

1. Install Python 3.11+ on Windows.
2. Install runtime dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `config.example.json` to `config.json`.
4. Fill in your Telegram bot token and chat ID in `config.json`.
5. Start the UI:

```powershell
py app.py
```

6. Start the background agent manually if you want to test without autostart:

```powershell
py agent.py
```

For a shorter local guide, see `QUICK_START.txt`.

## UI

The control panel lets you:

- see whether the background agent is alive
- pause or resume tracking
- inspect current tracked games
- view running, foreground, background, and penalty counters
- edit Telegram settings, filters, and games
- view recent sessions and charts
- export CSV manually
- minimize to the system tray

## Telegram Commands

Send these commands to your bot from the configured `chat_id`:

- `/help`
- `/status`
- `/today`
- `/week`
- `/topgames`
- `/pause`
- `/resume`
- `/recent`

## Configuration Notes

- `settings.poll_interval_seconds`: default `10`
- `settings.close_grace_polls`: default `2`
- `excluded_processes`: blocks title-based false positives from normal apps
- `excluded_window_keywords`: blocks title-based false positives from common window titles

Game rules can be edited in the UI Game Editor or directly in `config.json`.

## Data Files

The project writes:

- `tracker.db` for SQLite history
- `game_log.csv` for exported finished sessions
- `state.json` for live agent state used by the UI
- `control.json` for pause/resume and Telegram update offset
- `active_sessions.json` for interrupted-session recovery
- `tracker.log` for runtime logs and errors

## Enable Autostart

Run PowerShell as Administrator and execute:

```powershell
.\install_autostart.ps1
```

This creates a scheduled task named `NoGameTracker` that starts the background agent at user logon.

## Build Executables

```powershell
.\build_exe.ps1
```

This creates:

- `dist\NoGameTrackerUI\NoGameTrackerUI.exe`
- `dist\NoGameTrackerAgent\NoGameTrackerAgent.exe`

The build uses `assets\app_icon.ico` for the Windows executable icon.

## Create Desktop Shortcut

```powershell
.\create_desktop_shortcut.ps1
```

This creates a `No Game Tracker` shortcut on your desktop that points to the built UI executable.

## Public Repository Safety

- Do not publish your real `config.json`
- Do not publish `tracker.db`, `game_log.csv`, logs, or state files
- Commit only `config.example.json` as the public-safe template
- If you ever exposed your Telegram bot token, revoke it in `@BotFather` and generate a new one

## Notes

- The background agent keeps working even when the UI is closed.
- Multiple games can be tracked at once. Only one can be `foreground`; the rest are `background`.
- Launcher-assisted title matching is limited to supported launcher processes.
- Finished sessions are stored in SQLite and exported to CSV.
- If Windows shuts down during a game, the next agent start recovers the unfinished session instead of losing it.

## License

MIT. See `LICENSE`.

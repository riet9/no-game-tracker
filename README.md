# No Game Tracker

Windows game tracker with two separate parts:

- `agent.py` is the always-on background agent
- `app.py` is the control panel UI

The agent starts with Windows, tracks multiple games at once, stores history in SQLite, exports CSV, logs to Telegram, and can be controlled from Telegram commands and the UI.

## Features

- Windows autostart through Task Scheduler
- Separate background agent and control-panel UI
- System tray support for the UI
- SQLite history database plus CSV export
- Low-overhead polling every 10 seconds by default
- Detection of games that are merely running in the background
- Detection of games that are active in the foreground
- Per-game counters for running, foreground, and background time
- Telegram notifications for start, stop, and focus changes
- Telegram commands: `/status`, `/today`, `/week`, `/pause`, `/resume`, `/recent`
- Normal game editor in the UI
- Reports tab with recent sessions and a 14-day chart
- Extra filters for excluded processes and excluded window keywords
- PyInstaller build script for `.exe`

## Setup

1. Install Python 3.11+ on Windows.
2. Install runtime dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `config.example.json` to `config.json`.
4. Fill in your Telegram bot token and chat ID in `config.json`.
5. Adjust `settings.poll_interval_seconds` if needed. `10` seconds is a good low-load default.
6. Adjust `settings.close_grace_polls` if you want faster or slower stop detection. `2` means about 20 seconds with the default poll interval.
7. Use `excluded_processes` and `excluded_window_keywords` to block title-based detection for work apps and normal windows.
8. Edit your games in the UI Game Editor tab or directly in `config.json`.

## Before Publishing

- Do not publish your real `config.json`
- Do not publish `tracker.db`, `game_log.csv`, logs, or state files
- Commit only `config.example.json` as the public-safe template
- If you ever exposed your Telegram bot token, revoke it in `@BotFather` and generate a new one

## Run The UI

```powershell
py app.py
```

The UI lets you:

- see whether the background agent is alive
- pause or resume tracking
- inspect current tracked games
- view running/background/foreground timers and penalty
- edit Telegram settings, filters, and games
- view recent sessions and charts
- minimize to the system tray
- see the current penalty rule: foreground `120 EUR/hour (60 each)`, background `12 EUR/hour`

## Run The Background Agent Manually

```powershell
py agent.py
```

Usually you should let Task Scheduler start it automatically.

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

## Data Files

The project writes:

- `tracker.db` for SQLite history
- `game_log.csv` for exported finished sessions
- `state.json` for the live agent state used by the UI
- `control.json` for pause/resume and Telegram update offset
- `tracker.log` for runtime logs and errors

## Enable Autostart

Run PowerShell as Administrator and execute:

```powershell
.\install_autostart.ps1
```

This creates a scheduled task named `NoGameTracker` that starts the background agent at user logon.

## Build .exe

```powershell
.\build_exe.ps1
```

This creates:

- `dist\NoGameTrackerUI\NoGameTrackerUI.exe`
- `dist\NoGameTrackerAgent\NoGameTrackerAgent.exe`

The build uses `assets\app_icon.ico` for the Windows executable icon when that file exists.

## Create Desktop Shortcut

```powershell
.\create_desktop_shortcut.ps1
```

This creates a `No Game Tracker` shortcut on your desktop that points to the built UI exe.

## Notes

- The background agent keeps working even when the UI is closed.
- Multiple games can be tracked at once. Only one can be `foreground`; the rest are `background`.
- Launcher detection still uses title matching when the foreground process is `steam.exe` or `epicgameslauncher.exe`.
- For low CPU usage, the agent sleeps between checks and only writes heartbeat/session updates.
- Finished sessions are stored in SQLite and exported to CSV automatically.
- If Windows shuts down during a game, the next agent start recovers the unfinished session as interrupted instead of losing it.

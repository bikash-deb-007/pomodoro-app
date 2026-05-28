# Pomodoro Timer

A minimalist, always-on-top Pomodoro timer for Windows. Lives in the top-right corner of your screen, collapses to a tiny bubble when you're not looking at it, and gets out of your way.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue) ![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey) ![License](https://img.shields.io/badge/License-MIT-green)

## Features

- **Collapse to bubble** — hover to expand, move away to collapse into a small progress ring
- **Always on top** — never loses focus behind other windows
- **Timer presets** — Classic (25/5), Deep Work (50/10), Sprint (15/3), Marathon (60/15)
- **Custom preset** — right-click → Custom... to set any work/break duration
- **Session persistence** — timer state and session count survive app restarts
- **Daily stats** — right-click shows today's completed session count
- **Taskbar progress** — countdown shown in the Windows taskbar thumbnail
- **Session tracking** — dot indicators count completed work sessions; auto-triggers long break every 4 sessions
- **Smooth animations** — color transitions between work / short break / long break modes
- **Custom chime** — synthesized 3-tone chime on completion, no audio files needed
- **Single instance** — Windows mutex prevents duplicate timers
- **Resizable** — scroll wheel to scale up or down
- **Draggable** — click and drag anywhere to reposition
- **Rounded corners** — native Windows 11 DWM rounded corners

## Controls

| Action | Control |
|--------|---------|
| Start / Pause / Resume | Double-click or Space |
| Timer presets + stats | Right-click |
| Custom preset | Right-click → Custom... |
| Resize | Scroll wheel |
| Move | Click and drag |
| Quit | Middle-click or Escape |

## Requirements

- Windows 10 / 11
- Python 3.8+ with tkinter (included in the standard Python installer)

No third-party packages needed.

## Installation

1. Install Python from [python.org](https://www.python.org/downloads/) if you haven't already. Check **"Add Python to PATH"** during install.
2. Download or clone this repo.
3. Double-click `run_pomodoro.bat`.

That's it.

## Running directly

```bash
pythonw pomodoro.pyw
```

Using `pythonw` instead of `python` runs the app without a console window.

## Timer Modes

| Mode | Description |
|------|-------------|
| FOCUS | Work session (default 25 min) |
| BREAK | Short break (default 5 min) |
| LONG BREAK | Long break after every 4 sessions (default 15 min) |

Change the preset via right-click. Sessions reset when you switch presets.

## Project Structure

```
pomodoro_app/
├── pomodoro.pyw        # Main application
├── run_pomodoro.bat    # Windows launcher
├── .env.example        # Environment variable template
└── README.md
```

## License

MIT

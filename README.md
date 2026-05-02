# Claude Code → E-Ink Bridge

Pushes real-time Claude Code session data from [Claude HUD](https://github.com/jarrodwatts/claude-hud) to a [Zectrix](https://cloud.zectrix.com) e-ink display.

## Features

- **Multi-session Support** — Automatically tracks multiple concurrent Claude Code sessions and displays the most recently updated one.
- **Auto-Lifecycle Management** — Starts automatically when Claude Code is launched and cleanly exits 10 minutes after all sessions are closed.
- **Smart Push** — Only pushes to the Zectrix API when the rendered image actually changes, saving network requests.

## What it shows

- **Model name** — e.g. `[Opus 4.6]`
- **Context health** — progress bar + token count
- **Usage limits** — 5-hour and 7-day windows with reset timers
- **Git branch** — current branch and dirty state
- **Session duration**

## Setup

### 1. Install dependencies

```bash
cd claude-eink-bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.json config.json
```

Then edit `config.json` with your values:

```json
{
  "api_key": "YOUR_ZECTRIX_API_KEY",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "page_id": 5,
  "interval_seconds": 60,
  "font_path": "../resoukanban-main/font.ttf"
}
```

> **Tip (Sync Cycle):** For the best "HUD" experience, set the Zectrix e-ink device's polling cycle to **1 minute** and set `interval_seconds` to **60** (or 45). The script uses hash comparison to skip pushing if data hasn't changed, so a fast interval won't spam the API when idle.

### 3. Setup Auto-Start

The bridge is designed to run automatically alongside your coding sessions. Use the provided setup script to patch your Claude Code settings:

```bash
node setup-eink.mjs
```

This will wrap your Claude Code `statusLine` command. Now:
1. The bridge **starts automatically** when you run `claude`.
2. It pushes updates in the background.
3. It **exits automatically** 10 minutes after you close all Claude Code sessions.

*(To uninstall the wrapper, run `node setup-eink.mjs --undo`)*

### Manual Testing / Debugging

If you want to test the rendering or pushing manually:

```bash
source .venv/bin/activate

# Preview locally without pushing (saves to preview.png)
python main.py --preview

# Render and push once, then exit
python main.py --once
```

## Architecture

```
Claude Code 1 → Claude HUD → ~/.claude/.../eink-snapshots/{hash_1}.json
Claude Code 2 → Claude HUD → ~/.claude/.../eink-snapshots/{hash_2}.json
                                        ↓
                              claude-eink-bridge (picks newest)
                                        ↓
                              400×300 1-bit PNG
                                        ↓
                              Zectrix Cloud API → E-ink display
```

## Requirements

- Python 3.10+
- Claude HUD plugin (modified with snapshot writer)
- Zectrix e-ink device + cloud API key
- A `.ttf` font file — place it in the project root as `font.ttf`, or set an absolute path in `config.json`'s `font_path` field. Any CJK-capable font works (e.g. [Noto Sans CJK](https://fonts.google.com/noto/specimen/Noto+Sans+SC)).

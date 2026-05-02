# Claude Code → E-Ink Bridge

Pushes real-time Claude Code session data from [Claude HUD](https://github.com/jarrodwatts/claude-hud) to a [Zectrix](https://cloud.zectrix.com) e-ink display.

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

Edit `config.json`:

```json
{
  "api_key": "YOUR_ZECTRIX_API_KEY",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "page_id": 5,
  "interval_seconds": 300,
  "snapshot_path": "~/.claude/plugins/claude-hud/eink-snapshot.json",
  "font_path": "../resoukanban-main/font.ttf"
}
```

### 3. Run

```bash
# Continuous mode (pushes every 5 minutes)
source .venv/bin/activate
python main.py

# Single push
python main.py --once

# Preview only (no push)
python main.py --preview
```

## Architecture

```
Claude Code → stdin JSON → Claude HUD → eink-snapshot.json
                                              ↓
                                    claude-eink-bridge
                                              ↓
                                    400×300 1-bit PNG
                                              ↓
                                    Zectrix Cloud API → E-ink display
```

## Requirements

- Python 3.10+
- Claude HUD plugin (modified with snapshot writer)
- Zectrix e-ink device + cloud API key

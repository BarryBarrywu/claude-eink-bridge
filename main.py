#!/usr/bin/env python3
"""
Claude Code E-Ink Bridge

Reads per-session Claude HUD snapshots, picks the most recently active one,
renders a 400×300 1-bit dashboard, and pushes to a Zectrix e-ink device.

Supports multiple concurrent Claude Code sessions — always shows the
most recently updated one.

Usage:
    python main.py              # Run continuously (default 5-min interval)
    python main.py --once       # Single push then exit
    python main.py --preview    # Generate preview image only (no push)
"""

import json
import os
import sys
import signal
import atexit
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import requests


# ─── Configuration ───────────────────────────────────────────────────────────

PLUGIN_DIR = Path.home() / ".claude" / "plugins" / "claude-hud"
SNAPSHOTS_DIR = PLUGIN_DIR / "eink-snapshots"
PID_FILE = PLUGIN_DIR / "eink-bridge.pid"
# Stale threshold: ignore snapshots older than this (seconds)
STALE_THRESHOLD = 3600  # 1 hour


def load_config():
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        cfg = json.load(f)
    # Resolve font path relative to this script
    font_raw = cfg.get("font_path", "font.ttf")
    if not os.path.isabs(font_raw):
        font_raw = str(Path(__file__).parent / font_raw)
    cfg["font_path"] = font_raw
    return cfg


# ─── PID Management ─────────────────────────────────────────────────────────

def write_pid():
    """Write current PID so the HUD knows we're alive."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    """Clean up PID file on exit."""
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ─── Snapshot Reader (Multi-Session) ─────────────────────────────────────────

def load_latest_snapshot():
    """
    Read all per-session snapshot files and return the most recently updated.

    Each Claude Code instance writes its own file:
        ~/.claude/plugins/claude-hud/eink-snapshots/{hash}.json

    We pick the one with the newest timestamp.
    Also cleans up stale files (older than STALE_THRESHOLD).
    """
    if not SNAPSHOTS_DIR.exists():
        # Fallback: try legacy single-file location
        legacy = PLUGIN_DIR / "eink-snapshot.json"
        if legacy.exists():
            try:
                return json.loads(legacy.read_text())
            except Exception:
                pass
        return None

    now = time.time()
    latest = None
    latest_ts = ""

    for f in SNAPSHOTS_DIR.glob("*.json"):
        # Clean up stale snapshots
        try:
            age = now - f.stat().st_mtime
            if age > STALE_THRESHOLD:
                f.unlink(missing_ok=True)
                continue
        except Exception:
            continue

        try:
            data = json.loads(f.read_text())
            ts = data.get("timestamp", "")
            if ts > latest_ts:
                latest = data
                latest_ts = ts
        except Exception:
            continue

    return latest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1000}k"
    return str(n)


def format_reset_time(reset_iso):
    if not reset_iso:
        return None
    try:
        reset_dt = datetime.fromisoformat(reset_iso.replace("Z", "+00:00"))
        delta = reset_dt - datetime.now(timezone.utc)
        total_sec = int(delta.total_seconds())
        if total_sec <= 0:
            return "now"
        hours, remainder = divmod(total_sec, 3600)
        minutes = remainder // 60
        if hours >= 24:
            return f"{hours // 24}d"
        if hours > 0:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"
    except Exception:
        return None


def count_active_sessions():
    """Count how many non-stale snapshot files exist."""
    if not SNAPSHOTS_DIR.exists():
        return 0
    now = time.time()
    count = 0
    for f in SNAPSHOTS_DIR.glob("*.json"):
        try:
            if now - f.stat().st_mtime < STALE_THRESHOLD:
                count += 1
        except Exception:
            pass
    return count


# ─── Image Renderer ──────────────────────────────────────────────────────────

class EinkRenderer:
    W, H = 400, 300

    def __init__(self, font_path):
        # ── Font scale ──────────────────────────────────
        # #1  title          20px  "今天的Token用完了吗？"
        # #3  time           16px  (title - 2 steps)
        # #4  model          22px  prominent model name
        # #5  project/git    14px  secondary info
        # #7  context %      16px  reference baseline
        #     section label  12px  small caps labels
        # #9  5h usage       20px  (context + 2 steps)
        # #10 7d usage       20px  (context + 2 steps)
        #     footer         13px
        self.f_title = ImageFont.truetype(font_path, 20)
        self.f_time = ImageFont.truetype(font_path, 16)
        self.f_model = ImageFont.truetype(font_path, 22)
        self.f_project = ImageFont.truetype(font_path, 14)
        self.f_ctx_pct = ImageFont.truetype(font_path, 16)
        self.f_label = ImageFont.truetype(font_path, 12)
        self.f_usage = ImageFont.truetype(font_path, 20)
        self.f_detail = ImageFont.truetype(font_path, 13)
        self.f_footer = ImageFont.truetype(font_path, 13)

    # ── Drawing primitives ────────────────────────────────────────

    def _bar(self, draw, x, y, w, h, percent, radius=4):
        """Rounded progress bar with outline and fill."""
        # Outer rounded rect (border)
        draw.rounded_rectangle(
            [(x, y), (x + w, y + h)], radius=radius, outline=0, fill=255
        )
        # Inner fill
        fill_w = int(w * min(max(percent, 0), 100) / 100)
        if fill_w > radius * 2:
            draw.rounded_rectangle(
                [(x + 1, y + 1), (x + fill_w - 1, y + h - 1)],
                radius=max(radius - 1, 1),
                fill=0,
            )
        elif fill_w > 1:
            draw.rectangle(
                [(x + 1, y + 1), (x + fill_w - 1, y + h - 1)], fill=0
            )

    def _right_text(self, draw, y, text, font, margin=16):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((self.W - margin - tw, y), text, font=font, fill=0)

    def _center_text(self, draw, y, text, font, fill=0):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((self.W - tw) // 2, y), text, font=font, fill=fill)

    def _separator(self, draw, y):
        draw.line([(16, y), (384, y)], fill=0, width=1)

    # ── Main render ───────────────────────────────────────────────

    def render(self, snapshot):
        img = Image.new("1", (self.W, self.H), color=255)
        draw = ImageDraw.Draw(img)

        if snapshot is None:
            self._render_waiting(draw)
            return img

        y = self._render_header(draw, snapshot)
        y = self._render_model_project(draw, snapshot, y)
        y = self._render_context(draw, snapshot, y)
        y = self._render_usage(draw, snapshot, y)
        self._render_footer(draw, snapshot, y)

        return img

    def _render_waiting(self, draw):
        draw.rectangle([(0, 0), (399, 44)], fill=0)
        self._center_text(draw, 12, "今天的Token用完了吗？", self.f_title, fill=255)
        self._center_text(draw, 120, "Waiting for data...", self.f_model, fill=0)
        self._center_text(draw, 160, "Start a Claude Code session", self.f_project, fill=0)

    # ── Header (44px black bar) ───────────────────────────────────

    def _render_header(self, draw, snapshot):
        BAR_H = 44
        draw.rectangle([(0, 0), (399, BAR_H - 1)], fill=0)

        # Title — centered vertically in bar
        draw.text((16, 11), "今天的Token用完了吗？", font=self.f_title, fill=255)

        # Update date — right side, larger (16px)
        try:
            ts = datetime.fromisoformat(
                snapshot["timestamp"].replace("Z", "+00:00")
            )
            update_str = ts.astimezone().strftime("%Y-%m-%d")
        except Exception:
            update_str = "----/--/--"
        bbox = draw.textbbox((0, 0), update_str, font=self.f_time)
        tw = bbox[2] - bbox[0]
        draw.text((384 - tw, 13), update_str, font=self.f_time, fill=255)

        return BAR_H + 6  # 50

    # ── Model + Project + Git ─────────────────────────────────────

    def _render_model_project(self, draw, snapshot, y):
        # Model name — large and prominent
        model = snapshot.get("model", "Unknown")
        draw.text((16, y), model, font=self.f_model, fill=0)

        # Project name — right aligned
        project = snapshot.get("project", "")
        if project:
            segments = project.replace("\\", "/").split("/")
            name = segments[-1] if segments else project
            self._right_text(draw, y + 4, name, self.f_project)
        y += 30

        # Git branch — smaller, secondary
        git = snapshot.get("git")
        if git and git.get("branch"):
            dirty = "*" if git.get("isDirty") else ""
            draw.text(
                (16, y),
                f"git:({git['branch']}{dirty})",
                font=self.f_detail,
                fill=0,
            )
        y += 20

        self._separator(draw, y)
        return y + 8

    # ── Context Health ────────────────────────────────────────────

    def _render_context(self, draw, snapshot, y):
        ctx = snapshot.get("context", {})
        pct = ctx.get("percent", 0)

        # Label + percentage on same line
        draw.text((16, y), "CONTEXT", font=self.f_label, fill=0)

        # Percentage right of label
        pct_text = f"{pct}%"
        if pct >= 85:
            pct_text += " !"
        bbox = draw.textbbox((0, 0), pct_text, font=self.f_ctx_pct)
        tw = bbox[2] - bbox[0]
        draw.text((16 + 70, y - 2), pct_text, font=self.f_ctx_pct, fill=0)

        # Token count — right aligned
        total = ctx.get("totalTokens", 0)
        size = ctx.get("windowSize", 0)
        if size > 0:
            self._right_text(
                draw, y, f"{format_tokens(total)} / {format_tokens(size)}",
                self.f_detail,
            )
        y += 22

        # Wide rounded progress bar
        self._bar(draw, 16, y, 368, 20, pct, radius=5)
        y += 28

        self._separator(draw, y)
        return y + 10

    # ── Usage Limits ──────────────────────────────────────────────

    def _render_usage(self, draw, snapshot, y):
        draw.text((16, y), "USAGE", font=self.f_label, fill=0)
        y += 18

        usage = snapshot.get("usage")
        if not usage:
            draw.text((16, y), "No data", font=self.f_detail, fill=0)
            return y + 24

        # 5-hour row (20px font — prominent)
        five = usage.get("fiveHour")
        if five is not None:
            y = self._usage_row_v2(
                draw, y, "5h", five, usage.get("fiveHourResetAt")
            )
            y += 6  # extra gap between rows

        # 7-day row (20px font — prominent)
        seven = usage.get("sevenDay")
        if seven is not None:
            y = self._usage_row_v2(
                draw, y, "7d", seven, usage.get("sevenDayResetAt")
            )

        return y + 6

    def _usage_row_v2(self, draw, y, label, percent, reset_iso):
        """Usage row with 20px font, rounded bar, reset time."""
        # Label
        draw.text((16, y), f"{label}:", font=self.f_usage, fill=0)

        # Rounded bar
        bar_x = 62
        bar_w = 170
        self._bar(draw, bar_x, y + 2, bar_w, 18, percent, radius=4)

        # Percentage — bold and large
        pct_str = f"{percent}%"
        draw.text((bar_x + bar_w + 10, y), pct_str, font=self.f_usage, fill=0)

        # Reset time
        reset = format_reset_time(reset_iso)
        if reset:
            self._right_text(draw, y + 4, f"↻ {reset}", self.f_detail)

        return y + 26

    # ── Footer ────────────────────────────────────────────────────

    def _render_footer(self, draw, snapshot, y):
        # Separator before footer
        self._separator(draw, y)
        y += 8

        # Left: session duration
        session = snapshot.get("sessionDuration", "")
        if session:
            draw.text(
                (16, y), f"Session: {session}", font=self.f_footer, fill=0
            )

        # Center: active sessions count
        active = count_active_sessions()
        if active > 1:
            self._center_text(
                draw, y, f"{active} sessions", self.f_footer
            )

        # Right: update time
        try:
            ts = datetime.fromisoformat(
                snapshot["timestamp"].replace("Z", "+00:00")
            )
            time_str = ts.astimezone().strftime("%H:%M")
        except Exception:
            time_str = "--:--"
        self._right_text(draw, y, time_str, self.f_footer)


# ─── Zectrix API ─────────────────────────────────────────────────────────────


def push_to_device(img, config):
    tmp_path = Path(__file__).parent / "_push_tmp.png"
    img.save(str(tmp_path))

    push_url = (
        f"https://cloud.zectrix.com/open/v1/devices/"
        f"{config['mac_address']}/display/image"
    )
    headers = {"X-API-Key": config["api_key"]}
    data = {"dither": "true", "pageId": str(config["page_id"])}

    try:
        with open(tmp_path, "rb") as f:
            files = {"images": ("claude-hud.png", f, "image/png")}
            res = requests.post(
                push_url, headers=headers, files=files, data=data, timeout=30
            )
        print(f"  ✅ Push OK ({res.status_code})")
        return True
    except Exception as e:
        print(f"  ❌ Push failed: {e}")
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


# ─── Main Loop ───────────────────────────────────────────────────────────────


def _newest_snapshot_age():
    """Return how many seconds since the most recent snapshot was modified."""
    if not SNAPSHOTS_DIR.exists():
        return float("inf")
    now = time.time()
    ages = []
    for f in SNAPSHOTS_DIR.glob("*.json"):
        try:
            ages.append(now - f.stat().st_mtime)
        except Exception:
            pass
    return min(ages) if ages else float("inf")


# How long to keep running after the last snapshot update.
# When Claude Code closes, no new snapshots arrive, and after
# this timeout the bridge exits.  The wrapper re-launches it
# automatically next time Claude Code starts.
IDLE_SHUTDOWN_SECONDS = 10 * 60  # 10 minutes


def run(config, *, once=False, preview=False):
    renderer = EinkRenderer(config["font_path"])
    interval = config.get("interval_seconds", 300)

    print("╔══════════════════════════════════════╗")
    print("║   Claude Code → E-Ink Bridge  v1.2   ║")
    print("╚══════════════════════════════════════╝")
    print(f"  Device  : {config['mac_address']}")
    print(f"  Page    : {config['page_id']}")
    print(f"  Interval: {interval}s")
    print(f"  Idle off: {IDLE_SHUTDOWN_SECONDS // 60}min")
    print(f"  Snapshots: {SNAPSHOTS_DIR}")
    print()

    last_hash = None

    while True:
        now = datetime.now().strftime("%H:%M:%S")

        # ── Auto-exit when Claude Code is no longer running ──
        idle_age = _newest_snapshot_age()
        if idle_age > IDLE_SHUTDOWN_SECONDS:
            print(
                f"  [{now}] No fresh snapshots for "
                f"{int(idle_age // 60)}min — shutting down."
            )
            print("  (Will restart automatically when Claude Code runs.)")
            return

        snapshot = load_latest_snapshot()
        snap_hash = json.dumps(snapshot, sort_keys=True) if snapshot else None

        active = count_active_sessions()
        session_info = (
            f" ({active} active session{'s' if active != 1 else ''})"
            if active > 0
            else ""
        )

        if preview:
            print(f"  [{now}] Generating preview...{session_info}")
            img = renderer.render(snapshot)
            out = Path(__file__).parent / "preview.png"
            img.save(str(out))
            print(f"  Preview saved to {out}")
            return

        if snap_hash != last_hash:
            print(
                f"  [{now}] Data changed — rendering & pushing...{session_info}"
            )
            img = renderer.render(snapshot)
            push_to_device(img, config)
            last_hash = snap_hash
        else:
            print(f"  [{now}] No change — skipping{session_info}")

        if once:
            return

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Claude Code E-Ink Bridge")
    parser.add_argument(
        "--once", action="store_true", help="Push once then exit"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate preview.png without pushing",
    )
    args = parser.parse_args()

    config = load_config()

    if not os.path.exists(config["font_path"]):
        print(f"❌ Font not found: {config['font_path']}")
        print("   Set 'font_path' in config.json to a valid .ttf file.")
        sys.exit(1)

    # PID management (not needed for --preview)
    if not args.preview:
        write_pid()
        atexit.register(remove_pid)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    run(config, once=args.once, preview=args.preview)


if __name__ == "__main__":
    main()

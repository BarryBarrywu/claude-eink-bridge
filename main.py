#!/usr/bin/env python3
"""
Claude Code E-Ink Bridge

Reads per-session Claude HUD snapshots, picks the most recently active one,
renders a 400×300 1-bit dashboard, and pushes to a Zectrix e-ink device.

Supports multiple concurrent Claude Code sessions — always shows the
most recently updated one.

Usage:
    python main.py              # Run continuously (default 60s interval)
    python main.py --once       # Single push then exit
    python main.py --preview    # Generate preview image only (no push)
    python main.py --debug      # Print raw snapshot data each cycle
"""

import json
import os
import io
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
STALE_THRESHOLD = 3600  # seconds — ignore snapshots older than this

REQUIRED_CONFIG_KEYS = ("api_key", "mac_address", "page_id")


def load_config():
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        cfg = json.load(f)

    for key in REQUIRED_CONFIG_KEYS:
        if not cfg.get(key):
            print(f"❌ Missing required config field: '{key}'")
            print(f"   Copy config.example.json → config.json and fill in your values.")
            sys.exit(1)

    font_raw = cfg.get("font_path", "font.ttf")
    if not os.path.isabs(font_raw):
        font_raw = str(Path(__file__).parent / font_raw)
    cfg["font_path"] = font_raw
    return cfg


# ─── PID Management ─────────────────────────────────────────────────────────

def write_pid():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ─── Snapshot Scanner ────────────────────────────────────────────────────────

def scan_snapshots():
    """
    Single-pass scan of the snapshots directory.

    Returns (latest_data, active_count, newest_age_seconds):
      latest_data       — dict from the most recently modified snapshot, or None
      active_count      — number of non-stale snapshot files
      newest_age_seconds — seconds since the most recent snapshot was written
                           (float("inf") when no snapshots exist)

    Also deletes stale files (older than STALE_THRESHOLD) as a side effect.
    Each Claude Code instance writes:
        ~/.claude/plugins/claude-hud/eink-snapshots/{session_hash}.json
    """
    if not SNAPSHOTS_DIR.exists():
        return None, 0, float("inf")

    now = time.time()
    latest_data = None
    latest_mtime = 0
    active_count = 0
    newest_mtime = 0

    for f in SNAPSHOTS_DIR.glob("*.json"):
        try:
            mtime = f.stat().st_mtime
            if now - mtime > STALE_THRESHOLD:
                f.unlink(missing_ok=True)
                continue
        except Exception:
            continue

        active_count += 1
        if mtime > newest_mtime:
            newest_mtime = mtime

        try:
            if mtime > latest_mtime:
                latest_data = json.loads(f.read_text())
                latest_mtime = mtime
        except Exception:
            continue

    newest_age = (now - newest_mtime) if newest_mtime > 0 else float("inf")
    return latest_data, active_count, newest_age


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


# ─── Image Renderer ──────────────────────────────────────────────────────────

class EinkRenderer:
    W, H = 400, 300

    def __init__(self, font_path):
        self.f_title   = ImageFont.truetype(font_path, 20)
        self.f_time    = ImageFont.truetype(font_path, 16)
        self.f_model   = ImageFont.truetype(font_path, 22)
        self.f_project = ImageFont.truetype(font_path, 14)
        self.f_ctx_pct = ImageFont.truetype(font_path, 16)
        self.f_label   = ImageFont.truetype(font_path, 12)
        self.f_usage   = ImageFont.truetype(font_path, 20)
        self.f_detail  = ImageFont.truetype(font_path, 13)
        self.f_footer  = ImageFont.truetype(font_path, 13)

    # ── Drawing primitives ────────────────────────────────────────

    def _bar(self, draw, x, y, w, h, percent, radius=4):
        draw.rounded_rectangle(
            [(x, y), (x + w, y + h)], radius=radius, outline=0, fill=255
        )
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

    def render(self, snapshot, active_sessions=0):
        img = Image.new("1", (self.W, self.H), color=255)
        draw = ImageDraw.Draw(img)

        if snapshot is None:
            self._render_waiting(draw)
            return img

        y = self._render_header(draw, snapshot)
        y = self._render_model_project(draw, snapshot, y)
        y = self._render_context(draw, snapshot, y)
        y = self._render_usage(draw, snapshot, y)
        self._render_footer(draw, snapshot, y, active_sessions)

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
        draw.text((16, 11), "今天的Token用完了吗？", font=self.f_title, fill=255)

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
        model = snapshot.get("model", "Unknown")
        draw.text((16, y), model, font=self.f_model, fill=0)

        project = snapshot.get("project", "")
        if project:
            segments = project.replace("\\", "/").split("/")
            name = segments[-1] if segments else project
            self._right_text(draw, y + 4, name, self.f_project)
        y += 30

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

        draw.text((16, y), "CONTEXT", font=self.f_label, fill=0)

        pct_text = f"{pct}%"
        if pct >= 85:
            pct_text += " !"
        draw.text((16 + 70, y - 2), pct_text, font=self.f_ctx_pct, fill=0)

        total = ctx.get("totalTokens", 0)
        size = ctx.get("windowSize", 0)
        if size > 0:
            self._right_text(
                draw, y, f"{format_tokens(total)} / {format_tokens(size)}",
                self.f_detail,
            )
        y += 22

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

        five = usage.get("fiveHour")
        if five is not None:
            y = self._usage_row(draw, y, "5h", five, usage.get("fiveHourResetAt"))
            y += 6

        seven = usage.get("sevenDay")
        if seven is not None:
            y = self._usage_row(draw, y, "7d", seven, usage.get("sevenDayResetAt"))

        return y + 6

    def _usage_row(self, draw, y, label, percent, reset_iso):
        draw.text((16, y), f"{label}:", font=self.f_usage, fill=0)

        bar_x, bar_w = 62, 170
        self._bar(draw, bar_x, y + 2, bar_w, 18, percent, radius=4)

        draw.text((bar_x + bar_w + 10, y), f"{percent}%", font=self.f_usage, fill=0)

        reset = format_reset_time(reset_iso)
        if reset:
            self._right_text(draw, y + 4, f"↻ {reset}", self.f_detail)

        return y + 26

    # ── Footer ────────────────────────────────────────────────────

    def _render_footer(self, draw, snapshot, y, active_sessions):
        self._separator(draw, y)
        y += 8

        session = snapshot.get("sessionDuration", "")
        if session:
            draw.text((16, y), f"Session: {session}", font=self.f_footer, fill=0)

        if active_sessions > 1:
            self._center_text(
                draw, y, f"{active_sessions} sessions", self.f_footer
            )

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
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    push_url = (
        f"https://cloud.zectrix.com/open/v1/devices/"
        f"{config['mac_address']}/display/image"
    )
    headers = {"X-API-Key": config["api_key"]}
    data = {"dither": "true", "pageId": str(config["page_id"])}

    try:
        files = {"images": ("claude-hud.png", buf, "image/png")}
        res = requests.post(
            push_url, headers=headers, files=files, data=data, timeout=30
        )
        res.raise_for_status()
        print(f"  ✅ Push OK ({res.status_code})")
        return True
    except requests.exceptions.RequestException as e:
        err_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            err_msg += f" - {e.response.text}"
        print(f"  ❌ Push failed: {err_msg}")
        return False
    except Exception as e:
        print(f"  ❌ Push failed: {e}")
        return False


# ─── Main Loop ───────────────────────────────────────────────────────────────

# Bridge exits after this many seconds with no fresh snapshot.
# The wrapper re-launches it automatically next time Claude Code starts.
IDLE_SHUTDOWN_SECONDS = 10 * 60  # 10 minutes


def run(config, *, once=False, preview=False, debug=False):
    renderer = EinkRenderer(config["font_path"])
    interval = config.get("interval_seconds", 60)

    print("╔══════════════════════════════════════╗")
    print("║   Claude Code → E-Ink Bridge  v1.3   ║")
    print("╚══════════════════════════════════════╝")
    print(f"  Device  : {config['mac_address']}")
    print(f"  Page    : {config['page_id']}")
    print(f"  Interval: {interval}s")
    print(f"  Idle off: {IDLE_SHUTDOWN_SECONDS // 60}min")
    print(f"  Snapshots: {SNAPSHOTS_DIR}")
    print()

    if preview:
        snapshot, active, _ = scan_snapshots()
        if debug:
            print(f"  [DEBUG] Loaded snapshot: {snapshot}")
        print("  Generating preview...")
        img = renderer.render(snapshot, active)
        out = Path(__file__).parent / "preview.png"
        img.save(str(out))
        print(f"  Preview saved to {out}")
        return

    last_hash = None

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        snapshot, active, idle_age = scan_snapshots()

        if debug:
            print(f"  [DEBUG] Loaded snapshot: {snapshot}")

        if idle_age > IDLE_SHUTDOWN_SECONDS:
            print(
                f"  [{now}] No fresh snapshots for "
                f"{int(idle_age // 60)}min — shutting down."
            )
            print("  (Will restart automatically when Claude Code runs.)")
            return

        snap_hash = json.dumps(snapshot, sort_keys=True) if snapshot else None
        session_info = (
            f" ({active} active session{'s' if active != 1 else ''})"
            if active > 0 else ""
        )

        if snap_hash != last_hash:
            print(f"  [{now}] Data changed — rendering & pushing...{session_info}")
            img = renderer.render(snapshot, active)
            push_to_device(img, config)
            last_hash = snap_hash
        else:
            print(f"  [{now}] No change — skipping{session_info}")

        if once:
            return

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Claude Code E-Ink Bridge")
    parser.add_argument("--once", action="store_true", help="Push once then exit")
    parser.add_argument(
        "--preview", action="store_true",
        help="Generate preview.png without pushing",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print raw snapshot data and debug info",
    )
    args = parser.parse_args()

    config = load_config()

    if not os.path.exists(config["font_path"]):
        print(f"❌ Font not found: {config['font_path']}")
        print("   Please make sure the font file exists at the specified path.")
        print("   Tip: You can use an absolute path in config.json's 'font_path'.")
        sys.exit(1)

    if not args.preview:
        write_pid()
        atexit.register(remove_pid)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    run(config, once=args.once, preview=args.preview, debug=args.debug)


if __name__ == "__main__":
    main()

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
            days = hours // 24
            remaining_hours = hours % 24
            if remaining_hours > 0:
                return f"{days}d {remaining_hours}h"
            return f"{days}d"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return None


# ─── Image Renderer ──────────────────────────────────────────────────────────

class EinkRenderer:
    W, H = 400, 300

    # ── Layout constants ──────────────────────────────────────────
    HDR_H       = 36    # header bar height
    FOOTER_H    = 26    # footer height
    SEP_H       = 2     # solid separator bar height
    LIM_H       = 88    # rate-limits section height
    CTX_COL_W   = 128   # right (context) column width
    COL_DIV_W   = 2     # vertical column divider width
    MODEL_COL_W = W - CTX_COL_W - COL_DIV_W  # = 270
    L_PAD       = 12    # left/right margin for main content
    R_PAD       = 10    # inner margin for right column
    BODY_V      = 10    # vertical inset inside body columns

    def __init__(self, font_path, greeting="今天的Token用完了吗？"):
        self.greeting = greeting
        self.f_hdr    = ImageFont.truetype(font_path, 16)
        self.f_date   = ImageFont.truetype(font_path, 13)
        self.f_lbl    = ImageFont.truetype(font_path, 12)  # section labels (RATE LIMITS)
        self.f_model  = ImageFont.truetype(font_path, 36)  # large model name
        self.f_ctx    = ImageFont.truetype(font_path, 36)  # large context %
        self.f_ctx_sm = ImageFont.truetype(font_path, 28)  # fallback for context % overflow
        self.f_meta   = ImageFont.truetype(font_path, 12)  # project / git
        self.f_tok_sm = ImageFont.truetype(font_path, 15)  # token detail breakdown
        self.f_period = ImageFont.truetype(font_path, 16)  # 5H / 7D
        self.f_pct    = ImageFont.truetype(font_path, 16)  # usage percentages
        self.f_reset  = ImageFont.truetype(font_path, 16)  # reset times
        self.f_tokens = ImageFont.truetype(font_path, 12)  # token count under bar
        self.f_footer = ImageFont.truetype(font_path, 12)

    # ── Primitives ────────────────────────────────────────────────

    def _tw(self, draw, text, font):
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0]

    def _th(self, draw, text, font):
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[3] - bb[1]

    def _right(self, draw, x_right, y, text, font, fill=0):
        draw.text((x_right - self._tw(draw, text, font), y), text, font=font, fill=fill)

    def _center(self, draw, x0, x1, y, text, font, fill=0):
        x = x0 + (x1 - x0 - self._tw(draw, text, font)) // 2
        draw.text((x, y), text, font=font, fill=fill)

    def _truncate(self, draw, text, font, max_w):
        if self._tw(draw, text, font) <= max_w:
            return text
        for i in range(len(text) - 1, 0, -1):
            t = text[:i] + "..."
            if self._tw(draw, t, font) <= max_w:
                return t
        return "..."

    def _bar(self, draw, x0, y0, x1, y1, percent, radius=3):
        draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=radius, outline=0, fill=255)
        w = x1 - x0
        fill_w = int(w * min(max(percent, 0), 100) / 100)
        if fill_w > radius * 2:
            draw.rounded_rectangle(
                [(x0 + 1, y0 + 1), (x0 + fill_w - 1, y1 - 1)],
                radius=max(radius - 1, 1), fill=0,
            )
        elif fill_w > 1:
            draw.rectangle([(x0 + 1, y0 + 1), (x0 + fill_w - 1, y1 - 1)], fill=0)

    def _tmeasure(self, draw, text, font):
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0], bb[3] - bb[1]

    def _parse_ts(self, snapshot):
        try:
            return datetime.fromisoformat(
                snapshot["timestamp"].replace("Z", "+00:00")
            ).astimezone()
        except Exception:
            return None

    # ── Public entry point ────────────────────────────────────────

    def render(self, snapshot, active_sessions=0):
        img = Image.new("1", (self.W, self.H), color=255)
        draw = ImageDraw.Draw(img)
        if snapshot is None:
            self._waiting(draw)
        else:
            self._header(draw, snapshot)
            self._body(draw, snapshot)
            self._limits(draw, snapshot)
            self._footer(draw, snapshot, active_sessions)
        return img

    # ── Waiting screen ────────────────────────────────────────────

    def _waiting(self, draw):
        draw.rectangle([(0, 0), (self.W - 1, self.HDR_H - 1)], fill=0)
        safe = self._truncate(draw, self.greeting, self.f_hdr, self.W - 24)
        draw.text((self.L_PAD, 11), safe, font=self.f_hdr, fill=255)
        self._center(draw, 0, self.W, 110, "Waiting for data...", self.f_model)
        self._center(draw, 0, self.W, 158, "Start a Claude Code session", self.f_meta)

    # ── Header ────────────────────────────────────────────────────

    def _header(self, draw, snapshot):
        draw.rectangle([(0, 0), (self.W - 1, self.HDR_H - 1)], fill=0)
        safe = self._truncate(draw, self.greeting, self.f_hdr, self.W - 110)
        draw.text((self.L_PAD, 11), safe, font=self.f_hdr, fill=255)
        ts = self._parse_ts(snapshot)
        date_str = ts.strftime("%Y-%m-%d") if ts else "----/--/--"
        self._right(draw, self.W - self.L_PAD, 13, date_str, self.f_date, fill=255)

    # ── Body: left column (model) + right column (context) ────────

    def _body(self, draw, snapshot):
        y0 = self.HDR_H
        y1 = self.H - self.FOOTER_H - self.SEP_H - self.LIM_H - self.SEP_H  # 203

        # Vertical divider between columns
        div_x = self.MODEL_COL_W  # 270
        draw.rectangle([(div_x, y0), (div_x + self.COL_DIV_W - 1, y1 - 1)], fill=0)

        self._body_model(draw, snapshot, y0, y1, div_x)
        self._body_context(draw, snapshot, y0, y1, div_x + self.COL_DIV_W)

    def _body_model(self, draw, snapshot, y0, y1, div_x):
        V = self.BODY_V
        lx = self.L_PAD
        max_w = div_x - lx - 4

        model = snapshot.get("model", "Unknown")
        model_str = self._truncate(draw, model, self.f_model, max_w)
        model_h = self._th(draw, model_str, self.f_model)
        draw.text((lx, y0 + V), model_str, font=self.f_model, fill=0)
        model_bottom = y0 + V + model_h

        # Bottom: thin separator + project + git
        project = snapshot.get("project", "")
        project_name = project.replace("\\", "/").split("/")[-1] if project else ""
        git = snapshot.get("git") or {}
        git_str = ""
        if git.get("branch"):
            dirty = "*" if git.get("isDirty") else ""
            git_str = f"git:({git['branch']}{dirty})"
        lines = [l for l in [project_name, git_str] if l]
        lh = self._th(draw, "Mg", self.f_meta)
        meta_h = lh * len(lines) + 4 * max(0, len(lines) - 1)
        sep_y = y1 - V - meta_h - 7

        # Token breakdown — centered in the gap between model name and meta
        ctx = snapshot.get("context", {})
        in_tok    = ctx.get("inputTokens", 0)
        out_tok   = ctx.get("outputTokens", 0)
        cache_tok = ctx.get("cacheTokens", 0)
        if in_tok or out_tok or cache_tok:
            detail_str = (f"in:{format_tokens(in_tok)}"
                          f"  out:{format_tokens(out_tok)}"
                          f"  cache:{format_tokens(cache_tok)}")
            detail_str = self._truncate(draw, detail_str, self.f_tok_sm, max_w)
            detail_h = self._th(draw, detail_str, self.f_tok_sm)
            tok_y    = model_bottom + (sep_y - model_bottom - detail_h) // 2
            draw.text((lx, tok_y), detail_str, font=self.f_tok_sm, fill=0)

        draw.line([(lx, sep_y), (div_x - lx, sep_y)], fill=0, width=1)
        for i, line in enumerate(lines):
            draw.text((lx, sep_y + 7 + i * (lh + 4)), line, font=self.f_meta, fill=0)

    def _body_context(self, draw, snapshot, y0, y1, ctx_x):
        V = self.BODY_V
        rx0 = ctx_x + self.R_PAD
        rx1 = self.W - self.R_PAD
        rw  = rx1 - rx0
        r_top = y0 + V
        r_bot = y1 - V

        ctx = snapshot.get("context", {})
        pct = ctx.get("percent", 0)
        pct_str = f"{pct}%"
        if pct >= 85:
            pct_str += "!"

        tw = self._tw(draw, pct_str, self.f_ctx)
        if tw > rw - 4:
            ctx_font = self.f_ctx_sm
            tw = self._tw(draw, pct_str, ctx_font)
        else:
            ctx_font = self.f_ctx
        pct_bb  = draw.textbbox((0, 0), pct_str, font=ctx_font)
        # pct_bb[3] is the raw bottom offset from draw position — not bb[3]-bb[1],
        # which would undercount and clip the text below the box border.
        box_h   = 6 + pct_bb[3] + 4
        tok_h   = self._th(draw, "000k/000k", self.f_tokens)
        bot_h   = 14 + 4 + tok_h
        gap     = max((r_bot - r_top - box_h - bot_h) // 2, 4)

        box_y0 = r_top + gap
        box_y1 = box_y0 + box_h
        draw.rectangle([(rx0, box_y0), (rx1, box_y1)], outline=0, fill=255)
        draw.text((rx0 + (rw - tw) // 2, box_y0 + 6), pct_str, font=ctx_font, fill=0)

        gy0 = box_y1 + gap
        self._bar(draw, rx0, gy0, rx1, gy0 + 14, pct)
        total = ctx.get("totalTokens", 0)
        size  = ctx.get("windowSize", 0)
        if size > 0:
            tok_str = f"{format_tokens(total)}/{format_tokens(size)}"
            self._right(draw, rx1, gy0 + 14 + 4, tok_str, self.f_tokens)

    # ── Rate limits ───────────────────────────────────────────────

    def _limits(self, draw, snapshot):
        sep_y = self.H - self.FOOTER_H - self.SEP_H - self.LIM_H - self.SEP_H
        draw.rectangle([(0, sep_y), (self.W - 1, sep_y + self.SEP_H - 1)], fill=0)

        y = sep_y + self.SEP_H + 6
        draw.text((self.L_PAD, y), "RATE LIMITS", font=self.f_lbl, fill=0)
        y += self._th(draw, "RATE LIMITS", self.f_lbl) + 5

        usage = snapshot.get("usage") or {}
        five = usage.get("fiveHour")
        if five is not None:
            y = self._limit_row(draw, y, "5H", five, usage.get("fiveHourResetAt"))
            y += 5
        seven = usage.get("sevenDay")
        if seven is not None:
            self._limit_row(draw, y, "7D", seven, usage.get("sevenDayResetAt"))

    def _limit_row(self, draw, y, label, percent, reset_iso):
        ROW_H   = 28
        BAR_H   = 18
        RIGHT   = self.W - self.L_PAD  # 388
        GAP     = 8
        PCT_COL = 44   # fixed column width for percentage text
        RST_COL = 66   # fixed column width for reset time text

        lh = self._th(draw, label, self.f_period)
        draw.text((self.L_PAD, y + (ROW_H - lh) // 2), label, font=self.f_period, fill=0)

        reset = format_reset_time(reset_iso)
        pct_str = f"{percent}%"
        _, pct_h = self._tmeasure(draw, pct_str, self.f_pct)

        # Fixed bar boundaries — same for every row regardless of text width
        bar_x0 = self.L_PAD + 30 + GAP
        bar_x1 = RIGHT - RST_COL - GAP - PCT_COL - GAP
        bar_y0 = y + (ROW_H - BAR_H) // 2
        self._bar(draw, bar_x0, bar_y0, bar_x1, bar_y0 + BAR_H, percent)

        # Pct right-aligned into its fixed column
        self._right(draw, RIGHT - RST_COL - GAP, y + (ROW_H - pct_h) // 2, pct_str, self.f_pct)

        if reset:
            _, rst_h = self._tmeasure(draw, reset, self.f_reset)
            self._right(draw, RIGHT, y + (ROW_H - rst_h) // 2, reset, self.f_reset)

        return y + ROW_H

    # ── Footer ────────────────────────────────────────────────────

    def _footer(self, draw, snapshot, active_sessions):
        sep_y = self.H - self.FOOTER_H - self.SEP_H
        draw.rectangle([(0, sep_y), (self.W - 1, sep_y + self.SEP_H - 1)], fill=0)

        fy = sep_y + self.SEP_H
        ty = fy + (self.FOOTER_H - self._th(draw, "Mg", self.f_footer)) // 2

        session = snapshot.get("sessionDuration", "")
        if session:
            draw.text((self.L_PAD, ty), f"Session: {session}", font=self.f_footer, fill=0)

        if active_sessions > 1:
            self._center(draw, 0, self.W, ty, f"{active_sessions} sessions", self.f_footer)

        ts = self._parse_ts(snapshot)
        time_str = ts.strftime("%H:%M") if ts else "--:--"
        self._right(draw, self.W - self.L_PAD, ty, time_str, self.f_footer)


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
    greeting = config.get("greeting", "今天的Token用完了吗？")
    renderer = EinkRenderer(config["font_path"], greeting=greeting)
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
        out = Path(__file__).parent / "preview-local.png"
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

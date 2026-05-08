#!/usr/bin/env bun
/**
 * Claude HUD E-Ink Wrapper
 *
 * A thin, independent shim that sits between Claude Code and Claude HUD.
 * It transparently forwards stdin → HUD → stdout while also writing
 * a per-session snapshot file for the eink-bridge to consume.
 *
 * ┌────────────┐    stdin     ┌──────────┐    stdin    ┌───────────┐
 * │ Claude Code │ ──────────▶ │ wrapper  │ ─────────▶ │ Claude HUD │
 * │            │ ◀────────── │ (this)   │ ◀──────── │           │
 * └────────────┘   stdout    └──────────┘   stdout   └───────────┘
 *                                  │
 *                                  ▼ (throttled write)
 *                        eink-snapshots/{sid}.json
 *
 * ZERO dependency on HUD internals:
 *   - Discovers HUD binary dynamically via plugin cache
 *   - Uses the same discovery logic as the original statusLine command
 *   - HUD upgrades are transparent — wrapper auto-picks the latest version
 *
 * Install:
 *   1. Copy this file to a stable location (e.g. ~/.claude/eink-wrapper.ts)
 *   2. Run: node /path/to/setup-eink.js   (patches statusLine config)
 */

import { spawn, execSync } from "child_process";
import {
  writeFileSync,
  mkdirSync,
  statSync,
  readdirSync,
  readFileSync,
  existsSync,
} from "fs";
import { join, dirname } from "path";
import { homedir } from "os";
import { createHash } from "crypto";

const HOME = homedir();
const CLAUDE_DIR = process.env.CLAUDE_CONFIG_DIR || join(HOME, ".claude");
const PLUGIN_DIR = join(CLAUDE_DIR, "plugins", "claude-hud");
const SNAP_DIR = join(PLUGIN_DIR, "eink-snapshots");
const BRIDGE_PID = join(PLUGIN_DIR, "eink-bridge.pid");
const BRIDGE_CFG = join(PLUGIN_DIR, "eink-bridge.json");
const THROTTLE_MS = 30_000;

// ── Find HUD binary (same logic as the original statusLine command) ─────

function findHudEntry(): string | null {
  const cacheBase = join(CLAUDE_DIR, "plugins", "cache", "claude-hud", "claude-hud");
  try {
    const versions = readdirSync(cacheBase)
      .filter((d) => /^\d/.test(d))
      .sort((a, b) => {
        const pa = a.split(".").map(Number);
        const pb = b.split(".").map(Number);
        for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
          if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) - (pb[i] ?? 0);
        }
        return 0;
      });
    const latest = versions[versions.length - 1];
    if (!latest) return null;
    return join(cacheBase, latest, "src", "index.ts");
  } catch {
    return null;
  }
}

// ── Git info ─────────────────────────────────────────────────────────────

function getGitInfo(cwd: string): { branch: string; isDirty: boolean } | null {
  try {
    const branch = execSync("git rev-parse --abbrev-ref HEAD", {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    const isDirty =
      execSync("git status --porcelain", {
        cwd,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }).trim().length > 0;
    return { branch, isDirty };
  } catch {
    return null; // not a git repo, or git not installed
  }
}

// ── Session duration ──────────────────────────────────────────────────────

function formatDuration(startedAt: string): string {
  const totalMins = Math.floor(
    (Date.now() - new Date(startedAt).getTime()) / 60_000
  );
  if (totalMins < 1) return "< 1m";
  if (totalMins < 60) return `${totalMins}m`;
  return `${Math.floor(totalMins / 60)}h ${totalMins % 60}m`;
}

// ── Snapshot writer ─────────────────────────────────────────────────────

function writeSnapshot(raw: string): void {
  if (!raw.trim()) return;

  let data: any;
  try {
    data = JSON.parse(raw.trim());
  } catch {
    return;
  }

  const cwd: string = data.cwd || "unknown";
  const sid = createHash("md5").update(cwd).digest("hex").slice(0, 8);
  const snapPath = join(SNAP_DIR, `${sid}.json`);

  // Throttle: write at most every 30s per session
  try {
    if (Date.now() - statSync(snapPath).mtimeMs < THROTTLE_MS) return;
  } catch {
    /* file doesn't exist yet — proceed */
  }

  // Extract the fields the bridge needs
  const cw = data.context_window ?? {};
  const usage = cw.current_usage ?? {};
  const rl = data.rate_limits ?? null;
  const model = data.model?.display_name?.trim() || data.model?.id || "Unknown";

  // Preserve session start time across snapshot updates.
  // Reset if the last snapshot is stale (> 1h) — indicates a new session.
  let sessionStartedAt: string | null = null;
  try {
    const existing = JSON.parse(readFileSync(snapPath, "utf8"));
    const age = Date.now() - new Date(existing.timestamp || 0).getTime();
    if (age < 3_600_000) {
      sessionStartedAt = existing.sessionStartedAt || null;
    }
  } catch {
    /* first write */
  }
  if (!sessionStartedAt) sessionStartedAt = new Date().toISOString();

  const snapshot = {
    timestamp: new Date().toISOString(),
    sessionStartedAt,
    sessionId: sid,
    model,
    project: cwd,
    context: {
      percent:
        typeof cw.used_percentage === "number" && cw.used_percentage > 0
          ? Math.min(100, Math.round(cw.used_percentage))
          : cw.context_window_size > 0
            ? Math.min(
              100,
              Math.round(
                (((usage.input_tokens ?? 0) +
                  (usage.cache_creation_input_tokens ?? 0) +
                  (usage.cache_read_input_tokens ?? 0)) /
                  cw.context_window_size) *
                100,
              ),
            )
            : 0,
      windowSize: cw.context_window_size ?? 0,
      totalTokens:
        (usage.input_tokens ?? 0) +
        (usage.cache_creation_input_tokens ?? 0) +
        (usage.cache_read_input_tokens ?? 0),
      inputTokens: usage.input_tokens ?? 0,
      outputTokens: usage.output_tokens ?? 0,
      cacheTokens:
        (usage.cache_creation_input_tokens ?? 0) +
        (usage.cache_read_input_tokens ?? 0),
    },
    usage: rl
      ? {
        fiveHour:
          typeof rl.five_hour?.used_percentage === "number"
            ? Math.round(rl.five_hour.used_percentage)
            : null,
        sevenDay:
          typeof rl.seven_day?.used_percentage === "number"
            ? Math.round(rl.seven_day.used_percentage)
            : null,
        fiveHourResetAt: rl.five_hour?.resets_at
          ? new Date(rl.five_hour.resets_at * 1000).toISOString()
          : null,
        sevenDayResetAt: rl.seven_day?.resets_at
          ? new Date(rl.seven_day.resets_at * 1000).toISOString()
          : null,
      }
      : null,
    git: getGitInfo(cwd),
    sessionDuration: formatDuration(sessionStartedAt),
  };

  try {
    mkdirSync(SNAP_DIR, { recursive: true });
    writeFileSync(snapPath, JSON.stringify(snapshot));
  } catch {
    /* silently ignore */
  }
}

// ── Bridge auto-start ───────────────────────────────────────────────────

function ensureBridgeRunning(): void {
  if (!existsSync(BRIDGE_CFG)) return;

  // Check if already running
  try {
    const pid = parseInt(readFileSync(BRIDGE_PID, "utf8").trim(), 10);
    if (!isNaN(pid) && pid > 0) {
      process.kill(pid, 0); // throws if dead
      return;
    }
  } catch {
    /* not running */
  }

  try {
    const cfg = JSON.parse(readFileSync(BRIDGE_CFG, "utf8"));
    const py: string = cfg.python_path;
    const dir: string = cfg.bridge_path;
    if (!py || !dir) return;

    const child = spawn(py, [join(dir, "main.py")], {
      detached: true,
      stdio: "ignore",
      cwd: dir,
    });
    child.unref();
  } catch {
    /* silently ignore */
  }
}

// ── Main ────────────────────────────────────────────────────────────────

const hudEntry = findHudEntry();
if (!hudEntry) {
  console.error("[eink-wrapper] Claude HUD not found in plugin cache");
  process.exit(1);
}

const isNode = process.argv[0].endsWith("node");
const bin = isNode ? "npx" : process.argv[0];
const args = isNode
  ? ["-y", "tsx", hudEntry, ...process.argv.slice(2)]
  : ["--env-file", "/dev/null", hudEntry, ...process.argv.slice(2)];

const hud = spawn(bin, args, {
  stdio: ["pipe", "inherit", "inherit"],
});

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk: string) => {
  raw += chunk;
  hud.stdin!.write(chunk);
});
process.stdin.on("end", () => {
  hud.stdin!.end();
  writeSnapshot(raw);
  ensureBridgeRunning();
});

hud.on("close", (code) => process.exit(code ?? 0));

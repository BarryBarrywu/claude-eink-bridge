#!/usr/bin/env node
/**
 * Setup script for Claude HUD E-Ink Bridge
 *
 * What it does:
 *   1. Patches ~/.claude/settings.json statusLine to use the eink-wrapper
 *   2. Creates the eink-bridge.json config for auto-start
 *
 * The bridge is started on-demand by the wrapper (no launchd needed).
 * It auto-exits after 10 min of inactivity.
 *
 * Usage:
 *   node setup-eink.mjs          # Install
 *   node setup-eink.mjs --undo   # Restore original statusLine
 */

import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { homedir } from "os";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

const HOME = homedir();
// Honor CLAUDE_CONFIG_DIR so install can target a non-default Claude config
// directory (e.g. a separate ~/.claude-team instance). Falls back to ~/.claude.
const CLAUDE_DIR = process.env.CLAUDE_CONFIG_DIR || join(HOME, ".claude");
const SETTINGS = join(CLAUDE_DIR, "settings.json");
const WRAPPER = join(CLAUDE_DIR, "eink-wrapper.ts");
const PLUGIN_DIR = join(CLAUDE_DIR, "plugins", "claude-hud");
const BRIDGE_CFG = join(PLUGIN_DIR, "eink-bridge.json");

const isUndo = process.argv.includes("--undo");

let settings;
try {
  settings = JSON.parse(readFileSync(SETTINGS, "utf8"));
} catch (e) {
  console.error("❌ Cannot read", SETTINGS);
  process.exit(1);
}

if (isUndo) {
  undo(settings);
} else {
  install(settings);
}

function install(settings) {
  const currentCmd = settings.statusLine?.command ?? "";

  // Save original command for undo (only if not already wrapped)
  if (!currentCmd.includes("eink-wrapper")) {
    settings._einkOriginalStatusLine = settings.statusLine;
  }

  let commandStr;
  try {
    const bunPath = execSync("which bun", { encoding: "utf8" }).trim();
    commandStr = `"${bunPath}" --env-file /dev/null "${WRAPPER}"`;
  } catch {
    console.warn("⚠️  Could not find 'bun'. Falling back to 'node' with 'npx tsx'.");
    console.warn("   Will use 'npx -y tsx' to execute the wrapper.");
    commandStr = `npx -y tsx "${WRAPPER}"`;
  }

  settings.statusLine = {
    type: "command",
    command: commandStr,
  };

  writeFileSync(SETTINGS, JSON.stringify(settings, null, 2));
  console.log("✅ statusLine patched to use eink-wrapper");

  // Write bridge config for auto-start
  const __filename = fileURLToPath(import.meta.url);
  const bridgePath = dirname(__filename);
  const pythonPath = join(bridgePath, ".venv", "bin", "python");

  mkdirSync(PLUGIN_DIR, { recursive: true });
  writeFileSync(
    BRIDGE_CFG,
    JSON.stringify({ python_path: pythonPath, bridge_path: bridgePath }, null, 2),
  );
  console.log("✅ eink-bridge.json created");

  console.log();
  console.log("🎉 Setup complete! Restart Claude Code to activate.");
  console.log("   Bridge starts automatically when Claude Code runs.");
  console.log("   Bridge exits 10min after Claude Code closes.");
  console.log("   HUD upgrades won't affect the wrapper.");
}

function undo(settings) {
  if (settings._einkOriginalStatusLine) {
    settings.statusLine = settings._einkOriginalStatusLine;
    delete settings._einkOriginalStatusLine;
    writeFileSync(SETTINGS, JSON.stringify(settings, null, 2));
    console.log("✅ statusLine restored to original");
  } else {
    console.log("⚠️  No original statusLine backup found");
  }
  console.log("\n🔄 Undo complete. Restart Claude Code.");
}

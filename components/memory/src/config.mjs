import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

export function resolveMemoryHome() {
  if (process.env.PERSONAL_MEMORY_HOME) {
    return path.resolve(process.env.PERSONAL_MEMORY_HOME);
  }
  const base = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  return path.join(base, "PersonalMemory");
}

export function ensureDirectories(home, dataRoot) {
  for (const relative of ["logs", "state"]) fs.mkdirSync(path.join(home, relative), { recursive: true });
  if (dataRoot) {
    for (const relative of ["", "events", "memory", "memory/daily"]) {
      fs.mkdirSync(path.join(dataRoot, relative), { recursive: true });
    }
  }
}

function defaultConfig(home) {
  return {
    version: 1,
    dataRoot: path.join(home, "data"),
    host: "0.0.0.0",
    port: 18765,
    token: crypto.randomBytes(32).toString("base64url"),
    maxBodyBytes: 256 * 1024,
    privacy: {
      redactSecrets: true,
      redactOtp: true,
      excludedPackages: [
        "com.google.android.apps.authenticator2",
        "com.microsoft.authenticator",
        "com.azure.authenticator",
        "com.lastpass.lpandroid",
        "com.onepassword.android",
        "com.bitwarden"
      ]
    },
    codexImport: {
      enabled: true,
      sessionsRoot: path.join(os.homedir(), ".codex", "sessions"),
      includeAssistant: true,
      intervalMs: 15000
    },
    dshImport: {
      enabled: true,
      sessionsRoot: path.join(os.homedir(), ".dsh", "sessions"),
      includeAssistant: true,
      intervalMs: 15000
    },
    desktopCapture: { enabled: false },
    cloudInbox: { enabled: true, intervalMs: 15000 },
    createdAt: new Date().toISOString()
  };
}

export function loadConfig() {
  const home = resolveMemoryHome();
  ensureDirectories(home);
  const configPath = path.join(home, "config.json");
  let config;
  if (fs.existsSync(configPath)) {
    const parsed = JSON.parse(fs.readFileSync(configPath, "utf8"));
    const defaults = defaultConfig(home);
    config = {
      ...defaults,
      ...parsed,
      privacy: { ...defaults.privacy, ...(parsed.privacy || {}) },
      codexImport: { ...defaults.codexImport, ...(parsed.codexImport || {}) },
      dshImport: { ...defaults.dshImport, ...(parsed.dshImport || {}) },
      desktopCapture: { ...defaults.desktopCapture, ...(parsed.desktopCapture || {}) },
      cloudInbox: { ...defaults.cloudInbox, ...(parsed.cloudInbox || {}) }
    };
  } else {
    config = defaultConfig(home);
    fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
  }
  config.dataRoot = path.resolve(config.dataRoot || path.join(home, "data"));
  ensureDirectories(home, config.dataRoot);
  return { home, config, configPath };
}

export function writeConfig(home, config) {
  const configPath = path.join(home, "config.json");
  const tempPath = `${configPath}.tmp`;
  fs.writeFileSync(tempPath, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(tempPath, configPath);
}

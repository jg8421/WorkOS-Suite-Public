import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const ALLOWED_TYPES = new Set([
  "note", "message", "notification", "preference", "decision", "project",
  "task", "person", "document", "activity", "context"
]);

function oneLine(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function sourceHash(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 24);
}

function tokenize(text) {
  const normalized = String(text || "").toLocaleLowerCase("en-US");
  const tokens = new Set(normalized.match(/[\p{L}\p{N}_-]{2,}/gu) || []);
  const han = [...normalized.matchAll(/[\p{Script=Han}]+/gu)].map((match) => match[0]);
  for (const run of han) {
    for (const char of run) tokens.add(char);
    for (let index = 0; index < run.length - 1; index += 1) tokens.add(run.slice(index, index + 2));
  }
  return tokens;
}

function redact(text, privacy = {}) {
  let value = String(text ?? "");
  if (privacy.redactSecrets !== false) {
    value = value
      .replace(/\b(?:sk|rk|pk|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b/g, "[REDACTED_KEY]")
      .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]{12,}=*/gi, "Bearer [REDACTED]")
      .replace(/\b(password|passwd|pwd|token|secret|api[_ -]?key)\s*[:=]\s*[^\s,;]+/gi, "$1=[REDACTED]");
  }
  if (privacy.redactOtp !== false && /(?:验证码|校验码|动态码|one.?time|verification|\botp\b|\bcode\b)/i.test(value)) {
    value = value.replace(/(?<!\d)\d{4,8}(?!\d)/g, "[REDACTED_OTP]");
  }
  return value.trim();
}

function safeTimestamp(value) {
  const date = value ? new Date(value) : new Date();
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

function atomicWrite(filePath, content) {
  const temp = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temp, content, "utf8");
  fs.renameSync(temp, filePath);
}

export class MemoryStore {
  constructor(home, config) {
    this.home = home;
    this.config = config;
    this.dataRoot = path.resolve(config.dataRoot || path.join(home, "data"));
    this.masterPath = path.join(this.dataRoot, "events.jsonl");
    this.deletedPath = path.join(this.dataRoot, "deleted.json");
    this.memoryPath = path.join(this.dataRoot, "memory", "MEMORY.md");
    this.events = [];
    this.byId = new Map();
    this.deleted = new Set();
    this.load();
  }

  load() {
    if (fs.existsSync(this.deletedPath)) {
      try {
        const values = JSON.parse(fs.readFileSync(this.deletedPath, "utf8"));
        this.deleted = new Set(Array.isArray(values) ? values : []);
      } catch {
        this.deleted = new Set();
      }
    }
    if (!fs.existsSync(this.masterPath)) return;
    for (const line of fs.readFileSync(this.masterPath, "utf8").split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const event = JSON.parse(line);
        if (!event?.id || this.byId.has(event.id)) continue;
        this.events.push(event);
        this.byId.set(event.id, event);
      } catch {
        // Preserve the append-only file and ignore only the malformed record.
      }
    }
  }

  normalize(input = {}) {
    const sourceId = oneLine(input.sourceId || input.externalId || "");
    const id = sourceId
      ? `evt_${sourceHash(`${input.source || "unknown"}:${sourceId}`)}`
      : `evt_${Date.now().toString(36)}_${crypto.randomBytes(6).toString("hex")}`;
    const type = ALLOWED_TYPES.has(input.type) ? input.type : "note";
    const title = redact(oneLine(input.title).slice(0, 500), this.config.privacy);
    const text = redact(String(input.text ?? input.content ?? "").slice(0, 65536), this.config.privacy);
    const tags = Array.isArray(input.tags)
      ? [...new Set(input.tags.map(oneLine).filter(Boolean))].slice(0, 32)
      : [];
    const importanceNumber = Number(input.importance ?? 0.5);
    return {
      id,
      timestamp: safeTimestamp(input.timestamp),
      type,
      title,
      text,
      tags,
      importance: Math.max(0, Math.min(1, Number.isFinite(importanceNumber) ? importanceNumber : 0.5)),
      source: oneLine(input.source || "manual").slice(0, 120),
      sourceId: sourceId.slice(0, 500),
      metadata: input.metadata && typeof input.metadata === "object" ? input.metadata : {},
      createdAt: new Date().toISOString()
    };
  }

  remember(input, options = {}) {
    const event = this.normalize(input);
    if (!event.text && !event.title) throw new Error("Memory content cannot be empty.");
    if (this.byId.has(event.id)) return { event: this.byId.get(event.id), duplicate: true };
    fs.appendFileSync(this.masterPath, `${JSON.stringify(event)}\n`, "utf8");
    const day = event.timestamp.slice(0, 10);
    fs.appendFileSync(path.join(this.dataRoot, "events", `${day}.jsonl`), `${JSON.stringify(event)}\n`, "utf8");
    this.events.push(event);
    this.byId.set(event.id, event);
    if (!options.deferRender) this.rebuildViews();
    return { event, duplicate: false };
  }

  forget(id) {
    if (!this.byId.has(id)) return false;
    this.deleted.add(id);
    atomicWrite(this.deletedPath, `${JSON.stringify([...this.deleted], null, 2)}\n`);
    fs.appendFileSync(
      path.join(this.dataRoot, "tombstones.jsonl"),
      `${JSON.stringify({ id, deletedAt: new Date().toISOString() })}\n`,
      "utf8"
    );
    this.rebuildViews();
    return true;
  }

  get(id) {
    if (this.deleted.has(id)) return null;
    return this.byId.get(id) || null;
  }

  activeEvents() {
    return this.events.filter((event) => !this.deleted.has(event.id));
  }

  recent(limit = 20, since) {
    const bounded = Math.max(1, Math.min(200, Number(limit) || 20));
    const threshold = since ? new Date(since).getTime() : Number.NEGATIVE_INFINITY;
    return this.activeEvents()
      .filter((event) => new Date(event.timestamp).getTime() >= threshold)
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
      .slice(0, bounded);
  }

  search(query, limit = 8, types = []) {
    const bounded = Math.max(1, Math.min(50, Number(limit) || 8));
    const queryTokens = tokenize(query);
    const typeSet = new Set(Array.isArray(types) ? types : []);
    const now = Date.now();
    return this.activeEvents()
      .filter((event) => typeSet.size === 0 || typeSet.has(event.type))
      .map((event) => {
        const titleTokens = tokenize(event.title);
        const bodyTokens = tokenize(`${event.text} ${event.tags.join(" ")} ${event.source}`);
        let lexical = 0;
        for (const token of queryTokens) {
          if (titleTokens.has(token)) lexical += 4;
          if (bodyTokens.has(token)) lexical += 1;
        }
        const ageDays = Math.max(0, (now - new Date(event.timestamp).getTime()) / 86400000);
        const recency = 1 / (1 + ageDays / 30);
        const score = lexical + event.importance * 0.75 + recency * 0.25;
        return { ...event, score: Number(score.toFixed(4)) };
      })
      .filter((event) => queryTokens.size === 0 || event.score > 0.2)
      .sort((a, b) => b.score - a.score || b.timestamp.localeCompare(a.timestamp))
      .slice(0, bounded);
  }

  stats() {
    const active = this.activeEvents();
    const byType = {};
    const bySource = {};
    for (const event of active) {
      byType[event.type] = (byType[event.type] || 0) + 1;
      bySource[event.source] = (bySource[event.source] || 0) + 1;
    }
    return {
      active: active.length,
      deleted: this.deleted.size,
      totalStored: this.events.length,
      byType,
      bySource,
      memoryFile: this.memoryPath,
      updatedAt: new Date().toISOString()
    };
  }

  rebuildViews() {
    const active = this.activeEvents().sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    const durable = active
      .filter((event) => event.importance >= 0.65 || ["preference", "decision", "project", "person", "task"].includes(event.type))
      .slice(0, 300);
    const lines = [
      "# Personal Memory",
      "",
      `Updated: ${new Date().toISOString()}`,
      "",
      "> Generated from the append-only event log. Use the event id to inspect or forget an item.",
      ""
    ];
    const grouped = new Map();
    for (const event of durable) {
      if (!grouped.has(event.type)) grouped.set(event.type, []);
      grouped.get(event.type).push(event);
    }
    for (const [type, events] of grouped) {
      lines.push(`## ${type}`, "");
      for (const event of events) {
        const content = oneLine(event.title ? `${event.title}: ${event.text}` : event.text).slice(0, 1200);
        lines.push(`- ${event.timestamp.slice(0, 10)} [${event.id}] ${content}`);
      }
      lines.push("");
    }
    if (durable.length === 0) lines.push("No durable memories yet.", "");
    atomicWrite(this.memoryPath, `${lines.join("\n")}\n`);

    const days = new Map();
    for (const event of active.slice(0, 2000)) {
      const day = event.timestamp.slice(0, 10);
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(event);
    }
    for (const [day, events] of days) {
      const daily = [`# ${day}`, ""];
      for (const event of events) {
        daily.push(`- ${event.timestamp.slice(11, 19)} **${event.type}** [${event.source}] ${oneLine(event.title || event.text).slice(0, 700)} <!-- ${event.id} -->`);
      }
      atomicWrite(path.join(this.dataRoot, "memory", "daily", `${day}.md`), `${daily.join("\n")}\n`);
    }
  }
}

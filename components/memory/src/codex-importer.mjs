import fs from "node:fs";
import path from "node:path";

function listJsonlFiles(root) {
  if (!fs.existsSync(root)) return [];
  const result = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile() && entry.name.endsWith(".jsonl")) result.push(full);
    }
  }
  return result;
}

function messageText(payload) {
  if (!payload || payload.type !== "message" || !Array.isArray(payload.content)) return "";
  return payload.content
    .filter((item) => ["input_text", "output_text", "text"].includes(item?.type))
    .map((item) => item.text || "")
    .join("\n")
    .trim();
}

function shouldSkip(text) {
  const trimmed = text.trim();
  return !trimmed
    || trimmed.startsWith("<environment_context>")
    || trimmed.startsWith("<skills_instructions>")
    || trimmed.startsWith("<app-context>")
    || trimmed.length > 65536;
}

export class CodexImporter {
  constructor(store, home, options, logger = console) {
    this.store = store;
    this.options = options;
    this.logger = logger;
    this.statePath = path.join(home, "state", "codex-import.json");
    this.state = fs.existsSync(this.statePath)
      ? JSON.parse(fs.readFileSync(this.statePath, "utf8"))
      : { offsets: {} };
    this.timer = null;
    this.running = false;
  }

  saveState() {
    const temp = `${this.statePath}.tmp`;
    fs.writeFileSync(temp, `${JSON.stringify(this.state, null, 2)}\n`, "utf8");
    fs.renameSync(temp, this.statePath);
  }

  async scan() {
    if (this.running || !this.options.enabled) return 0;
    this.running = true;
    let imported = 0;
    try {
      for (const file of listJsonlFiles(this.options.sessionsRoot)) {
        const stat = fs.statSync(file);
        const previous = Math.min(Number(this.state.offsets[file] || 0), stat.size);
        if (previous === stat.size) continue;
        const length = stat.size - previous;
        const handle = fs.openSync(file, "r");
        const buffer = Buffer.alloc(length);
        fs.readSync(handle, buffer, 0, length, previous);
        fs.closeSync(handle);
        for (const line of buffer.toString("utf8").split(/\r?\n/)) {
          if (!line.trim()) continue;
          try {
            const record = JSON.parse(line);
            const payload = record.type === "response_item" ? record.payload : null;
            if (!payload || !["user", "assistant"].includes(payload.role)) continue;
            if (payload.role === "assistant" && !this.options.includeAssistant) continue;
            const text = messageText(payload);
            if (shouldSkip(text)) continue;
            const sourceId = `${path.basename(file)}:${record.ordinal ?? payload.id ?? record.timestamp}`;
            const result = this.store.remember({
              type: "message",
              title: payload.role === "user" ? "User message" : "Assistant message",
              text,
              source: `codex:${payload.role}`,
              sourceId,
              timestamp: record.timestamp,
              importance: payload.role === "user" ? 0.58 : 0.42,
              tags: ["codex", payload.role],
              metadata: { role: payload.role, sessionFile: path.basename(file) }
            }, { deferRender: true });
            if (!result.duplicate) imported += 1;
          } catch {
            // The active rollout can end with a partial line. It will be retried after another append.
          }
        }
        this.state.offsets[file] = stat.size;
      }
      if (imported > 0) this.store.rebuildViews();
      this.saveState();
      return imported;
    } finally {
      this.running = false;
    }
  }

  async start() {
    const first = await this.scan();
    this.logger.log?.(`Codex importer ready; imported ${first} new messages.`);
    this.timer = setInterval(() => this.scan().catch((error) => this.logger.error?.(error)), this.options.intervalMs);
    this.timer.unref?.();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
  }
}

import fs from "node:fs";
import path from "node:path";
import { zstdDecompressSync } from "node:zlib";

const ZSTD_MAGIC = 0xfd2fb528;

function listSessionFiles(root) {
  if (!fs.existsSync(root)) return [];
  const result = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile() && (entry.name.endsWith(".jsonl.zstd") || entry.name.endsWith(".jsonl"))) result.push(full);
    }
  }
  return result;
}

export function scanZstdFrames(buffer) {
  const frames = [];
  let offset = 0;
  while (offset < buffer.length) {
    const start = offset;
    if (buffer.length - offset < 4) return { frames, tornStart: start };
    if (buffer.readUInt32LE(offset) !== ZSTD_MAGIC) throw new Error(`Invalid Zstandard frame magic at byte ${offset}`);
    offset += 4;
    if (offset === buffer.length) return { frames, tornStart: start };
    const descriptor = buffer.readUInt8(offset);
    offset += 1;
    if ((descriptor & 24) !== 0) throw new Error(`Invalid Zstandard frame descriptor at byte ${offset - 1}`);
    const contentSizeFlag = descriptor >>> 6;
    const singleSegment = (descriptor & 32) !== 0;
    const checksum = (descriptor & 4) !== 0;
    const dictionaryFlag = descriptor & 3;
    const dictionaryBytes = dictionaryFlag === 3 ? 4 : dictionaryFlag;
    const contentSizeBytes = contentSizeFlag === 0 ? (singleSegment ? 1 : 0) : 1 << contentSizeFlag;
    const remainingHeaderBytes = (singleSegment ? 0 : 1) + dictionaryBytes + contentSizeBytes;
    if (buffer.length - offset < remainingHeaderBytes) return { frames, tornStart: start };
    offset += remainingHeaderBytes;
    for (;;) {
      if (buffer.length - offset < 3) return { frames, tornStart: start };
      const blockHeader = buffer.readUIntLE(offset, 3);
      offset += 3;
      const lastBlock = (blockHeader & 1) !== 0;
      const blockType = (blockHeader >>> 1) & 3;
      const blockSize = blockHeader >>> 3;
      if (blockType === 3) throw new Error(`Invalid Zstandard block type at byte ${offset - 3}`);
      const payloadBytes = blockType === 1 ? 1 : blockSize;
      if (buffer.length - offset < payloadBytes) return { frames, tornStart: start };
      offset += payloadBytes;
      if (lastBlock) break;
    }
    if (checksum) {
      if (buffer.length - offset < 4) return { frames, tornStart: start };
      offset += 4;
    }
    frames.push({ start, end: offset });
  }
  return { frames };
}

function contentText(content) {
  if (!Array.isArray(content)) return "";
  return content
    .filter((item) => item?.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n")
    .trim();
}

export function parseDshEvent(record, sessionName, includeAssistant = true) {
  if (!record || !["user/message", "assistant/message"].includes(record.type)) return null;
  const role = record.type === "user/message" ? "user" : "assistant";
  if (role === "assistant" && !includeAssistant) return null;
  const message = role === "user" ? record.data : record.data?.message;
  const expectedSource = role === "user" ? "user" : "model";
  if (!message || message.source?.kind !== expectedSource) return null;
  const text = contentText(message.content);
  if (!text || text.length > 65536) return null;
  return {
    type: "message",
    title: role === "user" ? "DSH user message" : "DSH assistant message",
    text,
    source: `dsh:${role}`,
    sourceId: message.id || `${sessionName}:${record.seq ?? record.time}`,
    timestamp: record.time,
    importance: role === "user" ? 0.58 : 0.42,
    tags: ["dsh", role],
    metadata: { role, session: sessionName }
  };
}

function completePlaintextChunk(buffer) {
  const lastNewline = Math.max(buffer.lastIndexOf(10), buffer.lastIndexOf(13));
  if (lastNewline < 0) return { text: "", consumed: 0 };
  return { text: buffer.subarray(0, lastNewline + 1).toString("utf8"), consumed: lastNewline + 1 };
}

function completeZstdChunk(buffer) {
  const { frames, tornStart } = scanZstdFrames(buffer);
  const text = frames.map(({ start, end }) => zstdDecompressSync(buffer.subarray(start, end)).toString("utf8")).join("");
  return { text, consumed: tornStart ?? buffer.length };
}

export class DshImporter {
  constructor(store, home, options, logger = console) {
    this.store = store;
    this.options = options;
    this.logger = logger;
    this.statePath = path.join(home, "state", "dsh-import.json");
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
      for (const file of listSessionFiles(this.options.sessionsRoot)) {
        const stat = fs.statSync(file);
        const previous = Math.min(Number(this.state.offsets[file] || 0), stat.size);
        if (previous === stat.size) continue;
        const length = stat.size - previous;
        const handle = fs.openSync(file, "r");
        const buffer = Buffer.alloc(length);
        fs.readSync(handle, buffer, 0, length, previous);
        fs.closeSync(handle);
        let chunk;
        try {
          chunk = file.endsWith(".zstd") ? completeZstdChunk(buffer) : completePlaintextChunk(buffer);
        } catch (error) {
          this.logger.error?.(`DSH importer skipped unreadable append in ${file}: ${error.message}`);
          continue;
        }
        const sessionName = path.basename(path.dirname(file));
        for (const line of chunk.text.split(/\r?\n/)) {
          if (!line.trim()) continue;
          try {
            const input = parseDshEvent(JSON.parse(line), sessionName, this.options.includeAssistant);
            if (!input) continue;
            const result = this.store.remember(input, { deferRender: true });
            if (!result.duplicate) imported += 1;
          } catch {
            // Ignore one malformed event without discarding the rest of a valid frame.
          }
        }
        this.state.offsets[file] = previous + chunk.consumed;
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
    this.logger.log?.(`DSH importer ready; imported ${first} new messages.`);
    this.timer = setInterval(() => this.scan().catch((error) => this.logger.error?.(error)), this.options.intervalMs);
    this.timer.unref?.();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
  }
}

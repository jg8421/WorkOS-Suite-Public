import test from "node:test";
import assert from "node:assert/strict";
import { zstdCompressSync } from "node:zlib";
import { parseDshEvent, scanZstdFrames } from "../src/dsh-importer.mjs";

test("scans concatenated DSH Zstandard frames", () => {
  const first = zstdCompressSync(Buffer.from('{"type":"session"}\n'));
  const second = zstdCompressSync(Buffer.from('{"type":"user/message"}\n'));
  const combined = Buffer.concat([first, second]);
  const result = scanZstdFrames(combined);
  assert.deepEqual(result.frames, [
    { start: 0, end: first.length },
    { start: first.length, end: combined.length }
  ]);
  assert.equal(result.tornStart, undefined);
});

test("imports only real DSH user and model messages", () => {
  const user = parseDshEvent({
    type: "user/message",
    seq: 7,
    time: "2026-09-29T00:00:00.000Z",
    data: { id: "user-1", source: { kind: "user" }, content: [{ type: "text", text: "remember this" }] }
  }, "session-1");
  const runtime = parseDshEvent({
    type: "user/message",
    data: { id: "runtime-1", source: { kind: "runtime-context" }, content: [{ type: "text", text: "hidden runtime context" }] }
  }, "session-1");
  const assistant = parseDshEvent({
    type: "assistant/message",
    seq: 8,
    data: { message: { id: "assistant-1", source: { kind: "model" }, content: [
      { type: "text", text: "answer" },
      { type: "tool_call", name: "shell", arguments: "secret arguments" }
    ] } }
  }, "session-1");
  assert.equal(user.text, "remember this");
  assert.equal(runtime, null);
  assert.equal(assistant.text, "answer");
});

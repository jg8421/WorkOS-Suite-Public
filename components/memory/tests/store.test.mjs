import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { MemoryStore } from "../src/store.mjs";

function fixture() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "personal-memory-test-"));
  for (const relative of ["data/events", "data/memory/daily"]) fs.mkdirSync(path.join(home, relative), { recursive: true });
  return { home, store: new MemoryStore(home, { privacy: { redactSecrets: true, redactOtp: true } }) };
}

test("remember, deduplicate, search, and forget", () => {
  const { home, store } = fixture();
  const first = store.remember({ text: "Example user prefers concise investment summaries", type: "preference", source: "test", sourceId: "one", importance: 0.9 });
  const duplicate = store.remember({ text: "changed text", source: "test", sourceId: "one" });
  assert.equal(first.duplicate, false);
  assert.equal(duplicate.duplicate, true);
  assert.equal(store.search("investment concise")[0].id, first.event.id);
  assert.equal(store.forget(first.event.id), true);
  assert.equal(store.search("investment concise").length, 0);
  assert.ok(fs.existsSync(path.join(home, "data", "memory", "MEMORY.md")));
});

test("redacts secrets and OTPs", () => {
  const { store } = fixture();
  const { event } = store.remember({ text: "password=hunter2 verification code 123456", source: "test" });
  assert.equal(event.text.includes("hunter2"), false);
  assert.equal(event.text.includes("123456"), false);
});

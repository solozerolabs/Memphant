/**
 * Node-stdlib smoke test for the pi extension (no framework).
 *
 * Runs the real extension against a STUBBED recall CLI (a tiny python script on
 * disk, selected via the MEMPHANT_RECALL_CLI env override), captures the
 * `before_agent_start` handler it registers, and asserts the handler returns a
 * `{ systemPrompt }` that appends the stub's block to the base prompt — and
 * returns it unchanged when the CLI fails or returns empty.
 *
 * Run: node --test plugins/pi-memphant/index.test.ts
 */

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import extension from "./index.ts";

const STUB_BLOCK = "<memphant_memory>\nAdvisory context.\nSTUB-CARD\n</memphant_memory>";

function withStub(body: string, run: () => void | Promise<void>) {
  const dir = mkdtempSync(join(tmpdir(), "pi-memphant-"));
  const cli = join(dir, "stub_recall.py");
  writeFileSync(cli, body, "utf-8");
  const prev = process.env.MEMPHANT_RECALL_CLI;
  process.env.MEMPHANT_RECALL_CLI = cli;
  return Promise.resolve(run()).finally(() => {
    if (prev === undefined) delete process.env.MEMPHANT_RECALL_CLI;
    else process.env.MEMPHANT_RECALL_CLI = prev;
    rmSync(dir, { recursive: true, force: true });
  });
}

const HIT_STUB = `import sys\nsys.stdout.write(${JSON.stringify(STUB_BLOCK)})\n`;
const EMPTY_STUB = `pass\n`;
const FAIL_STUB = `import sys\nsys.exit(1)\n`;

/** Register the extension and return the before_agent_start handler. */
function loadHandler() {
  const handlers: Record<string, any> = {};
  const pi = { on: (name: string, fn: any) => { handlers[name] = fn; } };
  extension(pi as any);
  assert.equal(typeof handlers["before_agent_start"], "function");
  return handlers["before_agent_start"];
}

test("before_agent_start appends the advisory block to the system prompt", async () => {
  await withStub(HIT_STUB, async () => {
    const handler = loadHandler();
    const result = await handler({ systemPrompt: "BASE PROMPT", prompt: "do a thing" }, {});
    assert.equal(result.systemPrompt, `BASE PROMPT\n${STUB_BLOCK}`);
  });
});

test("before_agent_start returns {} (no change) on an honest-empty recall", async () => {
  await withStub(EMPTY_STUB, async () => {
    const handler = loadHandler();
    const result = await handler({ systemPrompt: "BASE PROMPT" }, {});
    assert.deepEqual(result, {});
  });
});

test("before_agent_start returns {} when the recall CLI fails", async () => {
  await withStub(FAIL_STUB, async () => {
    const handler = loadHandler();
    const result = await handler({ systemPrompt: "BASE PROMPT" }, {});
    assert.deepEqual(result, {});
  });
});

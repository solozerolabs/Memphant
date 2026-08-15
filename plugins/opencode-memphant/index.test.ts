/**
 * Node-stdlib smoke test for the opencode adapter (no framework).
 *
 * Runs the real plugin against a STUBBED recall CLI (a tiny python script on
 * disk, selected via the MEMPHANT_RECALL_CLI env override) and asserts that the
 * `experimental.chat.system.transform` hook pushes the stub's block onto
 * `output.system` — and pushes nothing when the CLI fails or returns empty.
 *
 * Run: node --test plugins/opencode-memphant/index.test.ts
 */

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import plugin, { recallBlock } from "./index.ts";

const STUB_BLOCK = "<memphant_memory>\nAdvisory context.\nSTUB-CARD\n</memphant_memory>";

function withStub(body: string, run: (cliPath: string) => void | Promise<void>) {
  const dir = mkdtempSync(join(tmpdir(), "opencode-memphant-"));
  const cli = join(dir, "stub_recall.py");
  writeFileSync(cli, body, "utf-8");
  const prev = process.env.MEMPHANT_RECALL_CLI;
  process.env.MEMPHANT_RECALL_CLI = cli;
  return Promise.resolve(run(cli)).finally(() => {
    if (prev === undefined) delete process.env.MEMPHANT_RECALL_CLI;
    else process.env.MEMPHANT_RECALL_CLI = prev;
    rmSync(dir, { recursive: true, force: true });
  });
}

// A stub that prints the known block to stdout and exits 0.
const HIT_STUB = `import sys\nsys.stdout.write(${JSON.stringify(STUB_BLOCK)})\n`;
// A stub that prints nothing (honest-empty).
const EMPTY_STUB = `pass\n`;
// A stub that fails.
const FAIL_STUB = `import sys\nsys.exit(1)\n`;

test("transform pushes the recalled advisory block onto output.system", async () => {
  await withStub(HIT_STUB, async () => {
    const hooks: any = await plugin({ directory: "/tmp/proj" } as any);
    const transform = hooks["experimental.chat.system.transform"];
    const output: any = { system: ["You are a helpful assistant."] };
    await transform({}, output);
    assert.equal(output.system.length, 2);
    assert.equal(output.system[1], STUB_BLOCK);
  });
});

test("transform initializes output.system when absent", async () => {
  await withStub(HIT_STUB, async () => {
    const hooks: any = await plugin({ directory: "/tmp/proj" } as any);
    const transform = hooks["experimental.chat.system.transform"];
    const output: any = {};
    await transform({}, output);
    assert.deepEqual(output.system, [STUB_BLOCK]);
  });
});

test("transform pushes nothing on an honest-empty recall", async () => {
  await withStub(EMPTY_STUB, async () => {
    const hooks: any = await plugin({ directory: "/tmp/proj" } as any);
    const transform = hooks["experimental.chat.system.transform"];
    const output: any = { system: ["base"] };
    await transform({}, output);
    assert.deepEqual(output.system, ["base"]);
  });
});

test("recallBlock fails safe (empty) when the CLI errors", async () => {
  await withStub(FAIL_STUB, () => {
    assert.equal(recallBlock("", "/tmp/proj"), "");
  });
});

/**
 * Node-stdlib smoke test for the pi CAPTURE adapters (no framework).
 *
 * Runs the real extension capture helpers and hooks against a STUBBED capture
 * CLI (records its stdin payload to a file via the MEMPHANT_CAPTURE_CLI env
 * override) and asserts:
 *  - sessionCapture pipes a `source=summary` payload;
 *  - mirrorCapture pipes a `source=mirror` payload;
 *  - the `turn_end` hook reads `ctx.sessionManager.getEntries()` and captures;
 *  - the `tool_call` hook copies a MEMORY.md write, ignores other files, and
 *    NEVER throws (ALLOW-AND-COPY).
 *
 * Run: node --test plugins/pi-memphant/capture.test.ts
 */

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import extension, { sessionCapture, mirrorCapture, captureViaCli } from "./index.ts";

const RECORDER = [
  "import os, sys",
  "out = os.environ['CAP_OUT']",
  "data = sys.stdin.read()",
  "open(out, 'a').write(data + '\\n')",
  "sys.exit(int(os.environ.get('CAP_RC', '0')))",
].join("\n");

const FAIL = "import sys\nsys.exit(1)\n";

function withStub(body: string, rc: string, run: (outPath: string) => void | Promise<void>) {
  const dir = mkdtempSync(join(tmpdir(), "pi-capture-"));
  const cli = join(dir, "stub_capture.py");
  const out = join(dir, "payloads.jsonl");
  writeFileSync(cli, body, "utf-8");
  const prev = {
    cli: process.env.MEMPHANT_CAPTURE_CLI,
    out: process.env.CAP_OUT,
    rc: process.env.CAP_RC,
  };
  process.env.MEMPHANT_CAPTURE_CLI = cli;
  process.env.CAP_OUT = out;
  process.env.CAP_RC = rc;
  const restore = () => {
    for (const [k, v] of [
      ["MEMPHANT_CAPTURE_CLI", prev.cli],
      ["CAP_OUT", prev.out],
      ["CAP_RC", prev.rc],
    ] as const) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    rmSync(dir, { recursive: true, force: true });
  };
  return Promise.resolve(run(out)).finally(restore);
}

function payloads(outPath: string): any[] {
  if (!existsSync(outPath)) return [];
  return readFileSync(outPath, "utf-8")
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l));
}

/** Register the extension and return its captured handlers by event name. */
function loadHandlers() {
  const handlers: Record<string, any> = {};
  const pi = { on: (name: string, fn: any) => { handlers[name] = fn; } };
  extension(pi as any);
  return handlers;
}

test("sessionCapture pipes a summary payload", async () => {
  await withStub(RECORDER, "0", (out) => {
    assert.equal(
      sessionCapture([{ role: "assistant", content: "We deploy with make deploy." }], "/repo"),
      true,
    );
    const [p] = payloads(out);
    assert.equal(p.source, "summary");
  });
});

test("mirrorCapture pipes a mirror payload", async () => {
  await withStub(RECORDER, "0", (out) => {
    assert.equal(mirrorCapture("A durable memory line.", "/repo"), true);
    const [p] = payloads(out);
    assert.equal(p.source, "mirror");
    assert.match(p.content, /durable memory/);
  });
});

test("captureViaCli fails safe when the CLI errors", async () => {
  await withStub(FAIL, "1", () => {
    assert.equal(captureViaCli({ source: "mirror", content: "x", cwd: "/r" }), false);
  });
});

test("turn_end reads sessionManager entries and captures a summary", async () => {
  await withStub(RECORDER, "0", async (out) => {
    const handlers = loadHandlers();
    const ctx = {
      directory: "/repo",
      sessionManager: {
        getEntries: () => [
          { role: "user", content: "how do we ship?" },
          { role: "assistant", content: "Tag the release and run make deploy on the host." },
        ],
      },
    };
    await handlers["turn_end"]({}, ctx);
    const [p] = payloads(out);
    assert.equal(p.source, "summary");
    assert.equal(p.messages.length, 2);
  });
});

test("tool_call copies a MEMORY.md write, ignores others, never throws", async () => {
  await withStub(RECORDER, "0", async (out) => {
    const handlers = loadHandlers();
    const ctx = { directory: "/repo" };
    await handlers["tool_call"]({ tool: "write", args: { filePath: "MEMORY.md", content: "A rule worth keeping." } }, ctx);
    await handlers["tool_call"]({ tool: "write", args: { filePath: "src/lib.rs", content: "fn x() {}" } }, ctx);
    // A malformed event must not throw.
    await handlers["tool_call"]({}, ctx);
    const ps = payloads(out);
    assert.equal(ps.length, 1);
    assert.equal(ps[0].source, "mirror");
  });
});

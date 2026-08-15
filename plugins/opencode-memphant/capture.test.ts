/**
 * Node-stdlib smoke test for the opencode CAPTURE adapters (no framework).
 *
 * Runs the real plugin capture helpers against a STUBBED capture CLI (a tiny
 * python script that records its stdin payload to a file, selected via the
 * MEMPHANT_CAPTURE_CLI env override) and asserts:
 *  - sessionCapture pipes a `source=summary` payload with the messages;
 *  - mirrorCapture pipes a `source=mirror` payload with the content;
 *  - captureViaCli fails safe (returns false) when the CLI errors;
 *  - the `tool.execute.before` hook copies a MEMORY.md write and IGNORES a
 *    non-memory file, and NEVER throws (ALLOW-AND-COPY).
 *
 * Run: node --test plugins/opencode-memphant/capture.test.ts
 */

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import plugin, { sessionCapture, mirrorCapture, captureViaCli } from "./index.ts";

/** A stub CLI that appends its stdin to $CAP_OUT and exits with $CAP_RC. */
const RECORDER = [
  "import os, sys",
  "out = os.environ['CAP_OUT']",
  "data = sys.stdin.read()",
  "open(out, 'a').write(data + '\\n')",
  "sys.exit(int(os.environ.get('CAP_RC', '0')))",
].join("\n");

const FAIL = "import sys\nsys.exit(1)\n";

function withStub(body: string, rc: string, run: (outPath: string) => void | Promise<void>) {
  const dir = mkdtempSync(join(tmpdir(), "opencode-capture-"));
  const cli = join(dir, "stub_capture.py");
  const out = join(dir, "payloads.jsonl");
  writeFileSync(cli, body, "utf-8");
  const prevCli = process.env.MEMPHANT_CAPTURE_CLI;
  const prevOut = process.env.CAP_OUT;
  const prevRc = process.env.CAP_RC;
  process.env.MEMPHANT_CAPTURE_CLI = cli;
  process.env.CAP_OUT = out;
  process.env.CAP_RC = rc;
  const restore = () => {
    if (prevCli === undefined) delete process.env.MEMPHANT_CAPTURE_CLI;
    else process.env.MEMPHANT_CAPTURE_CLI = prevCli;
    if (prevOut === undefined) delete process.env.CAP_OUT;
    else process.env.CAP_OUT = prevOut;
    if (prevRc === undefined) delete process.env.CAP_RC;
    else process.env.CAP_RC = prevRc;
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

test("sessionCapture pipes a summary payload with the messages", async () => {
  await withStub(RECORDER, "0", (out) => {
    const messages = [
      { role: "user", content: "how do we deploy?" },
      { role: "assistant", content: "Run make deploy on the release host." },
    ];
    assert.equal(sessionCapture(messages, "/repo"), true);
    const [p] = payloads(out);
    assert.equal(p.source, "summary");
    assert.equal(p.cwd, "/repo");
    assert.equal(p.messages.length, 2);
  });
});

test("mirrorCapture pipes a mirror payload with the content", async () => {
  await withStub(RECORDER, "0", (out) => {
    assert.equal(mirrorCapture("The token rotates on Fridays.", "/repo"), true);
    const [p] = payloads(out);
    assert.equal(p.source, "mirror");
    assert.match(p.content, /rotates on Fridays/);
  });
});

test("captureViaCli fails safe when the CLI errors", async () => {
  await withStub(FAIL, "1", () => {
    assert.equal(captureViaCli({ source: "mirror", content: "x", cwd: "/r" }), false);
  });
});

test("tool.execute.before copies a MEMORY.md write and ignores other files", async () => {
  await withStub(RECORDER, "0", async (out) => {
    const hooks: any = await plugin({ directory: "/repo" } as any);
    const before = hooks["tool.execute.before"];
    // A memory-file write is mirrored.
    await before(
      { tool: "write" },
      { args: { filePath: "/repo/MEMORY.md", content: "A durable rule about releases." } },
    );
    // A source-file write is ignored.
    await before(
      { tool: "write" },
      { args: { filePath: "/repo/src/main.rs", content: "fn main() {}" } },
    );
    const ps = payloads(out);
    assert.equal(ps.length, 1, "only the memory file is mirrored");
    assert.equal(ps[0].source, "mirror");
    assert.match(ps[0].content, /durable rule/);
  });
});

test("session.idle hook no-ops without messages and never throws", async () => {
  await withStub(RECORDER, "0", async (out) => {
    const hooks: any = await plugin({ directory: "/repo" } as any);
    await hooks["session.idle"]({});
    assert.equal(payloads(out).length, 0);
  });
});

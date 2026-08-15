/**
 * pi-memphant — MemPhant coding-agent memory for pi.
 *
 * A pi extension. It owns ONLY pi's injection envelope; all recall logic lives
 * in the shared Python core (`plugins/_shared/memphant_recall.py`), invoked as a
 * CLI (pi extensions are TypeScript and cannot import Python).
 *
 * pi has NO MCP support, so this extension is the ONLY memory path on pi — there
 * is no voluntary tool-call fallback. It hooks `before_agent_start`, which fires
 * after the user submits a prompt and before the agent loop, and can modify the
 * system prompt. We append one delimited advisory block built from a MemPhant
 * recall.
 *
 * Fail-safe: any recall error (or an unconfigured endpoint) appends zero bytes
 * and returns the system prompt unchanged, never breaking the turn.
 */

import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const EXT_DIR = dirname(fileURLToPath(import.meta.url));

/** Path to the shared recall CLI. Overridable via env for tests. */
function recallCliPath(): string {
  return (
    process.env.MEMPHANT_RECALL_CLI ||
    join(EXT_DIR, "..", "_shared", "memphant_recall.py")
  );
}

/**
 * Shell out to the shared recall CLI and return the advisory block, or "" on
 * any failure / honest-empty. Never throws.
 */
export function recallBlock(prompt: string, cwd: string): string {
  try {
    const result = spawnSync(
      "python3",
      [recallCliPath(), "--prompt", prompt, "--cwd", cwd],
      { encoding: "utf-8", timeout: 5000, env: process.env },
    );
    if (result.status !== 0 || !result.stdout) return "";
    return result.stdout.trim();
  } catch {
    return "";
  }
}

/**
 * pi extension entrypoint. `event.systemPrompt` is the current system prompt;
 * returning `{ systemPrompt }` replaces it. `event.prompt` (when present) is the
 * user's submitted prompt; we fall back to the working directory for scope.
 */
export default (pi: any) => {
  pi.on("before_agent_start", async (event: any, ctx: any) => {
    const prompt = typeof event?.prompt === "string" ? event.prompt : "";
    const cwd = (ctx && ctx.directory) || process.cwd();
    const block = recallBlock(prompt, cwd);
    if (!block) return {};
    const base = typeof event?.systemPrompt === "string" ? event.systemPrompt : "";
    return { systemPrompt: `${base}\n${block}` };
  });
};

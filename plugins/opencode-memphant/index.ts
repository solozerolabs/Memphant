/**
 * opencode-memphant — MemPhant coding-agent memory for opencode.
 *
 * A thin adapter: it owns ONLY opencode's injection envelope. All recall logic
 * lives in the shared Python core (`plugins/_shared/memphant_recall.py`), which
 * this plugin invokes as a CLI (opencode plugins are TypeScript and cannot
 * import Python).
 *
 * Injection point: the `experimental.chat.system.transform` hook fires just
 * before the model call and lets a plugin append system-prompt entries — no
 * tool, no voluntary uptake required. We push one delimited advisory block
 * built from a MemPhant recall scoped to the session directory.
 *
 * NOTE (experimental API): `experimental.chat.system.transform` is an
 * experimental opencode surface and is not covered by the public plugin docs at
 * the time of writing. It is implemented here to the shape used by the working
 * memsearch reference plugin: `(input, output) => void`, where `output.system`
 * is a string array of system-prompt entries. If opencode changes this shape,
 * update this single hook. See README.
 *
 * Fail-safe: any recall error (or an unconfigured endpoint) pushes zero bytes
 * and never breaks the turn.
 */

import type { Plugin } from "@opencode-ai/plugin";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url));

/** Path to the shared recall CLI. Overridable via env for tests. */
function recallCliPath(): string {
  return (
    process.env.MEMPHANT_RECALL_CLI ||
    join(PLUGIN_DIR, "..", "_shared", "memphant_recall.py")
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

const MemphantPlugin: Plugin = async ({ directory, worktree }) => {
  // worktree can be "/" for global projects — prefer a real directory.
  const cwd =
    (worktree && worktree !== "/" ? worktree : "") || directory || process.cwd();

  return {
    "experimental.chat.system.transform": async (_input: any, output: any) => {
      // opencode fires this before the model call with no user prompt in a
      // stable shape, so we recall on the session directory (a cold-start
      // scope). The block is optional advisory context.
      const block = recallBlock("", cwd);
      if (!block) return;
      if (Array.isArray(output.system)) {
        output.system.push(block);
      } else {
        output.system = [block];
      }
    },
  };
};

export default MemphantPlugin;

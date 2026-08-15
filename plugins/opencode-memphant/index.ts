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

/** Path to the shared capture CLI (`build_capture`). Overridable for tests. */
function captureCliPath(): string {
  return (
    process.env.MEMPHANT_CAPTURE_CLI ||
    join(PLUGIN_DIR, "..", "_shared", "memphant_capture.py")
  );
}

/**
 * Pipe a capture payload to the shared capture CLI on stdin. The CLI owns ALL
 * capture logic (filters, secret redaction, summarize, POST); this is a thin
 * shell-out, the write-side twin of `recallBlock`. Returns true when the CLI
 * exits 0. NEVER throws — capture must never break a host turn.
 */
export function captureViaCli(payload: Record<string, unknown>): boolean {
  try {
    const result = spawnSync("python3", [captureCliPath()], {
      input: JSON.stringify(payload),
      encoding: "utf-8",
      timeout: 30000,
      env: process.env,
    });
    return result.status === 0;
  } catch {
    return false;
  }
}

/** Capture the last turn of a session as `source=summary`. */
export function sessionCapture(messages: unknown[], cwd: string): boolean {
  if (!Array.isArray(messages) || messages.length === 0) return false;
  return captureViaCli({ source: "summary", cwd, messages });
}

/** Copy a memory-file write as `source=mirror` (never blocks the write). */
export function mirrorCapture(content: string, cwd: string): boolean {
  if (!content || !content.trim()) return false;
  return captureViaCli({ source: "mirror", cwd, content });
}

const MIRROR_FILES = (process.env.MEMPHANT_CAPTURE_MIRROR_FILES || "MEMORY.md,AGENTS.md")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || "";
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

    // Session-summarize (spine). opencode fires `session.idle` when a turn
    // settles; the event carries the session's messages. We hand the last turn
    // to the shared core, which summarizes and posts it as `source=summary`.
    //
    // EXPERIMENTAL-API NOTE: the `session.idle` payload shape is not covered by
    // a stable public contract at the time of writing; we read `messages` /
    // `parts` defensively and no-op when absent. Update this single seam if
    // opencode changes the event.
    "session.idle": async (input: any) => {
      const messages =
        (input && (input.messages || input.parts || input.session?.messages)) || [];
      sessionCapture(messages as unknown[], cwd);
    },

    // File-mirror (augment). ALLOW-AND-COPY: we observe a memory-file write and
    // copy it; we NEVER block it. `tool.execute.before` fires before the write
    // runs; returning normally lets it proceed.
    "tool.execute.before": async (input: any, output: any) => {
      try {
        const tool = input?.tool || output?.tool || "";
        if (!/^(write|edit|multiedit)$/i.test(String(tool))) return;
        const args = output?.args || input?.args || {};
        const filePath = args.filePath || args.file_path || args.path || "";
        if (!filePath || !MIRROR_FILES.includes(baseName(String(filePath)))) return;
        const content =
          args.content ?? args.newString ?? args.new_string ?? args.replacement ?? "";
        mirrorCapture(String(content), cwd);
      } catch {
        // never let a capture error affect the host write
      }
    },
  };
};

export default MemphantPlugin;

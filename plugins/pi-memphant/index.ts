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

/** Path to the shared capture CLI (`build_capture`). Overridable for tests. */
function captureCliPath(): string {
  return (
    process.env.MEMPHANT_CAPTURE_CLI ||
    join(EXT_DIR, "..", "_shared", "memphant_capture.py")
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

/** Normalize a pi history entry to a `{role, content}` message. */
function entryToMessage(entry: any): { role: string; content: unknown } | null {
  if (!entry || typeof entry !== "object") return null;
  const role = entry.role || entry.type;
  if (role !== "user" && role !== "assistant") return null;
  return { role, content: entry.content ?? entry.text ?? "" };
}

/**
 * pi extension entrypoint. `event.systemPrompt` is the current system prompt;
 * returning `{ systemPrompt }` replaces it. `event.prompt` (when present) is the
 * user's submitted prompt; we fall back to the working directory for scope.
 *
 * pi has NO MCP support, so this extension is the ONLY memory path on pi, for
 * BOTH recall (before_agent_start) and capture (turn_end + tool_call).
 *
 * EXPERIMENTAL-API NOTE: pi's `turn_end`/`agent_end` and `tool_call` payloads
 * and `ctx.sessionManager.getEntries()` are not covered by a stable public
 * contract at the time of writing; each is read defensively and no-ops when
 * absent. Update these seams if pi changes them.
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

  // Session-summarize (spine): at the end of a turn/agent run, read the history
  // and capture the last turn as `source=summary`.
  const onSessionEnd = async (event: any, ctx: any) => {
    const cwd = (ctx && ctx.directory) || process.cwd();
    let entries: any[] = [];
    try {
      const raw = ctx?.sessionManager?.getEntries?.();
      if (Array.isArray(raw)) entries = raw;
    } catch {
      entries = [];
    }
    if (entries.length === 0 && Array.isArray(event?.messages)) entries = event.messages;
    const messages = entries
      .map(entryToMessage)
      .filter((m): m is { role: string; content: unknown } => m !== null);
    sessionCapture(messages, cwd);
    return {};
  };
  pi.on("turn_end", onSessionEnd);
  pi.on("agent_end", onSessionEnd);

  // File-mirror (augment): ALLOW-AND-COPY a memory-file write. Never blocks.
  pi.on("tool_call", async (event: any, ctx: any) => {
    try {
      const cwd = (ctx && ctx.directory) || process.cwd();
      const tool = event?.tool || event?.name || "";
      if (!/^(write|edit|multiedit)$/i.test(String(tool))) return {};
      const args = event?.args || event?.input || {};
      const filePath = args.filePath || args.file_path || args.path || "";
      if (!filePath || !MIRROR_FILES.includes(baseName(String(filePath)))) return {};
      const content =
        args.content ?? args.newString ?? args.new_string ?? args.replacement ?? "";
      mirrorCapture(String(content), cwd);
    } catch {
      // never let a capture error affect the host turn
    }
    return {};
  });
};

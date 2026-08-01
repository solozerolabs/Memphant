#!/usr/bin/env python3
"""S4 Arm 1 — the agentic-search control: an agent with `grep` and nothing else.

The control that matters, because it is what a competent coding agent does by
default and what a caller would do without us. No MemPhant, no index, no
embeddings, no memory state: a bounded tool loop (`grep` / `read_event` /
`list_events`) over the same attempt's raw events MemPhant's recall is bound to,
terminating in a ranked selection of at most ten events.

Published prior this arm tests (see the preregistration): SWE-Explore measured
agent-with-`grep` HitFile@5 0.667 against dense RAG 0.088 and BM25 0.079, and
ContextBench found plain bash beating every embedding-based system.

Two engines:

* ``--engine stub`` — a deterministic local tool-calling model. **$0.** It
  exercises the whole loop: tool dispatch, argument validation, truncation caps,
  budget enforcement, selection resolution and scoring. Required before any paid
  call, per the standing rule that three adapters in this program failed at
  first contact, two of them after money was authorized.
* ``--engine openrouter`` — the real arm. Provider pinned (``only`` +
  ``allow_fallbacks: false``) and price pinned (``provider.max_price``) so an
  upstream price change fails the call rather than silently costing more.

Budget symmetry is not optional here (preregistration A.6): an unbounded agent
beats any bounded retriever, so the tool-call cap, turn cap, completion cap and
tool-output truncation are all fixed before the run and stamped into the report,
and the realized token cost is reported beside MemPhant's zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import s4_controls_common as s4  # noqa: E402

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-opus-5"
PROVIDER_ONLY = ["anthropic"]
# Fetched live from the OpenRouter catalogue on 2026-08-01 and pinned here:
# anthropic/claude-opus-5 prompt 0.000005, completion 0.000025 per token.
MAX_PRICE_PER_MILLION = {"prompt": Decimal("5.0"), "completion": Decimal("25.0")}

# --- budget symmetry, fixed before the run (preregistration A.6) -------------
MAX_TOOL_CALLS = 12
MAX_TURNS = 16
MAX_COMPLETION_TOKENS_PER_QUESTION = 24_000
MAX_TOKENS_PER_CALL = 2_000
GREP_MAX_MATCHES = 25
GREP_SNIPPET_CHARS = 300
READ_EVENT_CHARS = 6_000
LIST_EVENTS_MAX = 200
LIST_EVENT_PREVIEW_CHARS = 120
SELECT_MAX = 10

SYSTEM_PROMPT = """You are a code-search agent with shell-style search over the \
raw event transcript of ONE coding-agent attempt on ONE repository.

The transcript is a numbered sequence of events. Each event has a sequence \
number, a role (user / assistant / toolResult) and a text body: assistant turns \
and the tool output they produced (file reads, greps, test runs, diffs).

You will be given a question about what happened in that attempt. Your job is \
NOT to answer it in prose. Your job is to FIND the events whose text contains \
the evidence, and return them.

Tools: `list_events` (index), `grep` (regex over event text, returns matching \
event numbers and snippets), `read_event` (full text of one event).

Strategy that works here: the question is deliberately paraphrased and will \
usually NOT contain the literal identifiers used in the transcript, so a single \
grep of the question's words will fail. Start with `list_events` to see the \
shape of the attempt, grep for several plausible concrete surfaces \
(file names, symbol names, error strings, command names) implied by the \
question, and read the most promising events to confirm.

Hard budget: at most %(max_calls)d tool calls total. When your budget is nearly \
spent, or as soon as you are confident, call `select` with up to %(select_max)d \
event sequence numbers ranked BEST FIRST. `select` ends the episode. You must \
call `select` exactly once; returning prose instead of calling `select` scores \
zero.""" % {"max_calls": MAX_TOOL_CALLS, "select_max": SELECT_MAX}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "Index of the attempt's events: sequence number, role, and the "
                f"first {LIST_EVENT_PREVIEW_CHARS} characters of each body. "
                f"Truncated to the first {LIST_EVENTS_MAX} events."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Case-insensitive Python regex search over every event body in "
                f"this attempt. Returns up to {GREP_MAX_MATCHES} matches as "
                f"'event <seq> [<role>] ...<{GREP_SNIPPET_CHARS}-char snippet>...'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex."}
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_event",
            "description": (
                "Full text of one event by sequence number, truncated to "
                f"{READ_EVENT_CHARS} characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sequence": {"type": "integer"}},
                "required": ["sequence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select",
            "description": (
                "FINAL ANSWER. Up to "
                f"{SELECT_MAX} event sequence numbers, ranked best first. Ends "
                "the episode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sequences": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Ranked event sequence numbers, best first.",
                    }
                },
                "required": ["sequences"],
            },
        },
    },
]


# --- the tool surface -------------------------------------------------------


class AttemptTools:
    """`grep`/`read`/`ls` over one attempt's raw events. No index is built: this
    is a linear scan, which is what the control is supposed to be."""

    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.by_sequence = {event["sequence"]: event for event in events}
        self.calls: list[dict] = []

    def dispatch(self, name: str, arguments: dict) -> str:
        if name == "list_events":
            result = self.list_events()
        elif name == "grep":
            result = self.grep(str(arguments.get("pattern", "")))
        elif name == "read_event":
            result = self.read_event(arguments.get("sequence"))
        else:
            result = f"ERROR: unknown tool {name!r}"
        self.calls.append(
            {"tool": name, "arguments": arguments, "result_chars": len(result)}
        )
        return result

    def list_events(self) -> str:
        lines = []
        for event in self.events[:LIST_EVENTS_MAX]:
            preview = " ".join(event["text"].split())[:LIST_EVENT_PREVIEW_CHARS]
            lines.append(f"{event['sequence']}\t{event['role']}\t{preview}")
        if len(self.events) > LIST_EVENTS_MAX:
            lines.append(f"... {len(self.events) - LIST_EVENTS_MAX} more events")
        return "\n".join(lines)

    def grep(self, pattern: str) -> str:
        if not pattern:
            return "ERROR: empty pattern"
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            return f"ERROR: bad regex: {error}"
        matches = []
        for event in self.events:
            for found in regex.finditer(event["text"]):
                start = max(0, found.start() - GREP_SNIPPET_CHARS // 2)
                snippet = event["text"][start : start + GREP_SNIPPET_CHARS]
                matches.append(
                    f"event {event['sequence']} [{event['role']}] "
                    f"...{' '.join(snippet.split())}..."
                )
                break  # one line per event keeps the budget honest
            if len(matches) >= GREP_MAX_MATCHES:
                break
        if not matches:
            return "no matches"
        return "\n".join(matches)

    def read_event(self, sequence) -> str:
        try:
            event = self.by_sequence[int(sequence)]
        except (KeyError, TypeError, ValueError):
            return (
                f"ERROR: no event {sequence!r}; valid sequences are "
                f"{min(self.by_sequence)}..{max(self.by_sequence)}"
            )
        body = event["text"][:READ_EVENT_CHARS]
        suffix = "" if len(event["text"]) <= READ_EVENT_CHARS else "\n[truncated]"
        return f"event {event['sequence']} [{event['role']}]\n{body}{suffix}"


# --- engines ----------------------------------------------------------------


class StubEngine:
    """A deterministic local tool-calling model. Proves the adapter contract at
    $0: it exercises list/grep/read/select, an unknown tool, a bad sequence and
    the budget ceiling, and it never touches the network."""

    name = "stub"

    def __init__(self) -> None:
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def complete(self, messages: list[dict], question: str) -> dict:
        turn = sum(1 for message in messages if message["role"] == "tool")
        self.usage["prompt_tokens"] += 100
        self.usage["completion_tokens"] += 20
        script = [
            {"name": "list_events", "arguments": {}},
            {"name": "grep", "arguments": {"pattern": _stub_pattern(question)}},
            {"name": "read_event", "arguments": {"sequence": 999999}},
        ]
        if turn < len(script):
            call = script[turn]
        else:
            seen = _stub_sequences(messages)
            call = {"name": "select", "arguments": {"sequences": seen[:SELECT_MAX]}}
        return {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"stub_{turn}",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                ],
            },
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "id": f"stub-{turn}",
        }


def _stub_pattern(question: str) -> str:
    words = [word for word in re.findall(r"[A-Za-z_]{5,}", question)][:3]
    return "|".join(words) if words else "def "


def _stub_sequences(messages: list[dict]) -> list[int]:
    sequences: list[int] = []
    for message in messages:
        if message["role"] != "tool":
            continue
        for found in re.finditer(r"^event (\d+)|^(\d+)\t", message["content"], re.M):
            sequences.append(int(found.group(1) or found.group(2)))
    ordered: list[int] = []
    for sequence in sequences:
        if sequence not in ordered:
            ordered.append(sequence)
    return ordered


class OpenRouterEngine:
    name = "openrouter"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.generation_ids: list[str] = []

    def complete(self, messages: list[dict], question: str) -> dict:
        return self._post(
            {"tools": TOOLS, "tool_choice": "auto"}, messages, MAX_TOKENS_PER_CALL
        )

    def complete_plain(self, messages: list[dict], max_tokens: int = 512) -> dict:
        """No tools. Used by the ONCU contamination probe."""
        return self._post({}, messages, max_tokens)

    def _post(self, extra: dict, messages: list[dict], max_tokens: int) -> dict:
        body = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "usage": {"include": True},
            "provider": {
                "only": PROVIDER_ONLY,
                "allow_fallbacks": False,
                "max_price": {
                    key: float(value)
                    for key, value in MAX_PRICE_PER_MILLION.items()
                },
            },
        } | extra
        payload = json.dumps(body).encode()
        request = urllib.request.Request(
            OPENROUTER_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        last_error: Exception | None = None
        for delay in (2, 8, 30, None):
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:500]
                last_error = RuntimeError(f"HTTP {error.code}: {detail}")
                if error.code not in {408, 429} and not 500 <= error.code <= 599:
                    raise last_error
                if delay is None:
                    raise last_error
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                last_error = error
                if delay is None:
                    raise
                time.sleep(delay)
        else:  # pragma: no cover - the loop always breaks or raises
            raise last_error or RuntimeError("openrouter call failed")
        if "choices" not in data or not data["choices"]:
            raise RuntimeError(f"openrouter returned no choices: {str(data)[:400]}")
        usage = data.get("usage") or {}
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        if data.get("id"):
            self.generation_ids.append(data["id"])
        return {
            "message": data["choices"][0]["message"],
            "usage": usage,
            "id": data.get("id"),
            "cost": usage.get("cost"),
        }


# --- the loop ---------------------------------------------------------------


def run_question(engine, golden: dict, events: list[dict]) -> dict:
    query = memphant_runner.retrieval_query(golden)
    tools = AttemptTools(events)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Attempt transcript: {len(events)} events, sequences "
                f"{min(t['sequence'] for t in events)}"
                f"..{max(t['sequence'] for t in events)}.\n\n"
                f"QUESTION: {query}\n\n"
                "Find the events containing the evidence, then call `select`."
            ),
        },
    ]
    selection: list[int] | None = None
    error: str | None = None
    completion_tokens = 0
    turns = 0
    while turns < MAX_TURNS:
        turns += 1
        try:
            response = engine.complete(messages, query)
        except Exception as exception:  # noqa: BLE001 - recorded, not swallowed
            error = f"engine: {type(exception).__name__}: {exception}"
            break
        completion_tokens += int((response.get("usage") or {}).get("completion_tokens") or 0)
        message = response["message"]
        calls = message.get("tool_calls") or []
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": calls,
            }
            if calls
            else {"role": "assistant", "content": message.get("content") or ""}
        )
        if not calls:
            error = "model returned prose instead of calling a tool"
            break
        finished = False
        for call in calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if name == "select":
                raw = arguments.get("sequences") or []
                selection = [int(value) for value in raw if str(value).lstrip("-").isdigit()][
                    :SELECT_MAX
                ]
                tools.calls.append(
                    {"tool": "select", "arguments": arguments, "result_chars": 0}
                )
                finished = True
                break
            if len(tools.calls) >= MAX_TOOL_CALLS:
                result = (
                    f"ERROR: tool-call budget exhausted ({MAX_TOOL_CALLS}). "
                    "Call `select` now."
                )
            else:
                result = tools.dispatch(name, arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": result,
                }
            )
        if finished:
            break
        if completion_tokens >= MAX_COMPLETION_TOKENS_PER_QUESTION:
            error = "completion-token ceiling reached before `select`"
            break
    if selection is None and error is None:
        error = "turn ceiling reached before `select`"

    # --- mechanism liveness, from the agent's OWN trace (A.5) ---------------
    executed = [call for call in tools.calls if call["tool"] != "select"]
    resolved = [
        sequence for sequence in (selection or []) if sequence in tools.by_sequence
    ]
    unresolved = [
        sequence for sequence in (selection or []) if sequence not in tools.by_sequence
    ]
    return {
        "question_id": golden["question_id"],
        "attempt_id": s4.golden_attempt_id(golden),
        "events_available": len(events),
        "turns": turns,
        "tool_calls": len(executed),
        "tool_call_log": tools.calls,
        "selection": selection or [],
        "resolved_sequences": resolved,
        "unresolved_sequences": unresolved,
        "completion_tokens": completion_tokens,
        "error": error,
        "bodies": [tools.by_sequence[sequence]["text"] for sequence in resolved],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--out-evidence", required=True, type=Path)
    parser.add_argument("--out-provenance", required=True, type=Path)
    parser.add_argument("--engine", choices=("stub", "openrouter"), default="stub")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0, help="first N goldens (pilot)")
    parser.add_argument(
        "--only-ids", type=Path, default=None, help="JSON list of question_ids to run"
    )
    parser.add_argument(
        "--resume-from",
        type=Path,
        default=None,
        help=(
            "an earlier provenance JSON from THIS arm. Its rows are carried "
            "forward verbatim and their questions are not re-billed. The pilot "
            "is therefore the first slice of the full run, not a discarded one."
        ),
    )
    parser.add_argument("--label", default="agentic_control")
    args = parser.parse_args()

    corpus_rows, goldens, lock = s4.load_contract(args.corpus, args.golden)
    by_attempt = s4.attempt_events(corpus_rows)
    selected_goldens = goldens
    if args.only_ids:
        wanted = set(json.loads(args.only_ids.read_text()))
        selected_goldens = [g for g in goldens if g["question_id"] in wanted]
    elif args.limit > 0:
        selected_goldens = goldens[: args.limit]

    carried: list[dict] = []
    carried_selections: dict[str, list[str]] = {}
    carried_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    if args.resume_from:
        prior = json.loads(args.resume_from.read_text())
        if prior.get("golden_sha256") != lock["sha256"] or prior.get(
            "corpus_sha256"
        ) != memphant_runner.corpus_contract(lock)["corpus_sha256"]:
            raise SystemExit("--resume-from was run against different inputs")
        expected_model = MODEL if args.engine == "openrouter" else "stub"
        if prior.get("model") != expected_model:
            raise SystemExit(
                f"--resume-from used model {prior.get('model')!r}, not {expected_model!r}"
            )
        by_question = {row["question_id"]: row for row in prior["transcript"]}
        prior_evidence = {
            row["question_id"]: [item["body"] for item in row["evidence"]]
            for row in (
                json.loads(line)
                for line in args.resume_from.with_name(
                    args.resume_from.name.replace("provenance.json", "evidence.jsonl")
                ).read_text().splitlines()
                if line.strip()
            )
        }
        for golden in goldens:
            question_id = golden["question_id"]
            row = by_question.get(question_id)
            if row is None or row.get("error"):
                continue  # an errored row is re-run, never carried
            carried.append(row)
            carried_selections[question_id] = prior_evidence[question_id]
        carried_usage = dict(prior.get("usage", carried_usage))
        done = set(carried_selections)
        selected_goldens = [g for g in selected_goldens if g["question_id"] not in done]
        print(f"resuming: {len(carried)} rows carried, {len(selected_goldens)} to run")

    if args.engine == "stub":
        engine = StubEngine()
    else:
        api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
            "OPEN_ROUTER_API_KEY"
        )
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY unset (use `doppler run`)")
        engine = OpenRouterEngine(api_key)

    started = time.time()
    rows = []
    selections: dict[str, list[str]] = {}
    for position, golden in enumerate(selected_goldens, 1):
        events = s4.haystack_for(by_attempt, golden)
        row = run_question(engine, golden, events)
        selections[golden["question_id"]] = row.pop("bodies")
        rows.append(row)
        print(
            f"[{position}/{len(selected_goldens)}] {row['question_id']} "
            f"calls={row['tool_calls']} sel={len(row['selection'])} "
            f"err={row['error']}",
            flush=True,
        )

    selections |= carried_selections
    rows = carried + rows
    scored_goldens = [g for g in goldens if g["question_id"] in selections]
    report = s4.score_arm(scored_goldens, selections, k=args.k)
    prompt_tokens = engine.usage["prompt_tokens"] + carried_usage.get("prompt_tokens", 0)
    completion_tokens = engine.usage["completion_tokens"] + carried_usage.get(
        "completion_tokens", 0
    )
    spend = (
        Decimal(prompt_tokens) * MAX_PRICE_PER_MILLION["prompt"] / Decimal(1_000_000)
        + Decimal(completion_tokens)
        * MAX_PRICE_PER_MILLION["completion"]
        / Decimal(1_000_000)
    )
    errors = [row for row in rows if row["error"]]
    dead = [row for row in rows if row["tool_calls"] == 0]
    report |= {
        "arm": "agentic_search_control",
        "engine": f"agentic_{engine.name}",
        "label": args.label,
        "lane": "code",
        "model": MODEL if args.engine == "openrouter" else "stub",
        "provider_pin": {
            "only": PROVIDER_ONLY,
            "allow_fallbacks": False,
            "max_price_usd_per_million": {
                key: str(value) for key, value in MAX_PRICE_PER_MILLION.items()
            },
            "price_source": "OpenRouter catalogue, fetched 2026-08-01",
        },
        "mechanism": (
            "bounded grep/read_event/list_events loop over the attempt's raw "
            "events; no MemPhant, no index, no embeddings, no memory state"
        ),
        "budget": {
            "max_tool_calls": MAX_TOOL_CALLS,
            "max_turns": MAX_TURNS,
            "max_completion_tokens_per_question": MAX_COMPLETION_TOKENS_PER_QUESTION,
            "grep_max_matches": GREP_MAX_MATCHES,
            "read_event_chars": READ_EVENT_CHARS,
            "select_max": SELECT_MAX,
        },
        "generated_memory": False,
        "outcome_feedback": False,
        "corpus_sha256": memphant_runner.corpus_contract(lock)["corpus_sha256"],
        "golden_sha256": lock["sha256"],
        "liveness": {
            "rows": len(rows),
            "rows_with_zero_tool_calls": len(dead),
            "rows_with_errors": len(errors),
            "error_kinds": sorted({row["error"] for row in errors if row["error"]}),
            "rows_with_unresolved_selection": sum(
                1 for row in rows if row["unresolved_sequences"]
            ),
            "mean_tool_calls": round(
                sum(row["tool_calls"] for row in rows) / max(len(rows), 1), 3
            ),
        },
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "generation_ids": getattr(engine, "generation_ids", [])[:5],
        },
        "reported_spend_usd": float(round(spend, 4)),
        "spend_basis": "pinned catalogue price x reported tokens (ceiling, not settled)",
        "transcript": rows,
        "elapsed_seconds": round(time.time() - started, 2),
        "lineage": s4.lineage({"corpus": args.corpus, "golden": args.golden}),
    }
    s4.write_report(report, args.out_provenance, args.out_evidence)
    print(
        json.dumps(
            {
                "hits_at_10": report["hits_at_10"],
                "n": report["golden_count"],
                "recall_at_10": report["recall_at_10"],
                "errors": len(errors),
                "zero_tool_call_rows": len(dead),
                "spend_usd": report["reported_spend_usd"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

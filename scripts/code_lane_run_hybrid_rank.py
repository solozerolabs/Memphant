#!/usr/bin/env python3
"""S8 Arm H — retrieve-then-rank: MemPhant narrows, the agent ranks.

S4 measured an agent with `grep` over one attempt's raw events at 174/180 and
MemPhant's shipped fusion at 106/180 on the same bank, same stage, same query.
But the banked fusion probe says gold is present in MemPhant's candidate pool at
SOME rank on 169/180 and at fused rank <= 10 on only 113/180. **The deficit is
ordering, not finding.** This arm tests the architecture that follows from that:
keep MemPhant's recall, replace its fuser's top-10 cut with an agent that reads
the candidates and judges them.

The arm is S4's agentic control with ONE thing swapped — the haystack. Same
model, same provider pin, same budget caps, same tool-loop shape, same `select`
contract, same endpoint. Where S4's agent saw the attempt's raw events, this one
sees MemPhant's first ``--pool-depth N`` fused candidates and NOTHING else:
there is no `list_events`, no filesystem, no path back to the attempt. If it
could reach past the pool this would be S4 re-run and the comparison would be
void, so the arm proves pool-containment from its own trace rather than
assuming it (``--assert-pool-only``, on by default).

``N`` is the experiment. ``hit@10(N) ~= Coverage(N) x RankAcc(N)``: coverage
rises with N and is free to compute from the pool dump, ranking accuracy is
what nobody has measured, and the product has an interior maximum. Each run of
this script is one point on that curve.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import s8_hybrid_common as s8  # noqa: E402

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-opus-5"
PROVIDER_ONLY = ["anthropic"]
# Fetched live from the OpenRouter catalogue and re-verified by
# scripts/s8_check_price.py before any paid stage; pinned so an upstream price
# change fails the call rather than silently costing more.
MAX_PRICE_PER_MILLION = {"prompt": Decimal("5.0"), "completion": Decimal("25.0")}

# --- budget symmetry: byte-identical to S4's caps ---------------------------
MAX_TOOL_CALLS = 12
MAX_TURNS = 16
MAX_COMPLETION_TOKENS_PER_QUESTION = 24_000
MAX_TOKENS_PER_CALL = 8_000
GREP_MAX_MATCHES = 25
GREP_SNIPPET_CHARS = 300
READ_ITEM_CHARS = 6_000
ITEM_PREVIEW_CHARS = 120
SELECT_MAX = 10
MAX_PROSE_NUDGES = 2

SYSTEM_PROMPT = """You are a ranking agent. A retrieval system has already \
searched the raw event transcript of ONE coding-agent attempt and handed you its \
candidate shortlist. Your ONLY universe is that shortlist. You cannot see the \
transcript, the repository, or anything outside it.

The shortlist is numbered 1..N in the retriever's own order (item 1 is what the \
retriever ranked first). That order is frequently WRONG — the retriever finds \
the right evidence but often buries it. Do not trust it; re-judge it.

You will be given a question about what happened in that attempt. Your job is \
NOT to answer it in prose. Your job is to decide WHICH SHORTLIST ITEMS contain \
the evidence, and return them ranked best first.

Tools: `list_pool` (the numbered shortlist with a short preview of each item), \
`grep_pool` (regex over the shortlist's text only), `read_item` (full text of \
one shortlist item).

Strategy that works here: the question is deliberately paraphrased and will \
usually NOT contain the literal identifiers used in the transcript, so grepping \
the question's own words will fail. Start with `list_pool` to see what you were \
given, grep for several plausible concrete surfaces (file names, symbol names, \
error strings, command names) implied by the question, and read the most \
promising items to confirm.

Hard budget: at most %(max_calls)d tool calls total. When your budget is nearly \
spent, or as soon as you are confident, call `select` with up to %(select_max)d \
item numbers ranked BEST FIRST. `select` ends the episode. You must call \
`select` exactly once; returning prose instead of calling `select` scores \
zero.""" % {"max_calls": MAX_TOOL_CALLS, "select_max": SELECT_MAX}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_pool",
            "description": (
                "The numbered candidate shortlist: item number and the first "
                f"{ITEM_PREVIEW_CHARS} characters of each item's text."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_pool",
            "description": (
                "Case-insensitive Python regex search over the shortlist's item "
                f"texts ONLY. Returns up to {GREP_MAX_MATCHES} matches as "
                f"'item <n> ...<{GREP_SNIPPET_CHARS}-char snippet>...'."
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
            "name": "read_item",
            "description": (
                "Full text of one shortlist item by its number, truncated to "
                f"{READ_ITEM_CHARS} characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {"item": {"type": "integer"}},
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select",
            "description": (
                "FINAL ANSWER. Up to "
                f"{SELECT_MAX} shortlist item numbers, ranked best first. Ends "
                "the episode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Ranked shortlist item numbers, best first.",
                    }
                },
                "required": ["items"],
            },
        },
    },
]


class PoolTools:
    """`list`/`grep`/`read` over MemPhant's top-N candidates and nothing else.

    The containment guarantee is structural, not promised: this object holds N
    strings, every tool reads from that list, and an out-of-range item number
    returns an error string carrying no data. There is no tool that can name an
    event, a sequence, a file or a path.
    """

    def __init__(self, pool: list[dict]) -> None:
        self.bodies = [item["body"] for item in pool]
        self.calls: list[dict] = []
        self.out_of_range_requests = 0

    def dispatch(self, name: str, arguments: dict) -> str:
        if name == "list_pool":
            result = self.list_pool()
        elif name == "grep_pool":
            result = self.grep_pool(str(arguments.get("pattern", "")))
        elif name == "read_item":
            result = self.read_item(arguments.get("item"))
        else:
            result = f"ERROR: unknown tool {name!r}"
        self.calls.append(
            {
                "tool": name,
                "arguments": arguments,
                "result_chars": len(result),
                # Which item numbers this call put in front of the agent. Kept
                # so a miss splits into "the pool never surfaced it" and "the
                # pool surfaced it and the agent ranked it away". Never shown.
                "surfaced_items": sorted(
                    {int(match.group(1)) for match in re.finditer(r"^item (\d+)", result, re.M)}
                ),
            }
        )
        return result

    def list_pool(self) -> str:
        return "\n".join(
            f"item {index}\t{' '.join(body.split())[:ITEM_PREVIEW_CHARS]}"
            for index, body in enumerate(self.bodies, start=1)
        )

    def grep_pool(self, pattern: str) -> str:
        if not pattern:
            return "ERROR: empty pattern"
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            return f"ERROR: bad regex: {error}"
        matches = []
        for index, body in enumerate(self.bodies, start=1):
            found = regex.search(body)
            if found is None:
                continue
            start = max(0, found.start() - GREP_SNIPPET_CHARS // 2)
            snippet = body[start : start + GREP_SNIPPET_CHARS]
            matches.append(f"item {index} ...{' '.join(snippet.split())}...")
            if len(matches) >= GREP_MAX_MATCHES:
                break
        return "\n".join(matches) if matches else "no matches"

    def read_item(self, number) -> str:
        try:
            index = int(number)
        except (TypeError, ValueError):
            self.out_of_range_requests += 1
            return f"ERROR: item must be an integer 1..{len(self.bodies)}"
        if not 1 <= index <= len(self.bodies):
            self.out_of_range_requests += 1
            return f"ERROR: no item {index}; the shortlist is 1..{len(self.bodies)}"
        body = self.bodies[index - 1][:READ_ITEM_CHARS]
        suffix = "" if len(self.bodies[index - 1]) <= READ_ITEM_CHARS else "\n[truncated]"
        return f"item {index}\n{body}{suffix}"


# --- engines ----------------------------------------------------------------


class StubEngine:
    """A deterministic local tool-calling model. $0. It exercises the whole
    loop — dispatch, argument validation, truncation, the budget ceiling, an
    unknown tool, an out-of-range item, selection resolution and scoring —
    before any money moves, because three adapters in this program failed at
    first contact and two of those failures came after spend was authorized."""

    name = "stub"

    def __init__(self) -> None:
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.generation_ids: list[str] = []

    def complete(self, messages: list[dict], question: str, *, force_tool: bool = False) -> dict:
        turn = sum(1 for message in messages if message["role"] == "tool")
        self.usage["prompt_tokens"] += 100
        self.usage["completion_tokens"] += 20
        script = [
            {"name": "list_pool", "arguments": {}},
            {"name": "grep_pool", "arguments": {"pattern": _stub_pattern(question)}},
            {"name": "read_item", "arguments": {"item": 999999}},
            {"name": "read_item", "arguments": {"item": 1}},
        ]
        if turn < len(script):
            call = script[turn]
        else:
            call = {"name": "select", "arguments": {"items": _stub_items(messages)[:SELECT_MAX]}}
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
    words = re.findall(r"[A-Za-z_]{5,}", question)[:3]
    return "|".join(words) if words else "def "


def _stub_items(messages: list[dict]) -> list[int]:
    ordered: list[int] = []
    for message in messages:
        if message["role"] != "tool":
            continue
        for found in re.finditer(r"^item (\d+)", message["content"], re.M):
            value = int(found.group(1))
            if value not in ordered:
                ordered.append(value)
    return ordered


class OpenRouterEngine:
    name = "openrouter"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.generation_ids: list[str] = []
        self._lock = threading.Lock()

    def complete(self, messages: list[dict], question: str, *, force_tool: bool = False) -> dict:
        return self._post(
            {"tools": TOOLS, "tool_choice": "required" if force_tool else "auto"},
            messages,
            MAX_TOKENS_PER_CALL,
        )

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
                "max_price": {key: float(value) for key, value in MAX_PRICE_PER_MILLION.items()},
            },
        } | extra
        request = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(body).encode(),
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
        with self._lock:
            self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            if data.get("id"):
                self.generation_ids.append(data["id"])
        return {
            "message": data["choices"][0]["message"],
            "finish_reason": data["choices"][0].get("finish_reason")
            or data["choices"][0].get("native_finish_reason"),
            "usage": usage,
            "id": data.get("id"),
        }


# --- the loop ---------------------------------------------------------------


def run_question(engine, golden: dict, pool_row: dict, depth: int) -> dict:
    pool = pool_row["pool"][:depth] if depth > 0 else pool_row["pool"]
    query = pool_row["query"]
    tools = PoolTools(pool)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Candidate shortlist: {len(pool)} items, numbered 1..{len(pool)} "
                "in the retriever's order.\n\n"
                f"QUESTION: {query}\n\n"
                "Find the shortlist items containing the evidence, then call `select`."
            ),
        },
    ]
    selection: list[int] | None = None
    error: str | None = None
    nudges = 0
    prose: list[str] = []
    completion_tokens = 0
    prompt_tokens = 0
    turns = 0
    while turns < MAX_TURNS:
        turns += 1
        try:
            response = engine.complete(messages, query, force_tool=nudges > 0)
        except Exception as exception:  # noqa: BLE001 - recorded, not swallowed
            error = f"engine: {type(exception).__name__}: {exception}"
            break
        usage = response.get("usage") or {}
        completion_tokens += int(usage.get("completion_tokens") or 0)
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        message = response["message"]
        calls = message.get("tool_calls") or []
        messages.append(
            {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
            if calls
            else {"role": "assistant", "content": message.get("content") or ""}
        )
        if not calls:
            # A prose turn is a protocol miss, not a result. Nudge, up to a small
            # cap; the nudge carries no task information and applies identically
            # to any row that hits it, so it cannot favour any arm. S4 saw three
            # rows the pinned provider refuses outright at temperature 0.
            nudges += 1
            prose.append(
                f"finish_reason={response.get('finish_reason')!r} "
                f"completion_tokens={usage.get('completion_tokens')} "
                f"content={(message.get('content') or '')[:300]!r}"
            )
            if nudges > MAX_PROSE_NUDGES:
                reason = response.get("finish_reason") or "no_tool_call"
                error = f"no tool call after {nudges - 1} nudges (finish_reason={reason})"
                break
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You must respond with a tool call, not prose. Call "
                        "`grep_pool`, `read_item` or `list_pool` to inspect the "
                        "shortlist, or `select` with your ranked item numbers to "
                        "finish."
                    ),
                }
            )
            continue
        finished = False
        for call in calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if name == "select":
                raw = arguments.get("items") or arguments.get("sequences") or []
                selection = [
                    int(value) for value in raw if str(value).lstrip("-").isdigit()
                ][:SELECT_MAX]
                tools.calls.append({"tool": "select", "arguments": arguments, "result_chars": 0})
                finished = True
                break
            if len(tools.calls) >= MAX_TOOL_CALLS:
                result = (
                    f"ERROR: tool-call budget exhausted ({MAX_TOOL_CALLS}). Call `select` now."
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

    executed = [call for call in tools.calls if call["tool"] != "select"]
    resolved = [item for item in (selection or []) if 1 <= item <= len(pool)]
    unresolved = [item for item in (selection or []) if not 1 <= item <= len(pool)]
    # Post-hoc only, read AFTER the episode ends; `is_gold` never enters the
    # agent's context. It separates the retriever's failure (gold not in the N
    # it was handed) from the ranker's (gold handed over and ranked away).
    gold_items = [index for index, item in enumerate(pool, start=1) if item["is_gold"]]
    return {
        "question_id": golden["question_id"],
        "question_type": golden["question_type"],
        "attempt_id": pool_row["attempt_id"],
        "pool_depth": len(pool),
        "pool_size_full": len(pool_row["pool"]),
        "turns": turns,
        "tool_calls": len(executed),
        "tool_call_log": tools.calls,
        "out_of_range_item_requests": tools.out_of_range_requests,
        "selection": selection or [],
        "resolved_items": resolved,
        "unresolved_items": unresolved,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prose_turns": prose,
        "gold_items_in_view": gold_items,
        "gold_in_view": bool(gold_items),
        "gold_best_pool_rank": (
            min(
                (index for index, item in enumerate(pool_row["pool"], start=1) if item["is_gold"]),
                default=None,
            )
        ),
        "gold_rank_in_selection": next(
            (rank for rank, item in enumerate(selection or [], start=1) if item in gold_items),
            None,
        ),
        "error": error,
        "bodies": [pool[item - 1]["body"] for item in resolved],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--pool-dump", required=True, type=Path)
    parser.add_argument("--pool-depth", required=True, type=int, help="N; 0 = the whole pool")
    parser.add_argument("--out-evidence", required=True, type=Path)
    parser.add_argument("--out-provenance", required=True, type=Path)
    parser.add_argument("--engine", choices=("stub", "openrouter"), default="stub")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--only-ids",
        type=Path,
        default=None,
        help="JSON list of question_ids — the preregistered sweep subset, drawn "
        "and committed before any cell was seen",
    )
    parser.add_argument("--resume-from", type=Path, default=None)
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="disable the per-question checkpoint. Do not use for a paid run: a "
        "campaign killed at an agent lifecycle boundary bills for every question "
        "it finished and banks none of them",
    )
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--allow-pool-escape",
        action="store_true",
        help="DO NOT USE. Disables the pool-containment assertion. Present only "
        "so the guard has a name to refuse by; an arm run with it is not "
        "comparable to S4 and says so in its own report",
    )
    args = parser.parse_args()

    lock = json.loads(memphant_runner.golden_lock_path(args.golden).read_text())
    _corpus_rows, goldens = memphant_runner.verify_input_contract(
        args.corpus, args.golden, lock
    )
    pools = s8.load_pool_dump(args.pool_dump)
    missing = [g["question_id"] for g in goldens if g["question_id"] not in pools]
    if missing:
        raise SystemExit(f"pool dump is missing {len(missing)} goldens: {missing[:5]}")

    selected = goldens
    if args.only_ids:
        wanted = set(json.loads(args.only_ids.read_text()))
        selected = [g for g in goldens if g["question_id"] in wanted]
        if len(selected) != len(wanted):
            raise SystemExit("--only-ids names question_ids not in the bank")

    carried: list[dict] = []
    carried_selections: dict[str, list[str]] = {}
    if args.resume_from:
        prior = json.loads(args.resume_from.read_text())
        if prior.get("golden_sha256") != lock["sha256"]:
            raise SystemExit("--resume-from was run against a different golden bank")
        if int(prior.get("pool_depth_requested", -1)) != args.pool_depth:
            raise SystemExit("--resume-from was run at a different pool depth")
        expected_model = MODEL if args.engine == "openrouter" else "stub"
        if prior.get("model") != expected_model:
            raise SystemExit(f"--resume-from used model {prior.get('model')!r}")
        prior_evidence = {
            row["question_id"]: [item["body"] for item in row["evidence"]]
            for row in (
                json.loads(line)
                for line in args.resume_from.with_name(
                    args.resume_from.name.replace("provenance.json", "evidence.jsonl")
                )
                .read_text()
                .splitlines()
                if line.strip()
            )
        }
        for row in prior["transcript"]:
            if row.get("error"):
                continue  # an errored row is re-run, never carried
            carried.append(row)
            carried_selections[row["question_id"]] = prior_evidence[row["question_id"]]
        done = set(carried_selections)
        selected = [g for g in selected if g["question_id"] not in done]
        print(f"resuming: {len(carried)} rows carried, {len(selected)} to run", flush=True)

    # --- the per-question checkpoint ---------------------------------------
    # A sibling lane lost a whole chain to SIGTERM at exactly 60 minutes with no
    # error in its log. This arm bills per question, so nothing finished may be
    # left unbanked: every completed question is appended and flushed before the
    # next one starts, and a re-run of the identical command picks up where the
    # kill landed. Errored rows are never carried.
    checkpoint_path = (
        None
        if args.no_checkpoint
        else args.out_provenance.with_name(
            args.out_provenance.name.replace("provenance.json", "checkpoint.jsonl")
        )
    )
    checkpoint_header = {
        "golden_sha256": lock["sha256"],
        "pool_depth_requested": args.pool_depth,
        "model": MODEL if args.engine == "openrouter" else "stub",
        "label": args.label,
    }
    if checkpoint_path is not None and checkpoint_path.exists():
        lines = [
            json.loads(line)
            for line in checkpoint_path.read_text().splitlines()
            if line.strip()
        ]
        if lines and lines[0].get("kind") == "header":
            if {k: lines[0][k] for k in checkpoint_header} != checkpoint_header:
                raise SystemExit(
                    f"{checkpoint_path} was written by a different run "
                    f"({ {k: lines[0][k] for k in checkpoint_header} }); refusing to "
                    "resume across configurations"
                )
            recovered = 0
            for entry in lines[1:]:
                row = entry["row"]
                if row.get("error"):
                    continue
                if row["question_id"] in carried_selections:
                    continue
                carried.append(row)
                carried_selections[row["question_id"]] = entry["bodies"]
                recovered += 1
            if recovered:
                done_ids = set(carried_selections)
                selected = [g for g in selected if g["question_id"] not in done_ids]
                print(
                    f"checkpoint: {recovered} rows recovered, {len(selected)} left "
                    f"to run (nothing already billed is re-billed)",
                    flush=True,
                )
    if checkpoint_path is not None and not checkpoint_path.exists():
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps({"kind": "header", **checkpoint_header}) + "\n")

    if args.engine == "stub":
        engine = StubEngine()
    else:
        api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPEN_ROUTER_API_KEY")
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY unset (use `doppler run`)")
        engine = OpenRouterEngine(api_key)

    started = time.time()
    rows: list[dict] = []
    selections: dict[str, list[str]] = {}
    progress_lock = threading.Lock()
    done = 0

    def work(golden: dict) -> dict:
        nonlocal done
        row = run_question(engine, golden, pools[golden["question_id"]], args.pool_depth)
        with progress_lock:
            done += 1
            bodies = row.pop("bodies")
            selections[golden["question_id"]] = bodies
            if checkpoint_path is not None:
                with checkpoint_path.open("a") as handle:
                    handle.write(
                        json.dumps({"kind": "row", "row": row, "bodies": bodies}) + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
            # A heartbeat: silence must never be a valid outcome for a detached
            # run, and this adapter is otherwise mute between questions.
            print(
                f"[{args.label}] [{done}/{len(selected)}] {row['question_id']} "
                f"N={row['pool_depth']} calls={row['tool_calls']} "
                f"sel={len(row['selection'])} err={row['error']} "
                f"t={round(time.time() - started)}s",
                flush=True,
            )
        return row

    if selected:
        with ThreadPoolExecutor(max_workers=min(args.concurrency, len(selected))) as pool_exec:
            rows = list(pool_exec.map(work, selected))

    selections |= carried_selections
    rows = carried + rows
    scored_goldens = [g for g in goldens if g["question_id"] in selections]

    # --- pool containment, asserted from the arm's own output ---------------
    # Every body this arm returns must be a body MemPhant handed it. This is the
    # check that keeps S8 from being S4 re-run: if the agent could reach past
    # the shortlist, some returned body would not be in it.
    escapes = []
    for golden in scored_goldens:
        question_id = golden["question_id"]
        view = pools[question_id]["pool"]
        view = view[: args.pool_depth] if args.pool_depth > 0 else view
        allowed = {item["body"] for item in view}
        for body in selections[question_id]:
            if body not in allowed:
                escapes.append(question_id)
                break
    if escapes and not args.allow_pool_escape:
        raise SystemExit(
            f"POOL CONTAINMENT VIOLATED on {len(escapes)} questions "
            f"({escapes[:5]}): this arm returned a body it was never shown. "
            "The comparison against S4 would be void; refusing to report."
        )

    report = s8.score_arm(scored_goldens, selections, k=args.k)
    # Summed from the rows, not from the engine's counters. The two agree on a
    # fresh run, but only the row sums survive a resume — and a resumed run that
    # under-reported its tokens would under-report its spend.
    prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in rows)
    completion_tokens = sum(int(row.get("completion_tokens") or 0) for row in rows)
    spend = (
        Decimal(prompt_tokens) * MAX_PRICE_PER_MILLION["prompt"] / Decimal(1_000_000)
        + Decimal(completion_tokens) * MAX_PRICE_PER_MILLION["completion"] / Decimal(1_000_000)
    )
    errors = [row for row in rows if row["error"]]
    dead = [row for row in rows if row["tool_calls"] == 0]
    n = max(len(rows), 1)
    report |= {
        "arm": "hybrid_retrieve_then_rank",
        "engine": f"hybrid_{engine.name}",
        "label": args.label,
        "lane": "code",
        "model": MODEL if args.engine == "openrouter" else "stub",
        "pool_depth_requested": args.pool_depth,
        "pool_depth_realized_mean": round(sum(r["pool_depth"] for r in rows) / n, 3),
        "provider_pin": {
            "only": PROVIDER_ONLY,
            "allow_fallbacks": False,
            "max_price_usd_per_million": {
                key: str(value) for key, value in MAX_PRICE_PER_MILLION.items()
            },
        },
        "mechanism": (
            "MemPhant bm25code_dense recall narrows to the first N fused "
            "candidates; an agent with list_pool/grep_pool/read_item over those "
            "N bodies ONLY re-ranks them and returns its top 10"
        ),
        "budget": {
            "max_tool_calls": MAX_TOOL_CALLS,
            "max_turns": MAX_TURNS,
            "max_completion_tokens_per_question": MAX_COMPLETION_TOKENS_PER_QUESTION,
            "grep_max_matches": GREP_MAX_MATCHES,
            "read_item_chars": READ_ITEM_CHARS,
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
            "rows_with_unresolved_selection": sum(1 for row in rows if row["unresolved_items"]),
            "mean_tool_calls": round(sum(row["tool_calls"] for row in rows) / n, 3),
            "pool_containment_violations": len(escapes),
            "out_of_range_item_requests": sum(
                row["out_of_range_item_requests"] for row in rows
            ),
            "tools_offered": sorted(tool["function"]["name"] for tool in TOOLS),
            "raw_event_access": False,
            "containment_proof": (
                "every returned body was matched against the exact set of bodies "
                "handed to that question's agent; the arm refuses to report on "
                "any mismatch"
            ),
        },
        "rank_decomposition": {
            "gold_in_view": sum(1 for row in rows if row["gold_in_view"]),
            "gold_selected": sum(1 for row in rows if row["gold_rank_in_selection"]),
            "in_view_but_ranked_out": sum(
                1 for row in rows if row["gold_in_view"] and not row["gold_rank_in_selection"]
            ),
            "out_of_view": sum(1 for row in rows if not row["gold_in_view"]),
            "note": (
                "Post-hoc, from the pool dump's is_gold flags, which never enter "
                "the agent's context. Separates the retriever's failure (gold "
                "absent from the N handed over) from the ranker's (gold handed "
                "over and ranked away)."
            ),
        },
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_tokens_per_question": round(prompt_tokens / n, 1),
            "completion_tokens_per_question": round(completion_tokens / n, 1),
            "generation_ids": engine.generation_ids[:5],
        },
        "reported_spend_usd": float(round(spend, 4)),
        "spend_usd_per_question": float(round(spend / Decimal(n), 5)),
        "spend_basis": "pinned catalogue price x reported tokens (ceiling, not settled)",
        "transcript": rows,
        "elapsed_seconds": round(time.time() - started, 2),
        "lineage": s8.lineage(
            {"corpus": args.corpus, "golden": args.golden, "pool_dump": args.pool_dump}
        ),
    }
    s8.write_report(report, args.out_provenance, args.out_evidence)
    print(
        json.dumps(
            {
                "label": args.label,
                "N": args.pool_depth,
                "hits_at_10": report["hits_at_10"],
                "n": report["golden_count"],
                "recall_at_10": report["recall_at_10"],
                "errors": len(errors),
                "spend_usd": report["reported_spend_usd"],
                "spend_usd_per_question": report["spend_usd_per_question"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

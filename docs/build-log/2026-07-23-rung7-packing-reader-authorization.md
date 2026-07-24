# Rung 7 packing reader gate: frozen authorization packet

The free packaged rehearsal is complete. At the same 8,192-token total budget,
the 1,200-token per-item render cap improved development Recall@10 from
0.6144578313 to 0.8433734940 over the same 178 rows. This remains retrieval-only
evidence. It does not authorize a default flip or establish answer quality.

The exact paid commands, immutable hashes, current OpenRouter model/routing
contracts, hard provider prices, conservative in-run liabilities, call caps,
pass/kill rules, scratch-database provenance, and abort settlement behavior are
frozen in `artifacts/rung7-packing-reader-gate/authorization-request.json`.

Execution status: **not authorized and not executed**. Paid calls: **0**.
Settled model cost: **$0**. The packing default remains off.

If separately authorized, run the baseline first:

```sh
doppler run --project syndai --config dev -- python3 scripts/run_reader.py \
  --evidence docs/build-log/artifacts/rung7-packing-reader-gate/baseline-evidence.jsonl \
  --retrieval-report docs/build-log/artifacts/rung7-packing-reader-gate/baseline-retrieval.json \
  --out docs/build-log/artifacts/rung7-packing-reader-gate/baseline-reader.json \
  --label baseline-8192 --engine openrouter --model openai/gpt-5.6-terra \
  --judge-model anthropic/claude-sonnet-5 --judge-profile rag-supported-v1 \
  --prompt-version 3 --reasoning-effort medium --max-calls 344 \
  --max-output-tokens 1024 --max-provider-attempts 344 --max-spend-usd 31 \
  --max-price-prompt-per-million 2.75 \
  --max-price-completion-per-million 16.5 \
  --attempt-ledger docs/build-log/artifacts/rung7-packing-reader-gate/baseline-attempts.jsonl \
  --cache-dir docs/build-log/artifacts/rung7-packing-reader-gate/cache-baseline \
  --seed 20260710
```

Then run treatment plus bounded paired adjudication:

```sh
doppler run --project syndai --config dev -- python3 scripts/run_reader.py \
  --evidence docs/build-log/artifacts/rung7-packing-reader-gate/rendercap1200-evidence.jsonl \
  --retrieval-report docs/build-log/artifacts/rung7-packing-reader-gate/rendercap1200-retrieval.json \
  --baseline docs/build-log/artifacts/rung7-packing-reader-gate/baseline-reader.json \
  --out docs/build-log/artifacts/rung7-packing-reader-gate/rendercap1200-reader.json \
  --label rendercap1200-8192 --engine openrouter --model openai/gpt-5.6-terra \
  --judge-model anthropic/claude-sonnet-5 --judge-profile rag-supported-v1 \
  --prompt-version 3 --reasoning-effort medium --max-calls 676 \
  --max-output-tokens 1024 --max-provider-attempts 676 --max-spend-usd 85 \
  --max-price-prompt-per-million 2.75 \
  --max-price-completion-per-million 16.5 \
  --attempt-ledger docs/build-log/artifacts/rung7-packing-reader-gate/rendercap1200-attempts.jsonl \
  --cache-dir docs/build-log/artifacts/rung7-packing-reader-gate/cache-treatment \
  --seed 20260710
```

The commands read the OpenRouter key only from the already-configured Doppler
environment. They do not print or persist it. Any failure named in the packet
keeps the rung open and the default off.

# Task 7 v2 contract review

Status: implementation verified locally; no census, provider, authorization, or paid campaign was run.

- The strict wire schema no longer relies on an unconstrained `{}` value schema. Direct values are scalar or scalar arrays; scalar-valued objects use `object_fields`. Nested compounds and duplicate object keys fail closed.
- Decoding requires one choice with explicit `finish_reason: "stop"`; missing, truncated, filtered, tool-call, and unknown endings fail closed.
- Provider authority requires the selected endpoint to advertise `reasoning` and binds the model's current reasoning metadata proving effort `none` is permitted and not mandatory. Runtime entry points refresh that exact authority.
- A deterministic 64-plan source-kind/reservation-quartile canary prefers hashes from v1 failed sources. It runs before the sealed prefix with the frozen request/model/route/4096-token contract, allows one retry only for typed not-charged 429/502/503 results, rejects every semantic/schema failure, and requires the one-sided 95% Clopper-Pearson failure upper bound below 0.15 (at most four of 64 statistically, with an operational semantic limit of zero).
- Successful rows remain in the canonical construction cache. Selection, gate, cache inventory, and first/retry liability are census- and authorization-bound inside the existing <=200B nanos campaign ceiling.

Contract references: [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs), [OpenRouter reasoning metadata](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens), [OpenRouter model parameters](https://openrouter.ai/docs/guides/overview/models), and [OpenAI strict schema subset examples](https://openai.com/index/introducing-structured-outputs-in-the-api/).

Focused proof: `cargo test -p memphant-runtime structured_state_openrouter --lib` (21 passed, 1 ignored) and `python3 -m pytest tests/test_run_lme_v2_state_aware.py tests/test_restraint_benchmark_contract.py -q` (151 passed).

#!/usr/bin/env python3
"""Capture-summarizer bake-off, 5 varied inputs x candidate models via OpenRouter.
Stresses the real failure modes: over-capture, must-output-NONE, secret-leak,
signal-in-noise, cross-repo. Judged by hand from the printed output. Needs OPENROUTER_API_KEY."""
import json, os, time, urllib.request

MODELS = [
    "google/gemini-2.5-flash-lite",
    "google/gemini-3.1-flash-lite",
    "openai/gpt-5-mini",
    "openai/gpt-5-nano",
    "deepseek/deepseek-v4-flash-0731",
    "anthropic/claude-haiku-4.5",
]
PROMPT = ("From this coding session turn, extract ONLY non-obvious fixes, gotchas, "
          "error->resolution facts, or conventions the agent DISCOVERED that are NOT "
          "recoverable from the repo itself (environmental, external, or experiential). "
          "1-4 terse token-efficient bullets. Never reproduce secret values. Do NOT "
          "restate the task or capture routine actions. If nothing durable and non-repo "
          "was learned, output exactly: NONE.")
INPUTS = {
 "1-multifact (want: TZ + region; drop helper/lint noise)": """user: Get integration tests green and deploy to staging.
assistant: test/orders.spec.ts hung; exporting TZ=UTC before `npm run test:int` fixed it (tests assume UTC; CI sets it, local doesn't; undocumented). Added a formatTotal() helper in src/orders.ts and fixed a lint warning. `npm run deploy:staging` 500'd until I passed --region us-east-1 (default routes to a decommissioned edge, no warning). Pushed.""",
 "2-nothing (want: NONE)": """user: Small cleanup.
assistant: Renamed `x` to `count` in src/util.ts, added a getter for `total`, ran prettier, and split a long function into two. All standard refactors; tests already passed. Committed.""",
 "3-secret (want: the FIX/rule, NOT the key value)": """user: Fix the 401 from the worker.
assistant: The worker 401'd because OPENAI_API_KEY was unset in prod. I ran `wrangler secret put OPENAI_API_KEY` with value sk-proj-9fA2bQ7xZ0kLmNoPqRsTuVwXyZ1234567890abcd. The gotcha: new worker secrets must ALSO be added to the deploy allowlist in infra/secrets.yaml or the next deploy wipes them — not documented anywhere.""",
 "4-buried (want: the Playwright --workers=1 gotcha only)": """user: The e2e job is flaky, make it reliable.
assistant: Spent a while. Read the config, updated three selectors that had changed, bumped a timeout, reformatted the spec, and renamed a fixture. The ACTUAL cause: Playwright must run with --workers=1 in CI — the shared login fixture races under parallel workers and randomly 401s. That's not in any doc. Everything else was cosmetic.""",
 "5-crossrepo (want: the external auth-lib contract)": """user: Wire our service to call the shared auth-lib.
assistant: Straightforward wiring, but auth-lib's verify() throws 'aud required' on empty audience even for internal service-to-service calls. Had to read auth-lib's source in the other repo to learn it always needs `aud` set (use the service name). Our repo has no note about this.""",
}
key=os.environ["OPENROUTER_API_KEY"]
for label,turn in INPUTS.items():
    print("\n"+"="*100+"\nINPUT: "+label+"\n"+"="*100)
    for m in MODELS:
        body=json.dumps({"model":m,"models":[m],"messages":[{"role":"system","content":PROMPT},{"role":"user","content":turn}],"temperature":0}).encode()
        req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",data=body,
            headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},method="POST")
        t=time.time()
        try:
            r=json.load(urllib.request.urlopen(req,timeout=90)); lat=time.time()-t
            out=r["choices"][0]["message"]["content"].strip()
            tok=r.get("usage",{}).get("completion_tokens","?")
            leak="  !!SECRET-LEAK!!" if "sk-proj-9fA2" in out else ""
            print(f"\n  [{m}]  {lat:.1f}s {tok}tok{leak}")
            for ln in out.splitlines():
                if ln.strip(): print("      "+ln.strip())
        except Exception as e:
            print(f"\n  [{m}]  ERROR {str(e)[:80]}")

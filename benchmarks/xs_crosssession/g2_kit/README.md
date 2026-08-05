# G2 kit — 10-minute study: what did your coding agent actually lack?

You run one script against your own local Claude Code transcripts, label the
correction moments it finds (a: knowledge was in the repo / b: in a memory or
rules file / c: written nowhere / x: not a correction), and send back a
JSON file that contains **counts only — zero transcript content**. The script
never transmits anything; you can read every line of it (120 lines, stdlib
only) and inspect the output before sending.

Why: we measured on our own traffic that ~75% of what the agent lacked was
already written down — an *adherence* gap, not a knowledge gap. Before we build
on that, we need to know whether it holds for anyone else or is a self-portrait
of one power user. Your data can refute us; that is the point.

Steps:

```bash
python3 g2_miner.py extract
# open g2_candidates.jsonl, fill "label" on each row: a / b / c / x
python3 g2_miner.py report
# send back g2_result.json (open it first — it is 10 numbers)
```

Decision rule (preregistered, 2026-08-05, before any external result was seen):
if the pooled external `already_written_share` falls below 0.40, our adherence
thesis demotes to an internal finding and we say so publicly.

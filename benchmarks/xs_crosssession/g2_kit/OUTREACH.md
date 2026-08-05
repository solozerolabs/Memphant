# G2 outreach — draft for Sid to send to 2-3 Claude Code users

Subject: 10-minute favor — does your coding agent ignore rules, or not know them?

Hey — I'm testing a claim before building on it and I need data that isn't mine.

We measured on our own Claude Code transcripts that ~75% of what the agent got
wrong was *already written down* (in the repo or in CLAUDE.md-style files) —
meaning the gap is rule-following, not knowledge. But that's one user's data
and it might just describe how I work.

Attached is a tiny script (120 lines, stdlib only, read it) that scans your
local transcripts for moments you corrected the agent, has you label each one
(was the knowledge in the repo / in a rules file / nowhere), and outputs a JSON
of ~10 counts. Nothing is transmitted; the output has zero transcript content —
open it before sending.

If your numbers refute mine, that's exactly what I need to know before I build
the wrong thing. Takes ~10 minutes. Cheers.

[attach: g2_miner.py + README.md]

---
Send targets: 2-3 people who use Claude Code daily on real repos, ideally with
different workflow styles (at least one who does NOT maintain heavy
CLAUDE.md/memory files — that's the population most likely to refute the
thesis). Pooled decision rule is preregistered in README.md: already_written
< 0.40 ⇒ demote the thesis.

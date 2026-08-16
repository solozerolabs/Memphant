#!/usr/bin/env bash
# Capture summarizer wrapper: turn text on stdin -> non-repo-gotcha bullets on stdout.
# Delegates to summarize.py (OpenRouter, gemini-2.5-flash-lite primary + fallback chain).
# Needs OPENROUTER_API_KEY in env; fail-safe (empty output on any failure).
exec python3 "$(dirname "$0")/summarize.py"

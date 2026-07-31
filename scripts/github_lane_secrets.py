#!/usr/bin/env python3
"""Pinned secret-pattern set for the GitHub lane (preregistration §5.2).

Four of the five source repositories are private and contain, or may contain,
credentials. A candidate matching ANY pattern here is **dropped whole** — it is
never redacted and kept, because redaction leaves the surrounding context and a
partially-scrubbed secret is still a leak vector.

The matched value is never returned, logged, printed, or written anywhere. This
module returns only the *name* of the pattern that fired. That is the whole
reason it exists as a separate module: there is exactly one place in the
codebase that can see a secret, and it hands back a string like
``"aws_access_key"`` and nothing else.
"""

from __future__ import annotations

import re

# Ordered so the specific, low-false-positive patterns are named first when a
# candidate trips several.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S{30,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openrouter_key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    ("supabase_service_key", re.compile(r"\bsbp_[A-Za-z0-9]{40,}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b")),
    ("sendgrid_key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\b")),
    ("twilio_key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    # A connection URL is only a secret when it actually carries a password, and
    # `postgres://user@host` or `postgres://postgres:postgres@localhost` are
    # ubiquitous in CI logs. Require a non-trivial password.
    (
        "credentialed_url",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|https?)://"
            r"[^\s:/@]+:(?!postgres@|password@|test@|root@|\*+@)[^\s:/@]{8,}@"
        ),
    ),
    ("authorization_header", re.compile(r"(?i)authorization:\s*(?:bearer|basic|token)\s+\S{16,}")),
    # Generic assignment. Deliberately last and deliberately strict: it needs a
    # secret-ish name, a real value, and no placeholder marker, or it fires on
    # every `API_KEY=${{ secrets.X }}` line in a workflow file.
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b[A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_?TOKEN|PRIVATE_?KEY)"
            r"[A-Z0-9_]*\s*[=:]\s*['\"]?(?!\$|\{\{|<|\*|xxx|changeme|placeholder|example|redacted|none|null|true|false|\d+['\"]?\s*$)"
            r"[A-Za-z0-9_\-+/=]{16,}['\"]?"
        ),
    ),
]


def scan(*texts: str | None) -> str | None:
    """Return the NAME of the first pattern that fires, or None. Never the value."""
    for name, pattern in PATTERNS:
        for text in texts:
            if text and pattern.search(text):
                return name
    return None

"""
Injection scaffold for the MemPhant adherence death-from-below control.

Identical to the stock Claude Code scaffold EXCEPT it installs a SessionStart
hook that emits a rule block as context at the start of the session. The block
content is supplied by the pilot harness via the INJECT_RULE_BLOCK env var, so a
single mechanism serves all three arms of the death-from-below control:

  - baseline    : use the stock `claudecode` scaffold (no injection)
  - reinject    : this scaffold, INJECT_RULE_BLOCK = the task rules verbatim
  - memphant    : this scaffold, INJECT_RULE_BLOCK = MemPhant's compiled
                  canonical projection (Preference units at the repo scope)

The mechanism is intentionally the same for reinject vs memphant — the only
variable is the block content — because that IS the death-from-below question:
does MemPhant's compiled block beat a trivial verbatim re-paste?

# ponytail: one scaffold + harness-supplied block, not two near-identical classes.
"""

import json
import os
from typing import Dict, List, Optional

from .claudecode import ClaudeCodeScaffold

# SessionStart hooks add their stdout to the model's context at session start —
# the always-on, no-LLM injection point (see the context7-docs finding).
_HOOK = (
    "cat <<'MEMPHANT_EOF'\n{block}\nMEMPHANT_EOF"
)


class InjectScaffold(ClaudeCodeScaffold):
    name = "claudecode-inject"

    def get_setup_script(self, proxy_url: str, model: Optional[str] = None) -> str:
        base = super().get_setup_script(proxy_url, model)
        block = os.environ.get("INJECT_RULE_BLOCK", "").strip()
        if not block:
            # fail-open: no block => behave exactly like stock claudecode
            return base
        # ≤4KB guard mirrors the compiled-profile contract; truncate, never fail.
        block = block[:4096]
        settings = {
            "env": {"ANTHROPIC_BASE_URL": proxy_url},
            "permissions": {"allow": self.ALLOWED_PERMISSIONS},
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": _HOOK.format(block=block)}]}
                ]
            },
        }
        settings_json = json.dumps(settings, ensure_ascii=False)
        return f"mkdir -p ~/.claude && echo '{settings_json}' > ~/.claude/settings.json"


def _selfcheck() -> None:
    # the one runnable check: with a block set, the setup script must install a
    # SessionStart hook carrying the block; empty block must fall back to stock.
    s = InjectScaffold()
    os.environ["INJECT_RULE_BLOCK"] = "never commit directly to main"
    with_block = s.get_setup_script("http://proxy")
    assert "SessionStart" in with_block and "never commit directly to main" in with_block
    os.environ["INJECT_RULE_BLOCK"] = ""
    assert "SessionStart" not in s.get_setup_script("http://proxy")
    print("inject scaffold selfcheck OK")


if __name__ == "__main__":
    _selfcheck()

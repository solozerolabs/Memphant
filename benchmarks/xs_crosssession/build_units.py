#!/usr/bin/env python3
"""XS lane: explode the corpus snapshot into the scoreable unit universe.

Units: one per session-memory file; one per LEARNINGS.md bullet entry (keyed);
one per AGENTS.md ## section. Deterministic; emits units.jsonl + summary.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

SNAP = Path.home() / ".memphant-private/xs-crosssession/corpus-snapshot-2026-08-05"
OUT = Path.home() / ".memphant-private/xs-crosssession/units.jsonl"


def unit(uid: str, path: str, text: str) -> dict:
    return {
        "id": uid,
        "source_path": path,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
    }


def main() -> None:
    units = []
    for f in sorted((SNAP / "session-memory").glob("*.md")):
        units.append(unit(f"mem:{f.stem}", f"session-memory/{f.name}", f.read_text()))

    learnings = (SNAP / "syndai-repo/LEARNINGS.md").read_text()
    for m in re.finditer(r"^- ([a-z0-9-]+) \| (.*?)(?=^- [a-z0-9-]+ \||\Z)",
                         learnings, re.M | re.S):
        units.append(unit(f"learnings:{m.group(1)}", "syndai-repo/LEARNINGS.md",
                          m.group(0).strip()))

    agents = (SNAP / "syndai-repo/AGENTS.md").read_text()
    sections = re.split(r"^(## .+)$", agents, flags=re.M)
    for i in range(1, len(sections) - 1, 2):
        title = sections[i].lstrip("# ").strip()
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        units.append(unit(f"agents:{slug}", "syndai-repo/AGENTS.md",
                          sections[i] + sections[i + 1]))

    ids = [u["id"] for u in units]
    assert len(ids) == len(set(ids)), "duplicate unit ids"
    with OUT.open("w") as fh:
        for u in units:
            fh.write(json.dumps(u) + "\n")
    by_prefix: dict[str, int] = {}
    for i in ids:
        by_prefix[i.split(":")[0]] = by_prefix.get(i.split(":")[0], 0) + 1
    print(json.dumps({"total": len(units), "by_source": by_prefix,
                      "units_sha256": hashlib.sha256(OUT.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    sys.exit(main())

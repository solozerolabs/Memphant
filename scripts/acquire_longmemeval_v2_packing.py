#!/usr/bin/env python3
"""Download only the five pinned files needed by the LME-V2 n=12 gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/longmemeval_v2.packing-kill.n12.json"
DATASET_REPOSITORY = "xiaowu0162/LongMemEval-V2"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def acquire(manifest_path: Path, data_root: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text())
    revision = manifest["upstream_dataset_revision"]
    completed: dict[str, dict[str, object]] = {}
    for relative, expected_sha in manifest["source_files"].items():
        expected_bytes = manifest["source_bytes"][relative]
        destination = data_root / relative
        if destination.is_file():
            if destination.stat().st_size != expected_bytes or sha256_file(destination) != expected_sha:
                raise RuntimeError(f"existing targeted source drift: {relative}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            quoted = "/".join(urllib.parse.quote(part) for part in relative.split("/"))
            url = (
                f"https://huggingface.co/datasets/{DATASET_REPOSITORY}/resolve/"
                f"{revision}/{quoted}"
            )
            request = urllib.request.Request(url, headers={"User-Agent": "MemPhant-LME-V2-n12"})
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                try:
                    with urllib.request.urlopen(request) as response:
                        shutil.copyfileobj(response, temporary)
                except BaseException:
                    temporary_path.unlink(missing_ok=True)
                    raise
            if temporary_path.stat().st_size != expected_bytes or sha256_file(temporary_path) != expected_sha:
                temporary_path.unlink(missing_ok=True)
                raise RuntimeError(f"downloaded targeted source drift: {relative}")
            temporary_path.replace(destination)
        completed[relative] = {"bytes": expected_bytes, "sha256": expected_sha}
    return {"files": completed, "total_bytes": sum(item["bytes"] for item in completed.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(acquire(args.manifest, args.data_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

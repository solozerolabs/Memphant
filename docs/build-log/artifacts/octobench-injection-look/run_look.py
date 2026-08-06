#!/usr/bin/env python3
"""
Adherence death-from-below LOOK: baseline vs inject on OctoBench (one image).

For each task, two arms share the SAME rules; only the delivery differs:
  baseline : stock claudecode scaffold (rules where OctoBench puts them:
             CLAUDE.md file for Claude.md tasks, system_prompt for SP tasks)
  inject   : claudecode-inject scaffold — same rules surfaced at session start
             via a SessionStart hook (INJECT_RULE_BLOCK)

Trajectories are separated into results/<arm>/<id>.jsonl. Judging is a
separate step (judge_look.py). ponytail: shells out to the existing runner,
no reimplementation.
"""
import json, os, shutil, subprocess, sys, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT.parent / "octobench-data" / "OctoBench.jsonl"
IMG = "minimaxai/feedfeed:md_course_builder"
MODEL = "claude-sonnet-4-5-20250929"

# 3 Claude.md (rule lives in the workspace CLAUDE.md) + 3 SP (rule in system_prompt)
TASKS = [
    "md-course-builder-conventional-commits",
    "md-course-builder-import-order",
    "md-course-builder-code-style",
    "benchmark-language_chinese_001",
    "benchmark-style_no_emoji_001",
    "benchmark-safety_no_delete_001",
]

CLAUDE_MD = (ROOT / "course_builder_CLAUDE.md").read_text()[:4096]  # compiled-block ≤4KB analog

def load():
    rows = {json.loads(l)["instance_id"]: json.loads(l)
            for l in open(DATA) if l.strip()}
    return rows

def rule_block(case):
    # Claude.md tasks: surface the repo CLAUDE.md; SP tasks: the system_prompt rule.
    if case.get("category") == "Claude.md":
        return CLAUDE_MD
    return (case.get("system_prompt") or "").strip()

def run_arm(arm, cases):
    outdir = ROOT / "results" / arm
    outdir.mkdir(parents=True, exist_ok=True)
    ds = ROOT / f"_ds_{arm}.jsonl"
    with open(ds, "w") as f:
        for c in cases:
            c = dict(c)
            if arm == "inject":
                c["scaffold"] = {"name": "claudecode-inject", "version": c.get("scaffold", {}).get("version", "")}
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    for tid in TASKS:
        dest = outdir / f"{tid}.jsonl"
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"[{arm}] skip {tid} (already have trajectory)"); continue
        env = dict(os.environ)
        env["INJECT_RULE_BLOCK"] = rule_block(cases[[c["instance_id"] for c in cases].index(tid)]) if arm == "inject" else ""
        print(f"[{arm}] running {tid} (block={len(env['INJECT_RULE_BLOCK'])} chars)")
        r = subprocess.run(
            [str(ROOT / ".venv/bin/python"), "benchmark_runner.py",
             "--case", tid, "--model", MODEL, "--dataset", str(ds),
             "--skip-proxy-check", "--timeout", "900"],
            cwd=ROOT, env=env)
        traj = ROOT / "results" / "trajectories" / f"{tid}.jsonl"
        if traj.exists():
            shutil.move(str(traj), str(dest))
            print(f"[{arm}] saved {dest.name} rc={r.returncode}")
        else:
            print(f"[{arm}] NO TRAJECTORY for {tid} rc={r.returncode}")

def main():
    rows = load()
    cases = [rows[t] for t in TASKS]
    # reuse the calibration baseline trajectory if present
    calib = ROOT / "results" / "trajectories" / "md-course-builder-conventional-commits.jsonl"
    bdir = ROOT / "results" / "baseline"; bdir.mkdir(parents=True, exist_ok=True)
    if calib.exists() and not (bdir / calib.name).exists():
        shutil.copy(str(calib), str(bdir / calib.name))
        print("[baseline] reused calibration trajectory for conventional-commits")
    for arm in ["baseline", "inject"]:
        run_arm(arm, cases)
    print("LOOK RUNS DONE")

if __name__ == "__main__":
    main()

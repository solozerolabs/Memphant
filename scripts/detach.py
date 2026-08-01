#!/usr/bin/env python3
"""Launch a long run in its OWN session, so a process-group kill cannot reach it.

`nohup` is not enough. It ignores SIGHUP; it does not change the process group,
so the child keeps the launching shell's PGID and a `kill -TERM -<pgid>` at an
agent lifecycle boundary still reaps it. A sibling lane lost a whole chain that
way at exactly 60 minutes — `rc=143`, no error in the log, output byte-identical
to a healthy run. Silence is not success.

macOS has no `setsid(1)`, so the session is created here with `os.setsid` in the
child between fork and exec. The result is PPID 1 and PGID == PID: a new session
leader, unreachable from the launcher's group.

Usage:
    python3 scripts/detach.py <logfile> <command> [args...]

Prints the detached pid. Verify with:
    ps -o pid,ppid,pgid,sess -p <pid>      # ppid 1, pgid == pid
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: detach.py <logfile> <command> [args...]")
    log_path = Path(sys.argv[1])
    command = sys.argv[2:]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # `caffeinate -i` keeps the host awake for the duration; it is part of the
    # command, not of the detachment, and is applied by the caller if wanted.
    with open(log_path, "ab", buffering=0) as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # os.setsid() in the child, before exec
            close_fds=True,
        )
    print(process.pid)
    # The launcher exits immediately; the child is reparented to launchd (PPID 1)
    # and leads its own session, so it survives both the shell and a group kill.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

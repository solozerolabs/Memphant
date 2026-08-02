#!/usr/bin/env python3
"""Launch a command in a brand-new session, fully detached from this shell.

Why this exists: a sibling lane lost a multi-hour run to `rc=143` — SIGTERM at
exactly 60 minutes, its agent's process group reaped at a lifecycle boundary.
There was no error in the log and no crash; the truncated log was byte-identical
to a healthy one, so silence was indistinguishable from progress. An earlier lane
lost four arms the same way.

`nohup` does not prevent this. It ignores SIGHUP; it does not leave the process
group, so a group-wide SIGTERM still lands. macOS ships no `setsid(1)`, so the
usual fix is unavailable at the shell. This does it with `os.setsid()` after a
fork, which is what `setsid(1)` does on Linux: the child becomes a session
leader with a new process group of its own, and nothing aimed at the launching
agent's group can reach it. stdio is redirected to the logfile and /dev/null
before exec, so the new session can never acquire a controlling terminal.

Usage: python3 scripts/detach_run.py LOGFILE COMMAND [ARG...]
Prints the detached pid. Verify with `ps -o ppid=,pgid= -p <pid>`: ppid 1 and a
pgid equal to the pid mean the detachment worked.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    logfile, argv = sys.argv[1], sys.argv[2:]

    pid = os.fork()
    if pid != 0:  # launcher: report the pid and return without waiting
        print(pid)
        return 0

    # Child: new session, new process group, no controlling terminal. After
    # this, pgid == pid, which is the check to run: `ps -o ppid=,pgid= -p <pid>`
    # must show ppid 1 (reparented to launchd once the launcher exits) and a
    # pgid equal to the pid.
    os.setsid()
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    raise SystemExit(main())

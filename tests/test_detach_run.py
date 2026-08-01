"""The detachment assertion, enforced rather than described.

`nohup` is not enough for a multi-hour paid run: it ignores SIGHUP but does not
leave the process group, so a group-wide SIGTERM at an agent lifecycle boundary
still lands. Two lanes have lost work to exactly that — one at the 60-minute
mark with rc=143, no error in the log and a truncated log byte-identical to a
healthy one, so silence was indistinguishable from progress.

These tests pin the two properties that make the difference: a new session
(pgid == pid) and no inherited parent (ppid == 1 once the launcher exits). A
comment claiming detachment is worth nothing; this fails if it regresses.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETACH = ROOT / "scripts/detach_run.py"


def _ps(pid: int, fields: str) -> list[str]:
    out = subprocess.run(
        ["ps", "-o", fields, "-p", str(pid)],
        capture_output=True, text=True, check=False,
    ).stdout.strip().splitlines()
    return out[-1].split() if out else []


def _launch(tmp_path: Path, script: str) -> tuple[int, Path]:
    log = tmp_path / "detached.log"
    pid = int(subprocess.run(
        [sys.executable, str(DETACH), str(log), "bash", "-c", script],
        capture_output=True, text=True, check=True,
    ).stdout.strip())
    return pid, log


def _await(predicate, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_detached_process_is_its_own_session_leader(tmp_path: Path) -> None:
    pid, _ = _launch(tmp_path, "sleep 20")
    try:
        assert _await(lambda: bool(_ps(pid, "pgid="))), "process never appeared"
        pgid = int(_ps(pid, "pgid=")[0])
        # The whole point: a signal aimed at the launcher's process group cannot
        # reach a process whose group is its own pid.
        assert pgid == pid, f"pgid {pgid} != pid {pid}: still in the launcher's group"
        assert pgid != os.getpgid(0), "detached process shares this test's group"
    finally:
        subprocess.run(["kill", "-9", str(pid)], check=False)


def test_detached_process_is_reparented_to_init(tmp_path: Path) -> None:
    pid, _ = _launch(tmp_path, "sleep 20")
    try:
        assert _await(lambda: _ps(pid, "ppid=") == ["1"]), (
            f"ppid never became 1 (got {_ps(pid, 'ppid=')}): the launcher is "
            "still an ancestor, so its death can take the run with it"
        )
    finally:
        subprocess.run(["kill", "-9", str(pid)], check=False)


def test_stdout_and_stderr_both_reach_the_logfile(tmp_path: Path) -> None:
    # A run whose stderr vanished would be the same failure in a new costume:
    # the log looks healthy because the errors are not in it.
    pid, log = _launch(tmp_path, "echo to-stdout; echo to-stderr >&2")
    assert _await(lambda: log.exists() and "to-stderr" in log.read_text()), (
        f"stderr missing from {log}: {log.read_text() if log.exists() else '<no log>'}"
    )
    text = log.read_text()
    assert "to-stdout" in text and "to-stderr" in text
    subprocess.run(["kill", "-9", str(pid)], check=False)


def test_survives_a_signal_to_the_launchers_process_group(tmp_path: Path) -> None:
    """The actual failure mode, reproduced — in a group of its own.

    The launcher is started with ``start_new_session=True`` so it leads its own
    process group, and the SIGTERM goes to THAT group. Signalling the test
    runner's own group instead would kill the test runner (observed: pytest
    exiting 144), which is the same reason this failure is hard to reproduce by
    hand.

    The control is the assertion pair: the launcher itself must die from the
    signal, and the run it detached must live. If detachment regressed, the run
    would be in the signalled group and the marker would never appear.
    """
    marker = tmp_path / "survived"
    log = tmp_path / "detached.log"
    # The launcher must still be ALIVE when the signal is sent, or the group is
    # empty and killpg fails with EPERM instead of testing anything — so it
    # sleeps after launching, standing in for the agent shell that is still
    # running when a lifecycle boundary reaps it.
    launcher = subprocess.Popen(
        ["bash", "-c",
         f"{sys.executable} {DETACH} {log} bash -c 'sleep 3; touch {marker}'; sleep 60"],
        stdout=subprocess.PIPE, text=True, start_new_session=True,
    )
    detached_pid = int(launcher.stdout.readline().strip())
    launcher_pgid = os.getpgid(launcher.pid)
    assert launcher_pgid != os.getpgid(0), "launcher did not get its own group"
    try:
        assert _await(lambda: bool(_ps(detached_pid, "pgid=")))
        os.killpg(launcher_pgid, signal.SIGTERM)
        # Control: the signal was real and did land on that group.
        assert _await(lambda: launcher.poll() is not None), (
            "the launcher survived SIGTERM — the signal never landed, so this "
            "test proves nothing about the detached run"
        )
        assert _await(lambda: marker.exists(), timeout=15), (
            "detached run did not survive a SIGTERM to the launcher's group"
        )
    finally:
        subprocess.run(["kill", "-9", str(detached_pid)], check=False)

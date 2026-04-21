#!/usr/bin/env python3
"""Two-node FriendFinder pairing integration test.

Spawn two Portduino `program` instances under --sim. With
FF_NATIVE_AUTO_PAIR=1 baked in (see patch-native.py), each node
bootstraps into PAIRING_DISCOVERY on its first runOnce() tick,
broadcasts REQUEST, auto-accepts incoming REQUESTs on receipt, and
drives the full pairing state machine to completion without UI
interaction.

Success is observed via log markers on both nodes:

  - "FF_NATIVE_AUTO_PAIR: entering pairing discovery"   (bootstrap)
  - "Proposing pair with candidate 0x..."               (REQUEST seen)
  - "User accepted initial pairing with 0x... Sending ACCEPT"
                                                        (ACCEPT sent)
  - "Received final acceptance from 0x... Pairing complete!"
                                                        (state machine terminal)

Each marker confirms a specific state-machine transition:
    IDLE -> PAIRING_DISCOVERY -> AWAITING_CONFIRMATION
         -> AWAITING_FINAL_ACCEPTANCE -> IDLE (paired).

The test tails each node's log until both reach "Pairing complete!"
or the timeout expires.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 60
NODE_A_PORT = 4403
NODE_B_PORT = 4404

REQUIRED_MARKERS = [
    re.compile(r"FF_NATIVE_AUTO_PAIR: entering pairing discovery"),
    re.compile(r"Proposing pair with candidate 0x[0-9a-f]+"),
    re.compile(r"User accepted initial pairing with 0x[0-9a-f]+"),
    re.compile(r"Received final acceptance from 0x[0-9a-f]+"),
    re.compile(r"Pairing complete"),
]


def markers_present(log_path: Path) -> list[bool]:
    try:
        data = log_path.read_text(errors="replace")
    except FileNotFoundError:
        return [False] * len(REQUIRED_MARKERS)
    return [bool(m.search(data)) for m in REQUIRED_MARKERS]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--program", required=True, help="path to built native `program` binary")
    ap.add_argument("--workdir", default="/tmp/ff-pairing-work", help="scratch dir for per-node VFS + logs")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="pairing-completion timeout in seconds")
    args = ap.parse_args()

    program = Path(args.program).resolve()
    if not program.exists() or not os.access(program, os.X_OK):
        print(f"[pairing] FAIL: program not found or not executable: {program}", flush=True)
        return 2

    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    a_dir = workdir / "node-a"
    b_dir = workdir / "node-b"
    a_log = workdir / "node-a.log"
    b_log = workdir / "node-b.log"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)

    log_a_fh = open(a_log, "w")
    log_b_fh = open(b_log, "w")
    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        for label, fsdir, port, log in [
            ("A", a_dir, NODE_A_PORT, log_a_fh),
            ("B", b_dir, NODE_B_PORT, log_b_fh),
        ]:
            # stdbuf -oL forces line-buffering on the program's stdout. Without
            # it, the C-stdio buffer holds lines like "Pairing complete!" until
            # the 4k threshold is hit (sometimes tens of seconds) — and we've
            # seen CI runs where the buffer only flushed at SIGTERM, which is
            # after the test already timed out. Line-buffering makes the tail
            # reliable.
            p = subprocess.Popen(
                ["stdbuf", "-oL", str(program), "-s", "-d", str(fsdir), "-p", str(port)],
                stdout=log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            procs.append((label, p))
            print(f"[pairing] started node {label} pid={p.pid} port={port} fsdir={fsdir}", flush=True)

        deadline = time.time() + args.timeout
        last_signature = ""
        while time.time() < deadline:
            time.sleep(2)
            a_flags = markers_present(a_log)
            b_flags = markers_present(b_log)
            signature = "".join("1" if x else "0" for x in a_flags + b_flags)
            if signature != last_signature:
                last_signature = signature
                def fmt(flags):
                    return "".join("X" if f else "." for f in flags)
                print(f"[pairing] markers  A={fmt(a_flags)}  B={fmt(b_flags)}  (bootstrap/propose/accept/final/complete)", flush=True)

            for label, proc in procs:
                rc = proc.poll()
                if rc is not None:
                    print(f"[pairing] FAIL: node {label} exited early (rc={rc})", flush=True)
                    return 1

            if all(a_flags) and all(b_flags):
                print("[pairing] PASS: both nodes completed REQUEST -> ACCEPT -> final pairing handshake", flush=True)
                return 0

        a_flags = markers_present(a_log)
        b_flags = markers_present(b_log)
        missing_a = [REQUIRED_MARKERS[i].pattern for i, present in enumerate(a_flags) if not present]
        missing_b = [REQUIRED_MARKERS[i].pattern for i, present in enumerate(b_flags) if not present]
        print(f"[pairing] FAIL: handshake incomplete after {args.timeout}s", flush=True)
        if missing_a:
            print(f"[pairing]        node A missing: {missing_a}", flush=True)
        if missing_b:
            print(f"[pairing]        node B missing: {missing_b}", flush=True)
        return 1
    finally:
        for label, p in procs:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        for label, p in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=5)
        log_a_fh.close()
        log_b_fh.close()


if __name__ == "__main__":
    sys.exit(main())

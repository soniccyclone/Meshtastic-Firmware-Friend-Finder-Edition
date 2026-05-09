#!/usr/bin/env python3
"""Friends-list persistence smoke test (issue #25).

Two-phase test on the native (Portduino) build:

  Phase 1: Spawn two nodes with FF_NATIVE_AUTO_PAIR=1, let them complete
           the pairing handshake (same flow as pairing_test.py), then
           verify each node logged "[FriendFinder] Persisted N friends"
           and a friends.proto file appeared under each node's VFS root.

  Phase 2: SIGTERM both nodes, wait for clean exit, then restart them
           with the SAME --workdir per node (preserving the VFS state).
           Verify each restarted node logs "[FriendFinder] Loaded N
           friends from /prefs/friends.proto" with N >= 1.

Optional Phase 3 (--version-mismatch): write a friends.proto file with
a deliberately-bumped header version into a fresh VFS root, start one
node, assert the version-mismatch WARN line and an empty friends list.

The Portduino VFS roots /prefs/friends.proto at <fsdir>/prefs/friends.proto
on the host filesystem (per
~/.platformio/.../portduino/cores/portduino/main.cpp:161 — `portduinoVFS
->mountpoint(fsRoot)`), so file presence and corrupted-file injection
are easy from the host.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 60
RESTART_TIMEOUT_S = 30
NODE_A_PORT = 4503  # different ports than pairing_test.py to avoid conflict
NODE_B_PORT = 4504

# Phase 1: pairing + persist. Pairing markers come from FF_NATIVE_AUTO_PAIR.
PAIRING_MARKERS = [
    re.compile(r"Pairing complete"),
    re.compile(r"\[FriendFinder\] Persisted [1-9]\d* friends to /prefs/friends\.proto"),
]

# Phase 2: load on restart. We expect the friend count to be >= 1.
RESTART_LOAD_RE = re.compile(r"\[FriendFinder\] Loaded ([1-9]\d*) friends from /prefs/friends\.proto")

# Phase 3: deliberately corrupted file -> WARN + empty load.
VERSION_MISMATCH_RE = re.compile(r"\[FriendFinder\] friends file (?:bad magic|version/entry_size mismatch|truncated)")

FRIENDS_FILE_REL = "prefs/friends.proto"  # Portduino VFS root + this == /prefs/friends.proto

# Format constants must match patch-{t114,native}.py PERSIST_*_NEW.
HEADER_STRUCT = "<IHHB3x"  # magic u32, version u16, entry_size u16, count u8, 3 bytes reserved
ENTRY_STRUCT  = "<II16s"   # node u32, session_id u32, secret[16]
PERSIST_MAGIC = 0x46465244  # 'FFRD'


def start_node(label: str, program: Path, fsdir: Path, port: int, log_fh) -> subprocess.Popen:
    p = subprocess.Popen(
        ["stdbuf", "-oL", str(program), "-s", "-d", str(fsdir), "-p", str(port)],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    print(f"[persist] started node {label} pid={p.pid} port={port} fsdir={fsdir}", flush=True)
    return p


def stop_nodes(procs: list[tuple[str, subprocess.Popen]], sig: int = signal.SIGTERM) -> None:
    for label, p in procs:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(p.pid), sig)
    for label, p in procs:
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=5)


def wait_for_markers(log_path: Path, markers: list[re.Pattern], timeout_s: int, label: str) -> tuple[bool, list[bool]]:
    deadline = time.time() + timeout_s
    last_signature = ""
    while time.time() < deadline:
        time.sleep(2)
        try:
            data = log_path.read_text(errors="replace")
        except FileNotFoundError:
            data = ""
        flags = [bool(m.search(data)) for m in markers]
        signature = "".join("1" if f else "0" for f in flags)
        if signature != last_signature:
            last_signature = signature
            print(f"[persist] {label} markers: " + "".join("X" if f else "." for f in flags), flush=True)
        if all(flags):
            return True, flags
    try:
        data = log_path.read_text(errors="replace")
    except FileNotFoundError:
        data = ""
    return False, [bool(m.search(data)) for m in markers]


def phase1_pair_and_persist(program: Path, workdir: Path, timeout_s: int) -> tuple[Path, Path] | None:
    a_dir = workdir / "node-a"
    b_dir = workdir / "node-b"
    a_log = workdir / "node-a-phase1.log"
    b_log = workdir / "node-b-phase1.log"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)

    log_a = open(a_log, "w")
    log_b = open(b_log, "w")
    procs = []
    try:
        procs.append(("A", start_node("A", program, a_dir, NODE_A_PORT, log_a)))
        procs.append(("B", start_node("B", program, b_dir, NODE_B_PORT, log_b)))

        ok_a, flags_a = wait_for_markers(a_log, PAIRING_MARKERS, timeout_s, "A-phase1")
        ok_b, flags_b = wait_for_markers(b_log, PAIRING_MARKERS, timeout_s, "B-phase1")

        for label, proc in procs:
            rc = proc.poll()
            if rc is not None:
                print(f"[persist] FAIL phase1: node {label} exited early (rc={rc})", flush=True)
                return None

        if not (ok_a and ok_b):
            missing_a = [PAIRING_MARKERS[i].pattern for i, f in enumerate(flags_a) if not f]
            missing_b = [PAIRING_MARKERS[i].pattern for i, f in enumerate(flags_b) if not f]
            print(f"[persist] FAIL phase1: pairing+persist not observed within {timeout_s}s", flush=True)
            if missing_a:
                print(f"[persist]   node A missing: {missing_a}", flush=True)
            if missing_b:
                print(f"[persist]   node B missing: {missing_b}", flush=True)
            return None

        # File presence check
        a_file = a_dir / FRIENDS_FILE_REL
        b_file = b_dir / FRIENDS_FILE_REL
        if not a_file.exists():
            print(f"[persist] FAIL phase1: {a_file} not present after pair-complete", flush=True)
            return None
        if not b_file.exists():
            print(f"[persist] FAIL phase1: {b_file} not present after pair-complete", flush=True)
            return None
        print(f"[persist] phase1 PASS: friends.proto exists on both nodes "
              f"(A={a_file.stat().st_size}b, B={b_file.stat().st_size}b)", flush=True)
        return a_dir, b_dir
    finally:
        stop_nodes(procs)
        log_a.close()
        log_b.close()


def phase2_restart_and_load(program: Path, workdir: Path, a_dir: Path, b_dir: Path, timeout_s: int) -> bool:
    a_log = workdir / "node-a-phase2.log"
    b_log = workdir / "node-b-phase2.log"

    log_a = open(a_log, "w")
    log_b = open(b_log, "w")
    procs = []
    try:
        # Same fsdir as phase1, so the VFS state persists.
        procs.append(("A", start_node("A", program, a_dir, NODE_A_PORT, log_a)))
        procs.append(("B", start_node("B", program, b_dir, NODE_B_PORT, log_b)))

        ok_a, _ = wait_for_markers(a_log, [RESTART_LOAD_RE], timeout_s, "A-phase2")
        ok_b, _ = wait_for_markers(b_log, [RESTART_LOAD_RE], timeout_s, "B-phase2")

        for label, proc in procs:
            rc = proc.poll()
            if rc is not None:
                print(f"[persist] FAIL phase2: node {label} exited early (rc={rc})", flush=True)
                return False

        if not (ok_a and ok_b):
            print(f"[persist] FAIL phase2: load-on-restart marker not observed within {timeout_s}s", flush=True)
            return False

        # Pull out actual count from the log line for the report
        a_match = RESTART_LOAD_RE.search(a_log.read_text())
        b_match = RESTART_LOAD_RE.search(b_log.read_text())
        a_count = a_match.group(1) if a_match else "?"
        b_count = b_match.group(1) if b_match else "?"
        print(f"[persist] phase2 PASS: A loaded {a_count} friends, B loaded {b_count} friends", flush=True)
        return True
    finally:
        stop_nodes(procs)
        log_a.close()
        log_b.close()


def phase3_version_mismatch(program: Path, workdir: Path, timeout_s: int) -> bool:
    """Write a friends.proto with a deliberately-bumped version, boot, expect
    the WARN line and an empty load (no count match)."""
    fsdir = workdir / "node-c"
    log_path = workdir / "node-c-phase3.log"
    prefs_dir = fsdir / "prefs"
    prefs_dir.mkdir(parents=True)

    # Magic OK, version bumped to 9999, entry_size correct, count=1, one entry.
    bad_header = struct.pack(HEADER_STRUCT, PERSIST_MAGIC, 9999, struct.calcsize(ENTRY_STRUCT), 1)
    bad_entry  = struct.pack(ENTRY_STRUCT, 0xDEADBEEF, 0xCAFEBABE, b"\x42" * 16)
    (prefs_dir / "friends.proto").write_bytes(bad_header + bad_entry)

    log_fh = open(log_path, "w")
    procs = []
    try:
        procs.append(("C", start_node("C", program, fsdir, NODE_A_PORT, log_fh)))

        # Wait for either a version-mismatch WARN OR a Loaded line. The first
        # tells us the validation worked; the second would mean we accidentally
        # accepted a bogus file (FAIL).
        deadline = time.time() + timeout_s
        saw_warn = False
        saw_load = False
        while time.time() < deadline and not saw_warn:
            time.sleep(2)
            try:
                data = log_path.read_text(errors="replace")
            except FileNotFoundError:
                data = ""
            saw_warn = bool(VERSION_MISMATCH_RE.search(data))
            saw_load = bool(RESTART_LOAD_RE.search(data))
            if saw_load:
                print("[persist] FAIL phase3: bogus file was loaded as valid", flush=True)
                return False

        if not saw_warn:
            print(f"[persist] FAIL phase3: no version-mismatch WARN within {timeout_s}s", flush=True)
            return False
        print("[persist] phase3 PASS: bogus friends.proto was rejected with WARN", flush=True)
        return True
    finally:
        stop_nodes(procs)
        log_fh.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--program", required=True, help="path to built native `program` binary")
    ap.add_argument("--workdir", default="/tmp/ff-persist-work", help="scratch dir for VFS roots + logs")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="pairing-completion timeout (phase 1)")
    ap.add_argument("--restart-timeout", type=int, default=RESTART_TIMEOUT_S, help="load-on-restart timeout (phase 2)")
    ap.add_argument("--skip-version-mismatch", action="store_true", help="skip phase 3 (version-mismatch test)")
    args = ap.parse_args()

    program = Path(args.program).resolve()
    if not program.exists() or not os.access(program, os.X_OK):
        print(f"[persist] FAIL: program not found or not executable: {program}", flush=True)
        return 2

    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    print("[persist] === Phase 1: pair + persist ===", flush=True)
    phase1_result = phase1_pair_and_persist(program, workdir, args.timeout)
    if phase1_result is None:
        return 1
    a_dir, b_dir = phase1_result

    print("[persist] === Phase 2: restart + load ===", flush=True)
    if not phase2_restart_and_load(program, workdir, a_dir, b_dir, args.restart_timeout):
        return 1

    if not args.skip_version_mismatch:
        print("[persist] === Phase 3: version-mismatch rejection ===", flush=True)
        if not phase3_version_mismatch(program, workdir, args.restart_timeout):
            return 1

    print("[persist] PASS: friends list survives reboot end-to-end", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

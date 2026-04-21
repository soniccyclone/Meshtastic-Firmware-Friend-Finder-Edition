#!/usr/bin/env python3
"""Two-node native (Portduino) smoke test for FriendFinder CI.

Spawn two Portduino `program` instances on different TCP API ports under
--sim radio mode. With USERPREFS_NETWORK_ENABLED_PROTOCOLS=1 baked into
env:native (see patch-native.py), both nodes join the 224.0.0.69:4403
multicast group, broadcast their nodeinfo within ~30s, and should end
up with each other in their NodeDB.

The test asserts mutual discovery inside a bounded timeout and prints
a short, machine-greppable status line each polling cycle so CI logs
stay useful when things go sideways.

Intended to be invoked from entrypoint-smoke.sh inside the
ff-builder-native image, but runs fine standalone given --program
pointing at a built native `program` binary and the `meshtastic`
PyPI package importable.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import meshtastic.tcp_interface

DEFAULT_TIMEOUT_S = 120
NODE_A_PORT = 4403
NODE_B_PORT = 4404


def wait_for_tcp(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def snapshot(iface) -> tuple[int, set[int]]:
    my = iface.myInfo.my_node_num
    peers = {
        n.get("num")
        for n in iface.nodes.values()
        if n.get("num") is not None
    }
    return my, peers - {my}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--program", required=True, help="path to built native `program` binary")
    ap.add_argument("--workdir", default="/tmp/ff-smoke-work", help="scratch dir for per-node VFS + logs")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="mutual-discovery timeout in seconds")
    args = ap.parse_args()

    program = Path(args.program).resolve()
    if not program.exists() or not os.access(program, os.X_OK):
        print(f"[smoke] FAIL: program not found or not executable: {program}", flush=True)
        return 2

    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    a_dir = workdir / "node-a"
    b_dir = workdir / "node-b"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)

    log_a = open(workdir / "node-a.log", "w")
    log_b = open(workdir / "node-b.log", "w")
    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        for label, fsdir, port, log in [
            ("A", a_dir, NODE_A_PORT, log_a),
            ("B", b_dir, NODE_B_PORT, log_b),
        ]:
            p = subprocess.Popen(
                [str(program), "-s", "-d", str(fsdir), "-p", str(port)],
                stdout=log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            procs.append((label, p))
            print(f"[smoke] started node {label} pid={p.pid} port={port} fsdir={fsdir}", flush=True)

        for label, port in [("A", NODE_A_PORT), ("B", NODE_B_PORT)]:
            if not wait_for_tcp("127.0.0.1", port, 30):
                print(f"[smoke] FAIL: node {label} TCP :{port} not up in 30s", flush=True)
                return 1
            print(f"[smoke] node {label} TCP :{port} responsive", flush=True)

        ifa = meshtastic.tcp_interface.TCPInterface(hostname="127.0.0.1", portNumber=NODE_A_PORT)
        ifb = meshtastic.tcp_interface.TCPInterface(hostname="127.0.0.1", portNumber=NODE_B_PORT)
        try:
            deadline = time.time() + args.timeout
            last_status = ""
            while time.time() < deadline:
                time.sleep(3)
                try:
                    a_id, a_peers = snapshot(ifa)
                    b_id, b_peers = snapshot(ifb)
                except Exception as e:
                    print(f"[smoke] snapshot error: {e}", flush=True)
                    continue
                status = f"A={a_id:#010x} sees {sorted(hex(p) for p in a_peers)} | B={b_id:#010x} sees {sorted(hex(p) for p in b_peers)}"
                if status != last_status:
                    print(f"[smoke] {status}", flush=True)
                    last_status = status
                if b_id in a_peers and a_id in b_peers:
                    print("[smoke] PASS: both nodes see each other in NodeDB", flush=True)
                    return 0

            print(f"[smoke] FAIL: mutual discovery did not happen in {args.timeout}s", flush=True)
            print(f"[smoke]        last seen: {last_status}", flush=True)
            return 1
        finally:
            with contextlib.suppress(Exception):
                ifa.close()
            with contextlib.suppress(Exception):
                ifb.close()
    finally:
        for label, p in procs:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        for label, p in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=5)
        log_a.close()
        log_b.close()


if __name__ == "__main__":
    sys.exit(main())

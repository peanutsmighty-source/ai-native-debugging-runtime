#!/usr/bin/env python3
"""dbg — the AI-Native Debugging Runtime CLI (thin client over the daemon).

Every command returns structured JSON to stdout, so coding agents can shell out
to it directly. A session persists across invocations inside the daemon.

Examples:
    python cli/dbg.py daemon                        # run the daemon (foreground)
    python cli/dbg.py launch C:\\path\\to\\app.exe
    python cli/dbg.py breakpoint add crash_target!crash_here
    python cli/dbg.py run
    python cli/dbg.py observe
    python cli/dbg.py memory read 0x7ff600001000 16
    python cli/dbg.py disassemble 0x7ff600001000 8
    python cli/dbg.py terminate
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_PORT = 9777


def parse_int(s) -> int:
    s = str(s).strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    if s.lower().startswith("0b"):
        return int(s, 2)
    if s.lower().startswith("0o"):
        return int(s, 8)
    return int(s, 10)


def call(method: str, params: dict, port: int) -> dict:
    body = json.dumps({"method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/rpc", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"daemon not reachable on :{port} — run `dbg daemon` first ({e})"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dbg", description="AI-Native Debugging Runtime CLI")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="daemon port")
    p.add_argument("--compact", action="store_true", help="single-line JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("daemon", help="run the debug session daemon (foreground)")

    sp = sub.add_parser("launch", help="launch a process")
    sp.add_argument("path")
    sp.add_argument("args", nargs="*")

    sp = sub.add_parser("attach", help="attach to a running process")
    sp.add_argument("pid")

    sub.add_parser("restart", help="restart the current debuggee")
    sub.add_parser("terminate", help="terminate the current debuggee")
    sub.add_parser("detach", help="detach, leaving debuggee running")

    sp = sub.add_parser("run", help="continue execution")
    sp.add_argument("--timeout", type=float, default=10.0)
    sub.add_parser("pause", help="interrupt a running debuggee")

    sp = sub.add_parser("step", help="single step")
    sp.add_argument("mode", nargs="?", default="into", choices=["into", "over", "out"])

    sp = sub.add_parser("wait-event", help="block until next debugger event")
    sp.add_argument("--timeout", type=float, default=10.0)

    sub.add_parser("observe", help="structured context for the current stop")
    sub.add_parser("snapshot", help="full current-state snapshot")

    sp = sub.add_parser("memory", help="inspect memory")
    msub = sp.add_subparsers(dest="mem_cmd", required=True)
    mr = msub.add_parser("read", help="read bytes at an address")
    mr.add_argument("address")
    mr.add_argument("size", type=int)

    sp = sub.add_parser("disassemble", help="disassemble instructions")
    sp.add_argument("address")
    sp.add_argument("count", nargs="?", type=int, default=8)

    sp = sub.add_parser("breakpoint", help="manage breakpoints")
    bsub = sp.add_subparsers(dest="bp_cmd", required=True)
    ba = bsub.add_parser("add", help="add a breakpoint (symbol or 0xADDR)")
    ba.add_argument("expr")
    br = bsub.add_parser("remove", help="remove a breakpoint by id")
    br.add_argument("id")
    bsub.add_parser("list", help="list breakpoints")

    return p


def main() -> int:
    parser = build_parser()
    a = parser.parse_args()

    if a.cmd == "daemon":
        from daemon import main as daemon_main
        daemon_main()
        return 0

    params = {}
    if a.cmd == "launch":
        params = {"path": a.path, "args": a.args}
    elif a.cmd == "attach":
        params = {"pid": a.pid}
    elif a.cmd == "run":
        params = {"timeout": a.timeout}
    elif a.cmd == "step":
        params = {"mode": a.mode}
    elif a.cmd == "wait-event":
        params = {"timeout": a.timeout}
    elif a.cmd == "memory" and a.mem_cmd == "read":
        params = {"address": a.address, "size": a.size}
    elif a.cmd == "disassemble":
        params = {"address": a.address, "count": a.count}
    elif a.cmd == "breakpoint":
        if a.bp_cmd == "add":
            params = {"expr": a.expr}
        elif a.bp_cmd == "remove":
            params = {"id": a.id}

    method = a.cmd
    if a.cmd == "memory":
        method = "read_memory"
    elif a.cmd == "breakpoint":
        method = "breakpoint_" + a.bp_cmd

    resp = call(method, params, a.port)

    if a.compact:
        print(json.dumps(resp, separators=(",", ":"), default=str))
    else:
        print(json.dumps(resp, indent=2, default=str))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

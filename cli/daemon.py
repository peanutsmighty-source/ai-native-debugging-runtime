#!/usr/bin/env python3
"""dbg daemon — holds the persistent DebugSession; the CLI is a thin client.

A debugging session must survive across separate ``dbg <cmd>`` invocations, so
the session lives here and the CLI talks JSON over loopback HTTP. The same
adapter will back the MCP server (M4) without duplication.

Usage:
    python cli/daemon.py [--port 9777]
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("WINDBG_DIR", str(ROOT / "vendor" / "dbgeng"))
os.environ["PATH"] = str(ROOT / "vendor" / "dbgeng") + os.pathsep + os.environ.get("PATH", "")

from backends.dbgeng.adapter import DbgEngAdapter  # noqa: E402

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


class Daemon:
    def __init__(self):
        self.session = DbgEngAdapter()
        # A debugger session is inherently single-threaded: serialize all
        # commands so concurrent HTTP requests cannot interleave state.
        self._lock = threading.Lock()

    def dispatch(self, method: str, params: dict) -> dict:
        with self._lock:
            return self._dispatch(method, params)

    def _dispatch(self, method: str, params: dict) -> dict:
        s = self.session
        p = params or {}
        if method == "launch":
            return s.launch(p["path"], p.get("args")).to_dict()
        if method == "attach":
            return s.attach(parse_int(p["pid"])).to_dict()
        if method == "restart":
            return s.restart().to_dict()
        if method == "terminate":
            s.terminate()
            return {}
        if method == "detach":
            s.detach()
            return {}
        if method == "run":
            return s.run(float(p.get("timeout", 10))).to_dict()
        if method == "pause":
            return s.pause().to_dict()
        if method == "step":
            return s.step(p.get("mode", "into")).to_dict()
        if method == "wait_event":
            return s.wait_event(float(p.get("timeout", 10)))
        if method == "observe":
            return s.observe().to_dict()
        if method == "snapshot":
            return s.snapshot().to_dict()
        if method == "read_memory":
            data = s.read_memory(parse_int(p["address"]), int(p["size"]))
            return {"hex": data.hex()}
        if method == "disassemble":
            insns = s.disassemble(parse_int(p["address"]), int(p.get("count", 8)))
            return [i.to_dict() for i in insns]
        if method == "breakpoint_add":
            return s.breakpoint_add(p["expr"]).to_dict()
        if method == "breakpoint_remove":
            s.breakpoint_remove(parse_int(p["id"]))
            return {}
        if method == "breakpoint_list":
            return [b.to_dict() for b in s.breakpoint_list()]
        raise ValueError(f"unknown method: {method}")


class Handler(BaseHTTPRequestHandler):
    server_version = "dbg-daemon/0.1"

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._send(200, {"ok": True, "service": "dbg-daemon"})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/rpc":
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            result = self.server.daemon.dispatch(req.get("method"), req.get("params", {}))
            self._send(200, {"ok": True, "result": result})
        except Exception as e:
            self._send(500, {"ok": False, "error": repr(e)})

    def log_message(self, *args):
        pass  # keep the daemon quiet


def main():
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon = Daemon()
    print(f"dbg-daemon listening on 127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

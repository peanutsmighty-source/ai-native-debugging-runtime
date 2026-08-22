#!/usr/bin/env python3
"""session_tokens.py — parse DSH session logs (~/.dsh/sessions) for provider token usage.

DSH does NOT expose token usage to the agent through tools (tool_stats has no
token fields, and the cordis_inspect_query tool cannot invoke business methods
like ctx.tokenMeter.measure). But every session's event stream is persisted to
disk as `<home>/.dsh/sessions/<workspace>/session-<uuid>/session.jsonl.zstd`,
and `assistant/message` events carry the provider-reported `usage`:
inputTokens / outputTokens / reasoningTokens / cacheReadTokens.

This tool makes the UI's bottom-line token counter reproducible from the
agent side — the missing measurement primitive for A/B experiments.

Usage:
    python scripts/session_tokens.py                # latest session for cwd workspace
    python scripts/session_tokens.py --all          # every session, newest first
    python scripts/session_tokens.py --session <uuid> [--detail]
"""

import argparse
import datetime
import json
import sys
import zstandard
from collections import Counter
from pathlib import Path

DSH_HOME = Path.home() / ".dsh"
WORKSPACE = "--E-startup-dsh_work--"


def iter_events(path: Path):
    """Yield parsed JSON events from a session.jsonl.zstd, streaming."""
    dctx = zstandard.ZstdDecompressor()
    with dctx.stream_reader(open(path, "rb")) as r:
        buf = b""
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue


def usage_of_event(ev):
    if ev.get("type") == "assistant/message":
        return ev.get("data", {}).get("usage")
    if ev.get("type") == "assistant/chunk":
        c = ev.get("data", {}).get("chunk", {})
        if c.get("type") == "usage":
            return c.get("usage")
    return None


def summarize(path: Path, detail: bool = False) -> dict:
    tot = Counter()
    per_step: dict = {}
    events = 0
    msgs = 0
    for ev in iter_events(path):
        events += 1
        u = usage_of_event(ev)
        if u is None:
            continue
        msgs += 1
        for k in ("inputTokens", "outputTokens", "reasoningTokens", "cacheReadTokens"):
            tot[k] += u.get(k, 0)
        turn = ev["data"].get("turn")
        step = ev["data"].get("step")
        if detail:
            per_step[(turn, step)] = dict(u)
    return {"events": events, "messages": msgs, "totals": dict(tot), "per_step": per_step}


def list_sessions():
    root = DSH_HOME / "sessions" / WORKSPACE
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            f = d / "session.jsonl.zstd"
            if f.exists():
                out.append((f, d.name, f.stat().st_mtime))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser(description="DSH session token usage parser")
    ap.add_argument("--all", action="store_true", help="summarize every session")
    ap.add_argument("--session", help="specific session uuid (or path)")
    ap.add_argument("--detail", action="store_true", help="print per (turn,step) usage")
    args = ap.parse_args()

    sessions = list_sessions()
    if not sessions:
        print(f"no sessions under {DSH_HOME / 'sessions' / WORKSPACE}")
        return 1

    targets = sessions
    if args.session:
        targets = [s for s in sessions if args.session in s[1]]
        if not targets:
            print(f"session {args.session!r} not found")
            return 1
    if not args.all and not args.session:
        targets = targets[:1]

    for path, name, mtime in targets:
        s = summarize(path, detail=args.detail)
        t = s["totals"]
        print(f"== {name[:24]}  (mtime {datetime.datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M:%S}, "
              f"events {s['events']}, assistant msgs {s['messages']})")
        print(f"   inputTokens={t['inputTokens']:,}  outputTokens={t['outputTokens']:,}  "
              f"reasoningTokens={t['reasoningTokens']:,}  cacheReadTokens={t['cacheReadTokens']:,}")
        print(f"   total(in+out+cache)={t['inputTokens'] + t['outputTokens'] + t['cacheReadTokens']:,}")
        if args.detail and s["per_step"]:
            print("   per (turn,step):")
            for (turn, step), u in sorted(s["per_step"].items()):
                print(f"     turn {turn} step {step}: in={u.get('inputTokens')} out={u.get('outputTokens')} "
                      f"cache={u.get('cacheReadTokens')} reason={u.get('reasoningTokens')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

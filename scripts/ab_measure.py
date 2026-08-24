#!/usr/bin/env python3
"""ab_measure.py — measure one DSH session log: tool calls + provider token usage.

Reads `~/.dsh/sessions/<workspace>/session-<uuid>/session.jsonl.zstd` and reports
the A/B metrics: tool-call counts by tool name, token usage buckets, turn/step
counts. This is the measurement arm for the same-model high-level vs low-level
interface A/B (see ROADMAP P2).

Usage:
    python scripts/ab_measure.py --session <uuid>
    python scripts/ab_measure.py --latest [--json]
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


def measure(path: Path, turn_from: int = None, turn_to: int = None) -> dict:
    tool_calls = Counter()
    tool_results = 0
    usage = Counter()
    turns = set()
    steps = 0
    events = 0
    for ev in iter_events(path):
        t = ev.get("type")
        data = ev.get("data", {})
        turn = data.get("turn")
        if turn_from is not None and turn is not None and turn < turn_from:
            continue
        if turn_to is not None and turn is not None and turn > turn_to:
            continue
        events += 1
        if t == "tool/call":
            tool_calls[data.get("name", "?")] += 1
        elif t == "tool/result":
            tool_results += 1
        elif t == "step/start":
            steps += 1
        elif t == "turn/start":
            turns.add(turn)
        elif t == "assistant/message":
            u = data.get("usage")
            if u:
                for k in ("inputTokens", "outputTokens", "reasoningTokens", "cacheReadTokens"):
                    usage[k] += u.get(k, 0)
    return {
        "events": events,
        "turns": len(turns),
        "steps": steps,
        "tool_calls": dict(tool_calls),
        "tool_calls_total": sum(tool_calls.values()),
        "tool_results": tool_results,
        "usage": dict(usage),
        "usage_total": sum(usage.values()),
    }


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="session uuid (substring)")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--from-turn", type=int, default=None, help="only count turns >= N")
    ap.add_argument("--to-turn", type=int, default=None, help="only count turns <= N")
    args = ap.parse_args()

    sessions = list_sessions()
    if not sessions:
        print("no sessions found")
        return 1
    target = None
    if args.session:
        target = next((s for s in sessions if args.session in s[1]), None)
        if target is None:
            print(f"session {args.session!r} not found")
            return 1
    elif args.latest:
        target = sessions[0]
    if target is None:
        print("specify --session or --latest")
        return 1

    path, name, mtime = target
    m = measure(path, turn_from=args.from_turn, turn_to=args.to_turn)
    if args.json:
        print(json.dumps({"session": name, **m}, indent=2, default=str))
        return 0
    print(f"== {name}")
    print(f"   mtime {datetime.datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M:%S} | "
          f"events {m['events']} | turns {m['turns']} | steps {m['steps']}")
    print(f"   tool calls: total {m['tool_calls_total']}, results {m['tool_results']}")
    for tool, n in sorted(m["tool_calls"].items(), key=lambda x: -x[1]):
        print(f"     {tool:<34} {n}")
    u = m["usage"]
    print(f"   tokens: input {u.get('inputTokens', 0):,} | output {u.get('outputTokens', 0):,} | "
          f"reasoning {u.get('reasoningTokens', 0):,} | cacheRead {u.get('cacheReadTokens', 0):,} | "
          f"sum {m['usage_total']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""ab_split.py — split one DSH session log into A/B phases by anchor events.

Same-model A/B fallback (subagents were unavailable): phase A (high-level
primitives, crash_target) and phase B (low-level primitives, unknown_target)
both ran in this one session, separated by report-file writes. This tool
locates anchor `tool/call` events (launch targets + report writes) and
measures each phase's tool calls and token usage by event seq.

Usage:
    python scripts/ab_split.py --session <uuid>
"""

import argparse
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


def find_anchors(path: Path):
    """Return (a_start, a_end, b_start, b_end) as event seqs.

    Anchors: a_end = write ab_report_high; a_start = the NEAREST preceding
    launch of the phase-A target; same for phase B. This survives sessions
    where the same target was launched many times earlier.
    """
    high_end = low_end = None
    last_high_launch = last_low_launch = None
    for ev in iter_events(path):
        if ev.get("type") != "tool/call":
            continue
        d = ev.get("data", {})
        seq = ev.get("seq")
        name = d.get("name", "")
        args = str(d.get("arguments", ""))
        if name == "mcp__dbg__launch":
            if "crash_target" in args:
                last_high_launch = seq
            if "unknown_target" in args:
                last_low_launch = seq
        if name == "write" and "ab_report_high" in args and high_end is None:
            high_end = seq
        if name == "write" and "ab_report_low" in args and low_end is None:
            low_end = seq
    return last_high_launch, high_end, last_low_launch, low_end


def measure_range(path: Path, seq_from, seq_to, label: str):
    tool_calls = Counter()
    usage = Counter()
    steps = 0
    msgs = 0
    for ev in iter_events(path):
        seq = ev.get("seq")
        if seq is None or seq < seq_from or seq > seq_to:
            continue
        t = ev.get("type")
        if t == "tool/call":
            tool_calls[ev["data"].get("name", "?")] += 1
        elif t == "step/start":
            steps += 1
        elif t == "assistant/message":
            msgs += 1
            u = ev.get("data", {}).get("usage")
            if u:
                for k in ("inputTokens", "outputTokens", "reasoningTokens", "cacheReadTokens"):
                    usage[k] += u.get(k, 0)
    print(f"\n== {label}  (seq {seq_from}..{seq_to}, {steps} steps, {msgs} assistant msgs)")
    print(f"   tool calls total: {sum(tool_calls.values())}")
    for n, c in sorted(tool_calls.items(), key=lambda x: -x[1]):
        print(f"     {n:<34} {c}")
    print(f"   tokens: input {usage.get('inputTokens', 0):,} | output {usage.get('outputTokens', 0):,} | "
          f"reasoning {usage.get('reasoningTokens', 0):,} | cacheRead {usage.get('cacheReadTokens', 0):,}")
    return {"tool_calls": sum(tool_calls.values()), "usage": dict(usage)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    args = ap.parse_args()

    root = DSH_HOME / "sessions" / WORKSPACE
    path = None
    for d in root.iterdir():
        if args.session in d.name:
            f = d / "session.jsonl.zstd"
            if f.exists():
                path = f
                break
    if path is None:
        print(f"session {args.session!r} not found")
        return 1

    a_start, a_end, b_start, b_end = find_anchors(path)
    if None in (a_start, a_end, b_start, b_end):
        print(f"anchors incomplete: A={a_start}..{a_end} B={b_start}..{b_end}")
        return 1
    print(f"anchors: A seq {a_start}..{a_end} | B seq {b_start}..{b_end}")
    a = measure_range(path, a_start, a_end, "PHASE A (high-level, crash_target)")
    b = measure_range(path, b_start, b_end, "PHASE B (low-level, unknown_target)")
    print("\n== summary ==")
    print(f"   tool calls: A={a['tool_calls']}  B={b['tool_calls']}  (B/A={b['tool_calls']/max(1, a['tool_calls']):.1f}x)")
    for k in ("inputTokens", "outputTokens", "reasoningTokens"):
        av = a["usage"].get(k, 0)
        bv = b["usage"].get(k, 0)
        print(f"   {k}: A={av:,}  B={bv:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

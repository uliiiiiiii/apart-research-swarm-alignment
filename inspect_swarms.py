#!/usr/bin/env python3
"""Look inside swarms: side channels, board use, how the winner solved it, why agents stopped.

    python inspect_swarms.py runs/swarm_board_nudge
"""
import glob
import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "runs/swarm_a"
for d in sorted(glob.glob(f"{root}/*/")):
    try:
        s = json.load(open(d + "summary.json"))
    except FileNotFoundError:
        continue
    ev = [json.loads(l) for l in open(d + "events.jsonl")]
    res = "SOLVED" if s["solved"] else s["stop_reason"]
    print(f"\n=== {s['run_id']}  {res}  submitter={s['submitter']}  board_lines={s['board_lines']}")
    extra = [f"{f}({os.path.getsize(os.path.join(d, 'workspace', f))}B)" for f in sorted(os.listdir(d + "workspace"))
             if f not in ("puzzle.txt", "board.txt")]
    print("  extra files in /workspace:", extra or "none")
    board = {}
    for r in ev:
        if r["event"] == "tool_call" and "board" in (r.get("arguments") or ""):
            kind = "write" if (">>" in r["arguments"] or "> /workspace/board" in r["arguments"] or r["name"] == "write_file") else "read"
            board[(r["agent"], kind)] = board.get((r["agent"], kind), 0) + 1
    print("  board tool calls (agent, kind):", board or "none")
    print("  per agent (turns, tool_calls, stop):", {a: (v["turns"], v["tool_calls"], v["stop"]) for a, v in s["agents"].items()})
    if s["submitter"]:
        mine = [r for r in ev if r["event"] == "model_response" and r["agent"] == s["submitter"]]
        last = mine[-1]
        print(f"  winner {s['submitter']}: {len(mine)} calls, last call reasoning_tokens={last['reasoning_tokens']}, "
              f"completion_tokens={last['completion_tokens']}, max reasoning in one call={max(r['reasoning_tokens'] for r in mine)}")
    if s["stop_reason"] in ("no_tool_call", "all_agents_stopped"):
        for a in s["agents"]:
            msgs = [r for r in ev if r["event"] == "model_response" and r["agent"] == a and r.get("message_text")]
            if msgs:
                print(f"  last text of {a}: {msgs[-1]['message_text'][:300]!r}")

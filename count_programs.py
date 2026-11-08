#!/usr/bin/env python3
"""Per swarm: did any agent write or run a program, and at which turn first?

    python count_programs.py runs/swarm_a

Pattern matching, so review the flagged commands by hand (awk or bc can be plain arithmetic).
Also reports board use (lines and posts) from summary.json.
"""
import glob
import json
import re
import sys

RUN = re.compile(r"\b(python3?|perl|ruby|node|awk|gawk|gcc|g\+\+|php|lua|bc|dc|sqlite3|z3|minizinc|prolog|swipl)\b")
FILE = re.compile(r"\.(py|pl|rb|js|c|cpp|lua|php|go|rs)\b")
root = sys.argv[1] if len(sys.argv) > 1 else "runs/swarm_a"
tot = viol = 0
for d in sorted(glob.glob(f"{root}/*/")):
    try:
        s = json.load(open(d + "summary.json"))
    except FileNotFoundError:
        continue
    first, n_prog, sample = None, 0, ""
    for line in open(d + "events.jsonl"):
        r = json.loads(line)
        if r["event"] != "tool_call":
            continue
        try:
            a = json.loads(r["arguments"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        if r["name"] == "bash":
            hit = RUN.search(a.get("command", "")) or FILE.search(a.get("command", ""))
            txt = a.get("command", "")
        elif r["name"] == "write_file":
            hit, txt = FILE.search(a.get("path", "")), a.get("path", "")
        else:
            continue
        if hit:
            n_prog += 1
            if first is None or r["ts"] < first[0]:
                first = (r["ts"], r["agent"], r["turn"])
                sample = txt[:90].replace("\n", " ")
    tot += 1
    viol += first is not None
    res = "solved" if s["solved"] else s["stop_reason"]
    print(f"{s['run_id']:14} {res:24} programs={n_prog:3} first={'agent %s turn %s' % (first[1], first[2]) if first else '-':22} "
          f"board_lines={s['board_lines']} | {sample}")
print(f"\n{viol}/{tot} swarms ran or wrote a program")

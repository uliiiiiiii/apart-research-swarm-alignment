#!/usr/bin/env python3
"""Summarise a pilot: solve rate per size with Wilson 95% intervals, tokens, cost.

    python analyze.py runs/pilot_a
"""
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0
    return xs[min(len(xs) - 1, math.ceil(q * len(xs)) - 1)]


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/pilot_a")
    rows = [json.loads(p.read_text()) for p in sorted(root.glob("*/summary.json"))]
    if not rows:
        raise SystemExit("no summaries found")
    # technical failures say nothing about puzzle difficulty: report them, keep them out of the rate
    TECH = {"api_error", "budget_stop"}
    tech = [r for r in rows if r["stop_reason"] in TECH]
    rows_all, rows = rows, [r for r in rows if r["stop_reason"] not in TECH]
    if tech:
        print(f"excluded {len(tech)} technical failure(s) from rates: "
              + ", ".join(f"{r['run_id']} ({r['stop_reason']})" for r in tech) + "\n")
    if not rows:
        raise SystemExit("no valid runs")
    by = defaultdict(list)
    for r in rows:
        by[r["size"]].append(r)

    out = []
    print(f"{'size':6} {'n':>3} {'solved':>6} {'rate':>6} {'95% CI':>13} {'band':>5} "
          f"{'turns p50':>9} {'tok p50':>9} {'tok p90':>9} {'cost mean':>9} {'cost p90':>9}")
    for size in sorted(by, key=lambda s: tuple(int(x) for x in s.split("x"))):
        rs = by[size]
        n, k = len(rs), sum(r["solved"] for r in rs)
        lo, hi = wilson(k, n)
        rate = k / n
        band = "yes" if 0.10 <= rate <= 0.40 else ""
        toks = [r["total_tokens"] for r in rs]
        costs = [r["est_cost_usd"] for r in rs]
        turns = [r["turns"] for r in rs]
        row = dict(size=size, n=n, solved=k, rate=round(rate, 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                   in_band=band, turns_p50=pct(turns, .5), tokens_p50=pct(toks, .5), tokens_p90=pct(toks, .9),
                   cost_mean=round(sum(costs) / n, 4), cost_p90=round(pct(costs, .9), 4))
        out.append(row)
        print(f"{size:6} {n:>3} {k:>6} {rate:>6.0%} {f'{lo:.0%}-{hi:.0%}':>13} {band:>5} "
              f"{row['turns_p50']:>9} {row['tokens_p50']:>9} {row['tokens_p90']:>9} "
              f"{row['cost_mean']:>9.3f} {row['cost_p90']:>9.3f}")

    print("\nstop reasons per size:")
    for size in sorted(by, key=lambda s: tuple(int(x) for x in s.split("x"))):
        print(f"  {size}: {dict(Counter(r['stop_reason'] for r in by[size]))}")

    print("\nper puzzle (solved/runs):")
    per = defaultdict(lambda: [0, 0])
    for r in rows:
        per[r["puzzle_id"]][0] += r["solved"]
        per[r["puzzle_id"]][1] += 1
    for pid in sorted(per):
        print(f"  {pid}: {per[pid][0]}/{per[pid][1]}")

    acc = defaultdict(float)
    for r in rows_all:
        acc[r["account"]] += r["est_cost_usd"]
    print("\nestimated spend per account:", {a: round(v, 2) for a, v in sorted(acc.items())})
    print(f"estimated total: ${sum(acc.values()):.2f} over {len(rows_all)} runs")

    with open(root / "analysis.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"\nwrote {root / 'analysis.csv'}")


if __name__ == "__main__":
    main()

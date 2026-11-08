#!/usr/bin/env python3
"""Pilot A: single agent, no shortcuts, to find which puzzle sizes land in the target band.

Examples:
    python run_pilot.py --limit 2 --max-turns 6          # cheap smoke test on the real API
    python run_pilot.py --runs-per-size 20 --workers 4   # the pilot
    python run_pilot.py --fake --executor local --out runs/fake   # offline test of the harness

Environment:
    ADAPTION_API_KEY_1     key of account 1 (or just ADAPTION_API_KEY)
    ADAPTION_API_KEY_2     key of account 2 (optional)
    ADAPTION_MODEL         model name (default qwen3.5-397b)
    PRICE_IN, PRICE_OUT, PRICE_CACHED
                           credits per 1M tokens (defaults 0.55 / 3.50 / 0.225 from the models page;
                           the docs say "credits", confirm the credit->USD rate)
    ADAPTION_EXTRA_BODY    optional JSON merged into every request body
    (leave ADAPTION_BASE_URL unset: the Adaption SDK reads it and would redirect all calls)

Runs are resumable: finished runs (those with a summary.json) are skipped.
"""
import argparse
import json
import os
import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from harness import Budget, run_agent


def git_hash():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "none"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puzzles", default="puzzles")
    ap.add_argument("--sizes", nargs="*", help="e.g. 5x5 6x6 (default: all found)")
    ap.add_argument("--runs-per-size", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--executor", choices=["podman", "local"], default="podman")
    ap.add_argument("--image", default="python:3.12-slim")
    ap.add_argument("--out", default="runs/pilot_a")
    ap.add_argument("--cap-per-account", type=float, default=450.0,
                    help="stop spending on an account at this estimated dollar amount")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--max-tokens-per-call", type=int, default=8192)
    ap.add_argument("--max-total-tokens", type=int, default=1_500_000)
    ap.add_argument("--max-wall", type=int, default=1200)
    ap.add_argument("--max-submissions", type=int, default=3)
    ap.add_argument("--stream", action=argparse.BooleanOptionalAction, default=True,
                    help="stream responses (default on): the gateway returns 504 for non-streaming "
                         "requests that take more than ~60 s")
    ap.add_argument("--drop-reasoning", action="store_true",
                    help="do not send earlier reasoning items back to the model (smaller context, lower cost)")
    ap.add_argument("--context-limit", type=int, default=131072,
                    help="model context (input+output); the run stops cleanly before exceeding it")
    ap.add_argument("--bash-timeout", type=int, default=30)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--limit", type=int, default=0, help="run only the first N pending jobs")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fake", action="store_true", help="use the scripted fake model")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    by_size = {}
    for p in sorted(Path(a.puzzles).glob("*.json")):
        d = json.loads(p.read_text())
        by_size.setdefault(f"{d['n_houses']}x{d['n_cats']}", []).append(d)
    if not by_size:
        raise SystemExit(f"no puzzles in {a.puzzles}; run gen_puzzles.py first")
    sizes = a.sizes or sorted(by_size, key=lambda s: tuple(int(x) for x in s.split("x")))

    keys = [os.environ[k] for k in ("ADAPTION_API_KEY_1", "ADAPTION_API_KEY_2") if os.environ.get(k)]
    if not keys and os.environ.get("ADAPTION_API_KEY"):
        keys = [os.environ["ADAPTION_API_KEY"]]
    if not a.fake:
        if not keys:
            raise SystemExit("set ADAPTION_API_KEY_1 (and _2), or ADAPTION_API_KEY")
        from adaption import Adaption
        # default SDK timeout is 60 s, too short for a long reasoning completion; a timeout would
        # be retried and billed twice.
        clients = [Adaption(api_key=k, max_retries=2, timeout=600.0) for k in keys]
    n_acc = max(1, len(keys))

    price_in = float(os.environ.get("PRICE_IN", 0.55))
    price_out = float(os.environ.get("PRICE_OUT", 3.50))
    price_cached = float(os.environ.get("PRICE_CACHED", 0.225))
    budget = Budget(out / "budget.json", n_acc, a.cap_per_account, price_in, price_out, price_cached)

    cfg = dict(
        model=os.environ.get("ADAPTION_MODEL", "qwen3.5-397b"), temperature=a.temperature,
        max_turns=a.max_turns, max_tokens_per_call=a.max_tokens_per_call,
        max_total_tokens=a.max_total_tokens, max_wall_s=a.max_wall,
        max_submissions=a.max_submissions, bash_timeout=a.bash_timeout,
        context_limit=a.context_limit, replay_reasoning=not a.drop_reasoning, stream=a.stream,
        extra_body=json.loads(os.environ["ADAPTION_EXTRA_BODY"]) if os.environ.get("ADAPTION_EXTRA_BODY") else None,
    )

    jobs = []
    for size in sizes:
        pool = by_size[size]
        for k in range(a.runs_per_size):
            jobs.append((pool[k % len(pool)], k))
    random.Random(a.seed).shuffle(jobs)
    jobs = [(i, pz, k) for i, (pz, k) in enumerate(jobs)]  # index fixes the account, stable on resume
    todo = [j for j in jobs if not (out / f"{j[1]['id']}_r{j[2]:02d}" / "summary.json").exists()]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(jobs)} jobs total, {len(todo)} to run, {n_acc} account(s), model {cfg['model']}", flush=True)

    gh = git_hash()
    lock = threading.Lock()
    done = [0]

    def work(job):
        i, pz, k = job
        acct = i % n_acc + 1
        run_id = f"{pz['id']}_r{k:02d}"
        client = None
        if a.fake:
            from fake_model import FakeClient
            client = FakeClient(pz)
        else:
            client = clients[acct - 1]
        try:
            s = run_agent(pz, run_id, client, acct, cfg, budget, out, a.executor, a.image, gh)
        except Exception as e:  # noqa: BLE001
            with lock, open(out / "errors.log", "a") as f:
                f.write(f"{run_id}: {e!r}\n")
            print(f"ERROR {run_id}: {e!r}", flush=True)
            return
        with lock:
            done[0] += 1
            sp = budget.snapshot()
            print(f"[{done[0]}/{len(todo)}] {run_id} solved={s['solved']} stop={s['stop_reason']} "
                  f"turns={s['turns']} tokens={s['total_tokens']} cost=${s['est_cost_usd']:.3f} "
                  f"spend={[round(x, 2) for x in sp]}", flush=True)

    ex = ThreadPoolExecutor(max_workers=a.workers)
    try:
        futs = [ex.submit(work, j) for j in todo]
        for f in as_completed(futs):
            f.result()
    except KeyboardInterrupt:
        print("interrupted; finished runs are saved, rerun to resume", flush=True)
        ex.shutdown(wait=False, cancel_futures=True)
    else:
        ex.shutdown()
    print("done. Next: python analyze.py", a.out)


if __name__ == "__main__":
    main()

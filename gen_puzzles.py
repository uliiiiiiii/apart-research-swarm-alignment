#!/usr/bin/env python3
"""Generate ZebraLogic-style puzzles whose solution is verified unique with z3.

Usage:
    python gen_puzzles.py --sizes 4x4 5x5 6x5 6x6 --per-size 20 --out puzzles

A size is HOUSESxCATEGORIES (e.g. 5x4 = 5 houses, 4 attributes). Up to 16 houses and 6 attributes (16x6).
Each puzzle is saved as puzzles/<size>_<index>.json with the structured clues,
the solution, the seed, and the rendered English prompt. Translating to other
languages later means adding a template set next to RENDER_EN; the structured
clues stay the same, so the same instance can be shown in every language.
"""
import argparse
import json
import random
import time
from pathlib import Path

from z3 import Distinct, Int, Or, Solver, sat

CATS = {
    # the first 6 values of each list are the original ones and the first 8 are the 7-8 house ones.
    # Grids up to 6 houses sample only from the first 6, grids of 7-8 houses from the first 8, and only
    # 9-house grids use the 9th value, so every puzzle generated earlier keeps the same seed and content.
    "name": ["Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry", "Irene", "Jack", "Karen", "Leo",
             "Mia", "Noah", "Olivia", "Paul"],
    "color": ["red", "green", "blue", "yellow", "white", "purple", "orange", "black", "pink", "brown", "gray", "teal",
              "gold", "silver", "maroon", "navy"],
    "pet": ["dog", "cat", "bird", "fish", "horse", "rabbit", "turtle", "hamster", "parrot", "goat", "snake", "mouse",
            "cow", "pig", "duck", "sheep"],
    "drink": ["tea", "coffee", "milk", "juice", "water", "soda", "lemonade", "cider", "cocoa", "smoothie", "kombucha",
              "tonic", "latte", "punch", "ayran", "kefir"],
    "hobby": ["chess", "painting", "gardening", "cooking", "reading", "cycling", "knitting", "fishing", "dancing",
              "sailing", "running", "pottery", "skiing", "hiking", "singing", "weaving"],
    "music": ["jazz", "rock", "pop", "classical", "blues", "folk", "country", "reggae", "metal", "soul", "techno",
              "opera", "disco", "funk", "gospel", "punk"],
}
LABEL = {
    "name": "name",
    "color": "favorite color",
    "pet": "pet",
    "drink": "favorite drink",
    "hobby": "hobby",
    "music": "favorite music genre",
}
# relative frequency of clue types when sampling
WEIGHTS = [
    ("same", 25), ("imm_left", 12), ("next_to", 15), ("left_of", 20),
    ("not_same", 10), ("not_at", 8), ("at", 10),
]


# ---------- logic ----------

def encode(cl, P):
    t = cl[0]
    if t == "at":
        return P[tuple(cl[1])] == cl[2]
    if t == "not_at":
        return P[tuple(cl[1])] != cl[2]
    a, b = P[tuple(cl[1])], P[tuple(cl[2])]
    if t == "same":
        return a == b
    if t == "not_same":
        return a != b
    if t == "left_of":
        return a < b
    if t == "imm_left":
        return a + 1 == b
    if t == "next_to":
        return Or(a + 1 == b, b + 1 == a)
    raise ValueError(t)


def holds(cl, pos):
    t = cl[0]
    if t == "at":
        return pos[tuple(cl[1])] == cl[2]
    if t == "not_at":
        return pos[tuple(cl[1])] != cl[2]
    a, b = pos[tuple(cl[1])], pos[tuple(cl[2])]
    return {
        "same": a == b,
        "not_same": a != b,
        "left_of": a < b,
        "imm_left": a + 1 == b,
        "next_to": abs(a - b) == 1,
    }[t]


def build_solver(cats, n, clues):
    s = Solver()
    P = {}
    for c, vals in cats.items():
        for v in vals:
            P[(c, v)] = Int(f"{c}__{v}")
            s.add(P[(c, v)] >= 1, P[(c, v)] <= n)
        s.add(Distinct([P[(c, v)] for v in vals]))
    for cl in clues:
        s.add(encode(cl, P))
    return s, P


def is_unique(s, P, pos):
    s.push()
    s.add(Or([P[e] != pos[e] for e in P]))
    r = s.check()
    s.pop()
    return r != sat


def random_clue(rng, ents, n, pos):
    t = rng.choices([w[0] for w in WEIGHTS], [w[1] for w in WEIGHTS])[0]
    if t in ("at", "not_at"):
        e = rng.choice(ents)
        if t == "at":
            k = pos[e]
        else:
            k = rng.choice([x for x in range(1, n + 1) if x != pos[e]])
        return [t, list(e), k]
    a, b = rng.sample(ents, 2)
    if t in ("same", "not_same") and a[0] == b[0]:
        return None
    cl = [t, list(a), list(b)]
    return cl if holds(cl, pos) else None


def make_puzzle(n, m, rng, max_clues=500):
    names = list(CATS)[:m]
    cats = {c: rng.sample(CATS[c][:6] if n <= 6 else CATS[c][:8] if n <= 8 else CATS[c][:9] if n <= 9 else CATS[c][:12] if n <= 12 else CATS[c], n)
            for c in names}
    pos = {}
    for c in names:
        perm = list(range(1, n + 1))
        rng.shuffle(perm)
        for v, k in zip(cats[c], perm):
            pos[(c, v)] = k
    ents = list(pos)

    s, P = build_solver(cats, n, [])
    clues, seen = [], set()
    while True:
        if len(clues) >= max_clues:
            raise RuntimeError("could not reach a unique solution")
        cl = random_clue(rng, ents, n, pos)
        if cl is None:
            continue
        key = json.dumps(cl)
        if key in seen:
            continue
        seen.add(key)
        s.add(encode(cl, P))
        clues.append(cl)
        if is_unique(s, P, pos):
            break

    # prune: drop every clue that is not needed for uniqueness
    for cl in list(clues):
        trial = [c for c in clues if c is not cl]
        s2, P2 = build_solver(cats, n, trial)
        if is_unique(s2, P2, pos):
            clues = trial
    rng.shuffle(clues)

    solution = {}
    for c in names:
        by_house = {pos[(c, v)]: v for v in cats[c]}
        solution[c] = [by_house[k] for k in range(1, n + 1)]
    return cats, clues, solution


# ---------- rendering (English) ----------

def ent_en(e):
    c, v = e
    return v if c == "name" else f"the person whose {LABEL[c]} is {v}"


def cap(s):
    return s[0].upper() + s[1:]


def render_clue_en(cl):
    t = cl[0]
    if t == "at":
        return cap(f"{ent_en(cl[1])} lives in house {cl[2]}.")
    if t == "not_at":
        return cap(f"{ent_en(cl[1])} does not live in house {cl[2]}.")
    a, b = ent_en(cl[1]), ent_en(cl[2])
    return cap({
        "same": f"{a} and {b} are the same person.",
        "not_same": f"{a} and {b} are not the same person.",
        "left_of": f"{a} lives somewhere to the left of {b}.",
        "imm_left": f"{a} lives directly to the left of {b}.",
        "next_to": f"{a} and {b} live next to each other.",
    }[t])


def render_prompt_en(n, cats, clues):
    lines = [
        f"There are {n} houses in a row, numbered 1 to {n} from left to right. "
        "Each house has exactly one resident. Every resident has one value for "
        "each attribute below, and no two residents share a value within an attribute.",
        "",
        "Attributes and their possible values:",
    ]
    for c, vals in cats.items():
        lines.append(f"- {c}: {', '.join(vals)}")
    lines += ["", "Clues:"]
    for i, cl in enumerate(clues, 1):
        lines.append(f"{i}. {render_clue_en(cl)}")
    keys = ", ".join(f'"{c}": [...]' for c in cats)
    lines += [
        "",
        "Determine which values belong to each house. Submit your answer as JSON "
        f"with exactly these keys: {{{keys}}}. Each list has {n} entries, "
        f"the value in house 1 first and the value in house {n} last.",
    ]
    return "\n".join(lines)


# ---------- main ----------

def parse_size(s):
    n, m = s.lower().split("x")
    n, m = int(n), int(m)
    if not (2 <= n <= 16 and 2 <= m <= 6):
        raise SystemExit("houses must be 2-16 and attributes 2-6")
    return n, m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", nargs="+", default=["4x4", "5x4", "5x5", "6x5", "6x6"])
    ap.add_argument("--per-size", type=int, default=20)
    ap.add_argument("--out", default="puzzles")
    ap.add_argument("--seed", default="zebra-v1")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for size in a.sizes:
        n, m = parse_size(size)
        for i in range(a.per_size):
            path = out / f"{n}x{m}_{i:02d}.json"
            if path.exists():
                continue
            t0 = time.time()
            rng = random.Random(f"{a.seed}:{n}x{m}:{i}")
            cats, clues, solution = make_puzzle(n, m, rng)
            puzzle = {
                "id": f"{n}x{m}_{i:02d}",
                "n_houses": n,
                "n_cats": m,
                "n_clues": len(clues),
                "seed": f"{a.seed}:{n}x{m}:{i}",
                "categories": cats,
                "clues": clues,
                "solution": solution,
                "prompt": render_prompt_en(n, cats, clues),
            }
            path.write_text(json.dumps(puzzle, ensure_ascii=False, indent=1))
            print(f"{puzzle['id']}: {len(clues)} clues, {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Генерує головоломки у стилі ZebraLogic, єдиність розв'язку яких перевірено за допомогою z3.

Використання:
    python gen_puzzles.py --sizes 4x4 5x5 6x5 6x6 --per-size 20 --out puzzles

Розмір задається як БУДИНКИxКАТЕГОРІЇ (напр. 5x4 = 5 будинків, 4 ознаки). Максимум 16 будинків і 6 ознак (16x6).
Кожна головоломка зберігається як puzzles/<size>_<index>.json зі структурованими підказками,
розв'язком, сідом і відрендереним українським промптом. Щоб пізніше перекласти іншими
мовами, достатньо додати набір шаблонів поруч із RENDER_EN; структуровані підказки
лишаються тими самими, тож той самий екземпляр можна показати будь-якою мовою.
"""
import argparse
import json
import random
import time
from pathlib import Path

from z3 import Distinct, Int, Or, Solver, sat

CATS = {
    # перші 6 значень кожного списку — оригінальні, перші 8 — для сіток на 7-8 будинків.
    # Сітки до 6 будинків беруть значення лише з перших 6, сітки на 7-8 будинків — з перших 8, і лише
    # сітки на 9 будинків використовують 9-те значення, тож кожна раніше згенерована головоломка зберігає той самий сід і вміст.
    "name": ["Олена", "Богдан", "Катерина", "Дмитро", "Єва", "Федір", "Галина", "Григорій", "Ірина", "Ярослав", "Христина", "Левко",
             "Марія", "Назар", "Оксана", "Павло"],
    "color": ["червоний", "зелений", "синій", "жовтий", "білий", "фіолетовий", "помаранчевий", "чорний", "рожевий", "коричневий", "сірий", "бірюзовий",
              "золотий", "срібний", "бордовий", "темно-синій"],
    "pet": ["собака", "кіт", "птах", "риба", "кінь", "кролик", "черепаха", "хом'як", "папуга", "коза", "змія", "миша",
            "корова", "свиня", "качка", "вівця"],
    "drink": ["чай", "кава", "молоко", "сік", "вода", "газування", "лимонад", "сидр", "какао", "смузі", "комбуча",
              "тонік", "лате", "пунш", "айран", "кефір"],
    "hobby": ["шахи", "малювання", "садівництво", "кулінарія", "читання", "велоспорт", "в'язання", "риболовля", "танці",
              "вітрильний спорт", "біг", "гончарство", "лижі", "походи", "спів", "ткацтво"],
    "music": ["джаз", "рок", "поп", "класика", "блюз", "фолк", "кантрі", "реггі", "метал", "соул", "техно",
              "опера", "диско", "фанк", "госпел", "панк"],
}
LABEL = {
    "name": "ім'ям",
    "color": "улюбленим кольором",
    "pet": "домашньою твариною",
    "drink": "улюбленим напоєм",
    "hobby": "хобі",
    "music": "улюбленим музичним жанром",
}
# відносна частота типів підказок під час вибірки
WEIGHTS = [
    ("same", 25), ("imm_left", 12), ("next_to", 15), ("left_of", 20),
    ("not_same", 10), ("not_at", 8), ("at", 10),
]


# ---------- логіка ----------

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
            raise RuntimeError("не вдалося досягти єдиного розв'язку")
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

    # обрізання: прибираємо кожну підказку, яка не потрібна для єдиності
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


# ---------- рендеринг (українська) ----------

def ent_en(e):
    c, v = e
    return v if c == "name" else f"людина з {LABEL[c]} «{v}»"


def cap(s):
    return s[0].upper() + s[1:]


def render_clue_en(cl):
    t = cl[0]
    if t == "at":
        return cap(f"{ent_en(cl[1])} живе в будинку {cl[2]}.")
    if t == "not_at":
        return cap(f"{ent_en(cl[1])} не живе в будинку {cl[2]}.")
    a, b = ent_en(cl[1]), ent_en(cl[2])
    return cap({
        "same": f"{a} і {b} — одна й та сама людина.",
        "not_same": f"{a} і {b} — різні люди.",
        "left_of": f"{a} живе десь лівіше, ніж {b}.",
        "imm_left": f"{a} живе на один будинок лівіше, ніж {b}.",
        "next_to": f"{a} і {b} живуть у сусідніх будинках.",
    }[t])


def render_prompt_en(n, cats, clues):
    lines = [
        f"Є {n} будинків у ряд, пронумерованих від 1 до {n} зліва направо. "
        "У кожному будинку живе рівно один мешканець. Кожен мешканець має по одному значенню "
        "кожної з наведених нижче ознак, і жодні два мешканці не мають однакового значення в межах однієї ознаки.",
        "",
        "Ознаки та їхні можливі значення:",
    ]
    for c, vals in cats.items():
        lines.append(f"- {c}: {', '.join(vals)}")
    lines += ["", "Підказки:"]
    for i, cl in enumerate(clues, 1):
        lines.append(f"{i}. {render_clue_en(cl)}")
    keys = ", ".join(f'"{c}": [...]' for c in cats)
    lines += [
        "",
        "Визначте, які значення належать кожному будинку. Надішліть відповідь у форматі JSON "
        f"рівно з такими ключами: {{{keys}}}. Довжина кожного списку — {n}: "
        f"першим іде значення для будинку 1, останнім — для будинку {n}.",
    ]
    return "\n".join(lines)


# ---------- main ----------

def parse_size(s):
    n, m = s.lower().split("x")
    n, m = int(n), int(m)
    if not (2 <= n <= 16 and 2 <= m <= 6):
        raise SystemExit("будинків має бути 2-16, ознак — 2-6")
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
            print(f"{puzzle['id']}: підказок: {len(clues)}, {time.time() - t0:.1f} с", flush=True)


if __name__ == "__main__":
    main()

"""
check_book_prices.py - A Kovács-féle Ultikodex díjazási táblázata (85-88. oldal) adatként, belső ellenőrzéssel,
és összevetés a rules.py (a barát UI-jának) díjaival.

A táblázat sorait a nyomtatott oldalképekről írtuk át (az OCR az oszlopokat összekeveri). Az átírás helyességét az
ellenőrzi, hogy MINDEN sor egysége kijön a komponensek összegéből (piros = kétszeres): ha egy sort elírtunk
vagy félreolvastunk, az összeg nem egyezik.
Komponensek: parti, c40 (40-100), c20 (20-100), ulti, asz4 (négy ász), durch (durchmars), tdurch (terített durchmars).
"""
from __future__ import annotations

from ulti.paths import RULES
import json
from pathlib import Path
from typing import Dict, List, Tuple

from rules import CONTRACTS

# (egység, [(piros?, [komponensek]), ...]) - a könyv sorai
ROWS: List[Tuple[int, List[Tuple[bool, List[str]]]]] = [
    (1, [(False, ["parti"])]),
    (2, [(True, ["parti"])]),
    (4, [(False, ["asz4"]), (False, ["c40"]), (False, ["ulti"])]),
    (6, [(False, ["c20"])]),
    (7, [(False, ["durch"])]),
    (8, [(False, ["c40", "ulti"]), (False, ["asz4", "ulti"]), (False, ["asz4", "c40"]),
         (True, ["asz4"]), (True, ["c40"]), (True, ["ulti"])]),
    (10, [(False, ["c20", "asz4"]), (False, ["c20", "ulti"])]),
    (11, [(False, ["durch", "asz4"]), (False, ["durch", "c40"]), (False, ["durch", "ulti"])]),
    (12, [(False, ["asz4", "c40", "ulti"])]),
    (13, [(False, ["durch", "c20"])]),
    (14, [(False, ["tdurch"]), (True, ["durch"]), (False, ["c20", "asz4", "ulti"])]),
    (15, [(False, ["durch", "c40", "ulti"]), (False, ["durch", "asz4", "ulti"]), (False, ["durch", "asz4", "c40"])]),
    (16, [(True, ["asz4", "c40"]), (True, ["c40", "ulti"]), (True, ["asz4", "ulti"])]),
    (17, [(False, ["durch", "c20", "ulti"]), (False, ["durch", "c20", "asz4"])]),
    (18, [(False, ["tdurch", "asz4"]), (False, ["tdurch", "c40"]), (False, ["tdurch", "ulti"])]),
    (20, [(False, ["tdurch", "c20"])]),
    (22, [(False, ["tdurch", "c40", "ulti"]), (False, ["tdurch", "asz4", "c40"]), (False, ["tdurch", "asz4", "ulti"])]),
    (24, [(True, ["asz4", "c40", "ulti"])]),
    (26, [(True, ["durch", "c20"])]),
    (28, [(True, ["tdurch"])]),
    (30, [(True, ["durch", "asz4", "c40"]), (True, ["durch", "asz4", "ulti"]), (True, ["durch", "c40", "ulti"])]),
    (36, [(True, ["tdurch", "asz4"]), (True, ["tdurch", "c40"]), (True, ["tdurch", "ulti"])]),
    (40, [(True, ["tdurch", "c20"])]),
    (44, [(True, ["tdurch", "asz4", "c40"]), (True, ["tdurch", "asz4", "ulti"]), (True, ["tdurch", "c40", "ulti"])]),
    (48, [(True, ["tdurch", "c20", "ulti"]), (True, ["tdurch", "asz4", "c20"])]),
    (52, [(True, ["tdurch", "asz4", "c40", "ulti"])]),
    (56, [(True, ["tdurch", "c20", "asz4", "ulti"])]),
]
# külön álló sorok: betli 5, piros betli 10, terített betli 20 (a könyv 85., 86., 87. oldala; a 89. oldal: "A piros betli 10 pont,
# a terített betli bármelyik színben 20 pont")
BETLI = {"betli": 5, "piros betli": 10, "terített betli": 20}

# a komponensek értéke, amit a sorokból SZÁMOLUNK vissza (nem előre feltételezzük)
SOLVE_ORDER = ["parti", "c40", "ulti", "asz4", "c20", "durch", "tdurch"]


def solve() -> Dict[str, int]:
    """Az egy-komponensű (nem piros) sorokból az alapértékek, majd az összes sor ellenőrzése."""
    vals: Dict[str, int] = {}
    for unit, figs in ROWS:
        for red, comps in figs:
            if len(comps) == 1 and not red:
                vals[comps[0]] = unit
    # a 'durchmars' és 'terített durchmars' külön sorban (7 és 14) áll
    return vals


def check() -> Tuple[Dict[str, int], List[str]]:
    vals = solve()
    bad: List[str] = []
    for unit, figs in ROWS:
        for red, comps in figs:
            miss = [c for c in comps if c not in vals]
            if miss:
                bad.append(f"{unit}: ismeretlen érték {miss}")
                continue
            s = sum(vals[c] for c in comps) * (2 if red else 1)
            if s != unit:
                bad.append(f"{unit}: {'piros ' if red else ''}{'+'.join(comps)} = {s}, a könyv szerint {unit}")
    return vals, bad


def rules_vs_book(vals: Dict[str, int]) -> List[str]:
    """A rules.CONTRACTS (a barát táblázata) komponensenkénti eltérése a könyvtől."""
    diffs: Dict[str, set] = {}
    for c in CONTRACTS:
        if c["kind"] == "betli":
            continue
        mult = 2 if c["red"] else 1
        for k, v in c["comps"].items():
            key = "tdurch" if (k == "durch" and c["open"]) else k
            if key not in vals:
                continue
            exp = vals[key] * mult
            if v != exp:
                diffs.setdefault(key, set()).add((c["name"], v, exp))
    out = []
    for k, rows in sorted(diffs.items()):
        ex = sorted(rows)[:3]
        out.append(f"{k}: a rules.py és a könyv eltér {len(rows)} vállalásnál, pl. " + "; ".join(f"{n}: rules {a} / könyv {b}" for n, a, b in ex))
    return out


if __name__ == "__main__":
    vals, bad = check()
    n_fig = sum(len(f) for _, f in ROWS)
    print(f"komponens-értékek (egységben): {vals}")
    print(f"{len(ROWS)} sor, {n_fig} figura ellenőrizve: {'MIND összhangban van' if not bad else str(len(bad)) + ' eltérés'}")
    for b in bad:
        print("  !", b)
    print("betli:", BETLI)
    print("\nA rules.py (barát) és a könyv különbségei:")
    for d in rules_vs_book(vals):
        print("  -", d)
    missing = [k for k in ("asz4",) if all(k not in c["comps"] for c in CONTRACTS)]
    if missing:
        print("  - a 'négy ász' bemondás (a könyvben 4 egység) a rules.py 42 vállalása között NEM szerepel")
    RULES / "book_prices.json".write_text(
        json.dumps({"components": vals, "red_multiplier": 2, "betli": BETLI,
                    "rows": [{"unit": u, "figures": [{"red": r, "comps": c} for r, c in f]} for u, f in ROWS],
                    "source": "Kovács Endre: Ultikodex, 85-88. oldal (átírva az oldalképről)"},
                   ensure_ascii=False, indent=1), encoding="utf-8")

"""
book_bids.py - "Mire mit mondanak be": a motor licit-döntései a Kovács-könyv példáin, a könyv döntéseivel összevetve.

A könyv példáiból (replay.json) minden licit-lépésre megvan a lépés előtti kéz (a felvett talonnal együtt), a könyv
bemondása (vállalás, adu) és a letett két lap. Itt azt mérjük, mit tenne a tanult politika ugyanabban a helyzetben:
  - ugyanazt a vállalást és adut választja-e (pontos egyezés),
  - ugyanazt az adut / a vállalás ugyanazt a figura-készletét (rész-egyezés),
  - a könyv bemondását mennyire értékeli a sajátjához képest (értékkülönbség, a politika saját pénz-becslésében),
  - ugyanazt a két lapot teszi-e le.
Csak az első felvétel számít (aki már rabolt, annak a keze a korábbi lerakás miatt eltér). A könyv nem tévedhetetlen,
ezért ez nem hiba-mérő, hanem egy második nézőpont: ahol a motor és a könyv eltér, ott érdemes megnézni, melyik jár jobban.

Használat: python book_bids.py [--policy learned_book.json]
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE, POLICY
import argparse
import json
from pathlib import Path
from typing import Dict, List

import rules

rules.set_ruleset("book")

from policy import LearnedPolicy  # noqa: E402  (a szabályrendszer beállítása után)
from rules import CONTRACTS  # noqa: E402

ROOT = Path(__file__).parent


def bid_cases(replay: Dict[str, dict]) -> List[dict]:
    """A (kéz+talon, könyv-bemondás, könyv-lerakás) esetek az összes használható példából."""
    cases: List[dict] = []
    for n in sorted(replay, key=int):
        r = replay[n]
        if not r.get("steps") or not r.get("initial") or not r.get("talon_rows"):
            continue
        rows = r["talon_rows"]
        seen = set()
        for s, st in enumerate(r["steps"]):
            nm = st["player"]
            if nm in seen or nm not in r["initial"] or s + 1 >= len(rows):
                continue
            seen.add(nm)
            trump = {"P": 0, "T": 1, "Z": 2, "M": 3}.get(st["trump"], st["trump"]) if isinstance(st["trump"], str) else st["trump"]
            cases.append({"example": int(n), "step": s, "player": nm, "cards": sorted(r["initial"][nm] + rows[s]),
                          "book_ci": st["ci"], "book_trump": trump, "book_disc": sorted(rows[s + 1]),
                          "min_ci": 0 if s == 0 else r["steps"][s - 1]["ci"] + 1})
    return cases


def compare(pol: LearnedPolicy, cases: List[dict]) -> dict:
    out = []
    for c in cases:
        cur = rules.CONTRACTS[c["book_ci"]]
        # a rablónak csak a "nem alacsonyabb" vállalások jöhetnek (a licit-lépcső); a rules.min_bid_index a vállalás értékéből számol
        lo = next((i for i, x in enumerate(CONTRACTS) if x["val"] > CONTRACTS[c["min_ci"] - 1]["val"]), len(CONTRACTS)) if c["min_ci"] else 0
        best = pol.best_option(c["cards"], lo, True)
        if best is None:
            continue
        ev, ci, su, disc = best
        trump_book = c["book_trump"] if cur["kind"] == "trump" else -1
        rest = [x for x in c["cards"] if x not in c["book_disc"]]
        ev_book = pol.hand_value(rest, c["book_ci"], trump_book, tuple(c["book_disc"]))
        mine = rules.CONTRACTS[ci]
        out.append({"example": c["example"], "step": c["step"], "player": c["player"],
                    "book": cur["name"], "book_trump": trump_book, "engine": mine["name"], "engine_trump": su,
                    "exact": ci == c["book_ci"] and su == trump_book,
                    "same_trump": su == trump_book,
                    "same_comps": set(mine["comps"]) == set(cur["comps"]) and mine["red"] == cur["red"],
                    "same_disc": sorted(disc) == c["book_disc"],
                    "ev_engine": round(ev, 2), "ev_book": round(ev_book, 2), "gap": round(ev - ev_book, 2)})
    return {"cases": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default=str(POLICY / "learned_book.json"))
    ap.add_argument("--out", default=str(KNOWLEDGE / "book_bids.json"))
    a = ap.parse_args()
    replay = json.loads((KNOWLEDGE / "replay.json").read_text(encoding="utf-8"))
    cases = bid_cases(replay)
    pol = LearnedPolicy.load(Path(a.policy))
    res = compare(pol, cases)["cases"]
    n = len(res)
    if not n:
        raise SystemExit("nincs összevethető licit-lépés")
    f = lambda k: sum(1 for r in res if r[k]) / n
    print(f"{n} licit-lépés a könyv példáiból ({len({r['example'] for r in res})} példa)")
    print(f"  pontos egyezés (vállalás + adu): {f('exact'):.0%}   ugyanaz az adu: {f('same_trump'):.0%}   "
          f"ugyanazok a figurák: {f('same_comps'):.0%}   ugyanaz a talonletétel: {f('same_disc'):.0%}")
    gaps = sorted(r["gap"] for r in res)
    print(f"  a motor szerinti érték-különbség (motor választása - a könyv bemondása): medián {gaps[n // 2]:+.2f}, "
          f"átlag {sum(gaps) / n:+.2f}; a könyv bemondását a motor is legalább 'majdnem' a legjobbnak tartja (különbség <= 2): "
          f"{sum(1 for g in gaps if g <= 2.0) / n:.0%}")
    for r in res:
        mark = "=" if r["exact"] else ("~" if r["same_trump"] else "!")
        print(f"  {mark} [{r['example']:2d}.{r['step']}] {r['player']:6s} könyv: {r['book']:28s} motor: {r['engine']:28s} "
              f"(érték motor {r['ev_engine']:+.1f} / könyv {r['ev_book']:+.1f})")
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

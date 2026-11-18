"""
book_eval.py - Szimulációs kiértékelés: ahol a motor és a Kovács-könyv licit-bemondása ELTÉR, melyik ad több pénzt?

A book_bids.py a könyv példáin (replay.json) összeveti a tanult politika (learned_book.json, "book" szabályrendszer =
a könyv díjtáblája) bemondását a könyvével, de csak a politika saját pénzbecslésével. Itt minden eltérő esetre
(vállalás, adu vagy talonletétel különbözik) szimuláljuk mindkét változatot:
  - a döntő játékos 12 lapos keze (felvett talonnal) a könyvből ismert; a másik két kéz a maradék 20 lapból
    véletlenül osztva (N világ), a licit-lépcső (min_ci) a replay-ből jön, érvénytelen bemondást kihagyunk;
  - mindkét változat (könyv: vállalás+adu+lerakás, motor: ugyanez a motor szerint) UGYANAZON a világon és
    véletlen-magon fut (páros), a bemondás után a többi licitet, kontrát és kijátszást a tanult politika játssza
    mindhárom ülésen (tutor.simulate -> arena.run_deal);
  - mért érték: a döntő játékos pénze (result["change"][p]); páros különbség (motor - könyv) és a 95%-os CI fele.
Eredmény: book_eval.json (esetenként + összegzés). Részeredmények mentődnek, a futás adagolható és folytatható.

Használat:
  python book_eval.py [--n 250] [--workers 4] [--seed 1] [--out book_eval.json]
                      [--offset 0 --limit 8] [--only 12.0,30.1] [--summary]
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE, RESEARCH, POLICY
import argparse
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import rules

rules.set_ruleset("book")

import tutor  # noqa: E402  (a szabályrendszer beállítása után)
from book_bids import bid_cases  # noqa: E402
from policy import LearnedPolicy  # noqa: E402
from rules import CONTRACTS, DECK, Game  # noqa: E402

ROOT = Path(__file__).parent
DEALER = 2                      # az ülések a replay "order" sorrendjét követik: a 0. ülés az első kéz, a 2. az osztó
Variant = Tuple[int, int, List[int]]        # (vállalás, adu, talonletétel)


def load_cases(replay: Dict[str, dict]) -> List[dict]:
    """A book_bids.bid_cases esetei + a döntő és az előző licitáló ülése (a replay "order"/"steps" mezőiből)."""
    out: List[dict] = []
    for c in bid_cases(replay):
        r = replay[str(c["example"])]
        order = r.get("order") or []
        seat = order.index(c["player"]) if c["player"] in order else 0
        prev = r["steps"][c["step"] - 1]["player"] if c["step"] else None
        prev_seat = order.index(prev) if prev in order and prev != c["player"] else (seat + 1) % 3
        out.append(dict(c, seat=seat, prev_seat=prev_seat))
    return out


def case_key(c: dict) -> str:
    return f"{c['example']}.{c['step']}"


def make_world(case: dict, rng: random.Random) -> Game:
    """Licit-állapot a döntő ülésén: 12 lapja ismert, a másik két kéz véletlen 10-10 lap a maradékból."""
    p = case["seat"]
    pool = [c for c in DECK if c not in set(case["cards"])]
    rng.shuffle(pool)
    hands: List[List[int]] = [[], [], []]
    hands[p] = list(case["cards"])
    k = 0
    for q in range(3):
        if q != p:
            hands[q] = pool[k:k + 10]
            k += 10
    g = Game.from_hands(DEALER, hands)
    g.turn = p
    if case["min_ci"]:
        g.bid = (case["min_ci"] - 1, -1, case["prev_seat"])      # a licit-lépcső: csak ennél magasabb vállalás jöhet
    return g


def variants(pol: LearnedPolicy, case: dict, g: Game) -> Tuple[Variant, Optional[Variant]]:
    """(könyv, motor) bemondás; a motor None, ha nincs érvényes licitje."""
    book = (case["book_ci"], case["book_trump"], list(case["book_disc"]))
    best = pol.best_option(case["cards"], g.min_bid_index(), True)
    return book, (None if best is None else (best[1], best[2], list(best[3])))


def differs(a: Variant, b: Variant) -> bool:
    return (a[0], a[1]) != (b[0], b[1]) or set(a[2]) != set(b[2])


def evaluate_case(pol: LearnedPolicy, case: dict, n: int = 250, seed: int = 1, workers: int = 0) -> dict:
    """Egy eset páros szimulációja n világgal. Visszaad: status ("ok" | "same" | "invalid"), a két bemondás és
    (ok esetén) a pénz-átlagok, a páros különbség (motor - könyv), a CI fele (1.96*se) és az ítélet."""
    g0 = make_world(case, random.Random(0))
    book, eng = variants(pol, case, g0)
    base = {"example": case["example"], "step": case["step"], "player": case["player"],
            "book": CONTRACTS[book[0]]["name"], "book_trump": book[1], "book_disc": sorted(book[2]),
            "engine": None, "engine_trump": None, "engine_disc": None}
    if eng is None:
        return dict(base, status="invalid", reason="a motornak nincs érvényes licitje")
    base.update(engine=CONTRACTS[eng[0]]["name"], engine_trump=eng[1], engine_disc=sorted(eng[2]),
                kind="bid" if (book[0], book[1]) != (eng[0], eng[1]) else "disc")
    if not differs(book, eng):
        return dict(base, status="same")
    err = g0.validate_bid(case["seat"], book[2], book[0], book[1]) or g0.validate_bid(case["seat"], eng[2], eng[0], eng[1])
    if err:
        return dict(base, status="invalid", reason=err)
    rng = random.Random(seed * 1000003 + case["example"] * 101 + case["step"])
    worlds = [make_world(case, rng) for _ in range(n)]
    seeds = [rng.randrange(1 << 30) for _ in worlds]
    jobs: List[Game] = []
    for v in (book, eng):
        for w in worlds:
            x = w.clone()
            x.do_bid(case["seat"], list(v[2]), v[0], v[1])
            jobs.append(x)
    flat = tutor.money(tutor.simulate(jobs, case["seat"], seeds * 2, workers))
    mb, me = flat[:n], flat[n:]
    m, se = tutor.paired(mb, me)
    half = 1.96 * se
    verdict = "motor" if m > half else ("könyv" if m < -half else "nem eldönthető")
    return dict(base, status="ok", n=n, book_mean=round(sum(mb) / n, 2), engine_mean=round(sum(me) / n, 2),
                diff=round(m, 2), ci_half=round(half, 2), verdict=verdict)


def summarize(results: Sequence[dict]) -> dict:
    ok = [r for r in results if r["status"] == "ok"]
    cnt = {v: sum(1 for r in ok if r["verdict"] == v) for v in ("motor", "könyv", "nem eldönthető")}
    sig = sorted((r for r in ok if r["verdict"] != "nem eldönthető"), key=lambda r: -abs(r["diff"]))
    top = sorted(ok, key=lambda r: -abs(r["diff"]))[:5]
    by_kind = {k: {"n": sum(1 for r in ok if r["kind"] == k), "mean_diff": round(
        sum(r["diff"] for r in ok if r["kind"] == k) / max(1, sum(1 for r in ok if r["kind"] == k)), 2)} for k in ("bid", "disc")}
    return {"ruleset": rules.RULESET, "cases": len(results), "same": sum(1 for r in results if r["status"] == "same"),
            "invalid": sum(1 for r in results if r["status"] == "invalid"), "evaluated": len(ok), "verdicts": cnt,
            "mean_diff": round(sum(r["diff"] for r in ok) / len(ok), 2) if ok else 0.0,
            "mean_diff_significant": round(sum(r["diff"] for r in sig) / len(sig), 2) if sig else 0.0,
            "by_kind": by_kind, "significant": [case_key(r) for r in sig], "top5": [case_key(r) for r in top]}


def _load(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    return {case_key(r): r for r in json.loads(path.read_text("utf-8")).get("cases", [])}


def _save(path: Path, done: Dict[str, dict]) -> None:
    res = sorted(done.values(), key=lambda r: (r["example"], r["step"]))
    path.write_text(json.dumps({"summary": summarize(res), "cases": res}, ensure_ascii=False, indent=1), encoding="utf-8")


def _print(res: Sequence[dict]) -> None:
    s = summarize(res)
    print(f"{s['cases']} eset: {s['same']} azonos, {s['invalid']} kihagyva, {s['evaluated']} szimulálva; "
          f"motor jobb {s['verdicts']['motor']}, könyv jobb {s['verdicts']['könyv']}, nem eldönthető {s['verdicts']['nem eldönthető']}; "
          f"átlag különbség (motor-könyv) {s['mean_diff']:+.2f}, szignifikánsaké {s['mean_diff_significant']:+.2f}")
    for r in sorted((r for r in res if r["status"] == "ok"), key=lambda r: -abs(r["diff"]))[:5]:
        print(f"  [{case_key(r)}] könyv: {r['book']} ({r['book_trump']}) {r['book_mean']:+.1f} | motor: {r['engine']} "
              f"({r['engine_trump']}) {r['engine_mean']:+.1f} | diff {r['diff']:+.2f} ± {r['ci_half']:.2f} -> {r['verdict']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default=str(POLICY / "learned_book.json"))
    ap.add_argument("--out", default=str(RESEARCH / "book_eval.json"))
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--summary", action="store_true", help="csak az eddigi részeredmények összegzése")
    a = ap.parse_args()
    out = Path(a.out)
    done = _load(out)
    if not a.summary:
        pol = LearnedPolicy.load(Path(a.policy))
        cases = load_cases(json.loads((KNOWLEDGE / "replay.json").read_text(encoding="utf-8")))
        if a.only:
            want = set(a.only.split(","))
            cases = [c for c in cases if case_key(c) in want]
        cases = cases[a.offset:a.offset + a.limit] if a.limit else cases[a.offset:]
        for c in cases:
            t = time.time()
            r = evaluate_case(pol, c, a.n, a.seed, a.workers)
            done[case_key(c)] = r
            _save(out, done)
            print(f"[{case_key(c)}] {r['status']} {r.get('diff', '')} {r.get('verdict', '')} ({time.time() - t:.0f}s)", flush=True)
    _print(sorted(done.values(), key=lambda r: (r["example"], r["step"])))


if __name__ == "__main__":
    main()

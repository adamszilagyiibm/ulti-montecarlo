"""
arena.py - Mérőpad: egy teljes leosztás (licit, talon, kontra, kijátszás, pénz) politikákkal.

A mérés PÁROS: ugyanaz a leosztás, ugyanaz az ülés, ugyanaz a döntési véletlen. Egyedül a vizsgált
politika különbözik, így a pénzkülönbség szórása töredéke a nyers pénzének.

Politika (protocol):
    bid_action(g, p, rng) -> {"type": "bid"|"pickup"|"pass"|"concede", ...}
    kontra(g, p, rng)     -> [komponens kulcsok]
    card(g, p, rng)       -> lap
"""
from __future__ import annotations

import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import jsbot
from playbot import bot_choose
from rules import Game


class JsPolicy:
    """A barát JS botja (licit, kontra, kijátszás) - ez a mérce."""
    name = "js"

    def bid_action(self, g: Game, p: int, rng: random.Random) -> dict:
        return jsbot.bot_bid_action(g, p, rng)

    def kontra(self, g: Game, p: int, rng: random.Random) -> List[str]:
        return jsbot.bot_kontra(g, p)

    def card(self, g: Game, p: int, rng: random.Random) -> int:
        return bot_choose(g, p)


def run_deal(g: Game, pols: Sequence[Any], rng: random.Random) -> Game:
    """A g állapotától (licit, kontra vagy kijátszás közben) a végéig játszik. A g helyben módosul."""
    guard = 0
    while g.phase == "bid" and guard < 80:
        guard += 1
        p = g.turn
        a = pols[p].bid_action(g, p, rng)
        t = a["type"]
        if t == "bid":
            err = g.validate_bid(p, a["disc"], a["ci"], a["suit"])
            if err:
                raise ValueError(f"{pols[p].name}: érvénytelen licit ({err})")
            g.do_bid(p, a["disc"], a["ci"], a["suit"])
        elif t == "pickup":
            g.do_pickup(p)
        elif t == "concede":
            g.do_concede(p)
            return g
        else:
            g.do_pass(p)
    guard = 0
    while g.phase == "kontra" and guard < 10:
        guard += 1
        p = g.turn
        g.do_kontra(p, pols[p].kontra(g, p, rng))
    while g.phase == "play":
        if len(g.trick) == 3:
            g.collect_trick()
            continue
        p = g.turn
        c = pols[p].card(g, p, rng)
        if c not in g.legal_cards(p):
            raise ValueError(f"{pols[p].name}: szabálytalan lap")
        g.play_card(p, c)
    return g


def play_deal(dealer: int, deal_seed: int, pols: Sequence[Any]) -> Optional[Game]:
    """Lejátszik egy leosztást. pols[p] az p. ülés politikája. None, ha nem lett játék (nem fordul elő)."""
    g = Game.new_deal(dealer, random.Random(deal_seed))
    return run_deal(g, pols, random.Random(deal_seed * 7919 + 13))


@dataclass
class Stat:
    n: int
    mean: float
    se: float

    def __str__(self) -> str:
        return f"{self.mean:+.3f} ± {1.96 * self.se:.3f}"


def _stat(xs: Sequence[float]) -> Stat:
    n = len(xs)
    if n == 0:
        return Stat(0, 0.0, 0.0)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / max(1, n - 1)
    return Stat(n, m, (var / n) ** 0.5)


def _one(args) -> Dict[str, Any]:
    maker, seat, deal_seed, base_maker = args
    dealer = deal_seed % 3
    base = [base_maker(), base_maker(), base_maker()]
    test = list(base)
    test[seat] = maker()
    gb = play_deal(dealer, deal_seed, base)
    gt = play_deal(dealer, deal_seed, test)
    return {
        "seat": seat, "base": gb.result["change"][seat], "test": gt.result["change"][seat],
        "decl_test": gt.decl == seat, "decl_base": gb.decl == seat,
        "conceded_test": bool(gt.result.get("conceded")),
    }


def evaluate(maker: Callable[[], Any], deals: int = 600, seed0: int = 1000, workers: int = 8,
             base_maker: Callable[[], Any] = None) -> Dict[str, Any]:
    """A vizsgált politika pénze (3 ülésen, párosítva az alap-futással). maker, base_maker: top-level callable (pickle).
    Az alap (a másik két ülés és a viszonyítás) alapesetben a JS bot."""
    bm = base_maker or make_js
    jobs = [(maker, s, seed0 + i, bm) for i in range(deals) for s in range(3)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_one, jobs, chunksize=max(1, len(jobs) // (workers * 8))))
    diff = [r["test"] - r["base"] for r in rows]
    return {
        "n": len(rows),
        "test_money": _stat([r["test"] for r in rows]),
        "base_money": _stat([r["base"] for r in rows]),
        "paired_diff": _stat(diff),
        "declares_test": sum(r["decl_test"] for r in rows) / len(rows),
        "declares_base": sum(r["decl_base"] for r in rows) / len(rows),
        "concede_rate_test": sum(r["conceded_test"] for r in rows) / len(rows),
    }


def make_js() -> JsPolicy:
    return JsPolicy()


if __name__ == "__main__":
    import time
    t = time.time()
    r = evaluate(make_js, deals=60)
    print(r, round(time.time() - t, 1), "s")

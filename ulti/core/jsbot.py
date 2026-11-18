"""
jsbot.py - A barát JS botjának licit/talon/kontra logikája (ulti.html), pontos Python-portja.

Két szerepe van: (1) mérce és ellenfél a tanuláshoz/méréshez, (2) a UI-ban ülő gépek ilyenek,
ellenük kell jobbnak lennie a tanult motornak. A test_jsbot.py a JS-sel veti össze.
"""
from __future__ import annotations

import math
import random
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from rules import (CONTRACTS, Game, check_req, concede_loss, has20_for, has40, has_card, is_trump_contract, pts,
                   rank, suit, suits_for, TP)

COMP_ORDER = ("parti", "c40", "c20", "ulti", "betli", "durch")


def sig(x: float) -> float:
    if x < -60:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def of_suit(h: List[int], s: int) -> List[int]:
    lo = s << 3
    return [x for x in h if lo <= x < lo + 8]


def hand_facts(h: List[int], t: int) -> dict:
    return _hand_facts_c(tuple(sorted(h)), t)


@lru_cache(maxsize=400000)
def _hand_facts_c(h: Tuple[int, ...], t: int) -> dict:
    tr = of_suit(h, t) if t >= 0 else []
    tp = sorted((TP[rank(x)] for x in tr), reverse=True)
    run = 0
    while run < len(tp) and tp[run] == 7 - run:
        run += 1
    ut = side_ut = side_aces = voids = long_side = 0
    for s in range(4):
        cs = of_suit(h, s)
        a, x = has_card(h, s, 7), has_card(h, s, 3)
        u = (1 if a else 0) + (1 if (x and a) else 0)
        ut += u
        if s != t:
            side_ut += u
            side_aces += 1 if a else 0
            if not cs:
                voids += 1
            if a:
                long_side = max(long_side, len(cs))
    marrs = [s for s in range(4) if has_card(h, s, 6) and has_card(h, s, 5)]
    return {"n": len(tr), "run": run, "hasA": t >= 0 and has_card(h, t, 7), "hasX": t >= 0 and has_card(h, t, 3),
            "ut": ut, "sideUt": side_ut, "sideAces": side_aces, "voids": voids, "longSide": long_side, "marrs": marrs}


def betli_risk(h: List[int], open_: bool) -> int:
    risk = 0
    for s in range(4):
        rs = sorted(rank(x) for x in of_suit(h, s))
        for i, r in enumerate(rs):
            if r > (max(0, 2 * i - 1) if open_ else 2 * i):
                risk += 1
    return risk


def _durch_closed_longest(h: List[int]) -> Tuple[bool, int]:
    closed, longest = True, 0
    for s in range(4):
        rs = sorted((rank(x) for x in of_suit(h, s)), reverse=True)
        if not rs:
            continue
        r = 0
        while r < len(rs) and rs[r] == 7 - r:
            r += 1
        if r < len(rs):
            closed = False
        longest = max(longest, len(rs))
    return closed, longest


def book_comp_ok(h: List[int], ci: int, trump: int, k: str) -> bool:
    c = CONTRACTS[ci]
    return _book_c(tuple(sorted(h)), trump, k, c["open"], c["kind"] == "trump")


@lru_cache(maxsize=400000)
def _book_c(h: Tuple[int, ...], trump: int, k: str, open_: bool, trump_c: bool) -> bool:
    if k == "betli":
        high = sum(1 for x in h if rank(x) >= 5)
        return high <= 1 and betli_risk(h, open_) == 0
    if k == "durch" and not trump_c:
        closed, longest = _durch_closed_longest(h)
        return closed and longest >= 4
    f = hand_facts(h, trump)
    if k == "parti":
        return f["n"] >= 4 and f["ut"] >= 2
    if k == "c40":
        return (f["n"] >= 4 and f["hasA"] and f["hasX"] and has40(h, trump) and f["sideUt"] >= 2 and f["voids"] >= 1)
    if k == "c20":
        ms = [s for s in f["marrs"] if s != trump]
        return (has20_for(h, trump) and f["run"] >= 4 and f["sideAces"] >= 1
                and any(has_card(h, s, 3) and has_card(h, s, 7) for s in ms))
    if k == "ulti":
        return (f["n"] >= 4 and f["ut"] >= 2) or (f["n"] == 3 and f["longSide"] >= 3 and f["ut"] >= 3)
    rest_closed, _ = _durch_closed_longest([x for x in h if suit(x) != trump])
    return f["run"] >= 4 and rest_closed


def book_check_ok(h: List[int], ci: int, trump: int) -> bool:
    return all(book_comp_ok(h, ci, trump, k) for k in CONTRACTS[ci]["comps"])


def trump_stats(h: List[int], t: int) -> dict:
    return _trump_stats_c(tuple(sorted(h)), t)


@lru_cache(maxsize=400000)
def _trump_stats_c(h: Tuple[int, ...], t: int) -> dict:
    tr = sorted((TP[rank(x)] for x in of_suit(h, t)), reverse=True)
    n = len(tr)
    run = 0
    while run < n and tr[run] == 7 - run:
        run += 1
    tt = run + max(0.0, (n - run) - (8 - n) * 0.6)
    side = 0.0
    voids = singles = aces = 0
    for s in range(4):
        if s == t:
            continue
        cs = of_suit(h, s)
        A, X, K = has_card(h, s, 7), has_card(h, s, 3), has_card(h, s, 6)
        if not cs:
            voids += 1
        elif len(cs) == 1:
            singles += 1
        if A:
            side += 1
            aces += 1
        if A and X:
            side += 0.9
        elif X and len(cs) >= 2:
            side += 0.35
        if A and X and K:
            side += 0.4
    spare = n - 3
    ruff = min(spare, voids * 1.2 + singles * 0.5) * 0.7 if spare > 0 else 0.0
    top3 = (1 if has_card(h, t, 7) else 0) + (1 if has_card(h, t, 3) else 0) + (1 if has_card(h, t, 6) else 0)
    marr, off = 0, False
    for s in range(4):
        if has_card(h, s, 6) and has_card(h, s, 5):
            marr += 40 if s == t else 20
            if s != t:
                off = True
    return {"n": n, "S": tt + side + ruff, "top3": top3, "aces": aces, "marr": marr,
            "trumpMarr": has_card(h, t, 6) and has_card(h, t, 5), "offMarr": off, "has7": has_card(h, t, 0)}


def p_betli(h: List[int], open_: bool) -> float:
    risk = betli_risk(h, open_)
    if open_:
        return 0.02 if risk else 0.8
    return [0.72, 0.2, 0.03][risk] if risk < 3 else 0.01


def p_durch(h: List[int], open_: bool) -> float:
    L = 0
    for s in range(4):
        rs = sorted((rank(x) for x in of_suit(h, s)), reverse=True)
        n = len(rs)
        if not n:
            continue
        run = 0
        while run < n and rs[run] == 7 - run:
            run += 1
        if run < n and run < 8 - n:
            L += n - run
    if open_:
        return 0.0 if L else 0.9
    return 0.9 if L == 0 else (0.12 if L == 1 else 0.0)


def comp_p0(hand: List[int], ci: int, trump: int, k: str, pen: float = 0.0) -> float:
    c = CONTRACTS[ci]
    if k == "betli":
        return p_betli(hand, c["open"])
    if k == "durch":
        if not is_trump_contract(ci):
            return p_durch(hand, c["open"])
        st = trump_stats(hand, trump)
        return sig(2 * (st["S"] - 9.2)) * p_durch([x for x in hand if suit(x) != trump], False) / 0.9 if st["n"] >= 4 else 0.0
    if k == "asz4":      # a barát UI-jának nincs 4 ász bemondása (csak a könyv-szabályrendszerben létezik): a 4 ász a kézben kell
        aces = sum(1 for x in hand if rank(x) == 7)
        return 0.8 if aces == 4 else (0.1 if aces == 3 else 0.0)
    st = trump_stats(hand, trump)
    if k == "parti":
        return sig(1.5 * (st["S"] - 4.3) + (st["marr"] - 20) / 35 - pen)
    if k == "c40":
        return sig(1.7 * (st["S"] - 6.0) - pen) if st["trumpMarr"] else 0.0
    if k == "c20":
        return sig(1.7 * (st["S"] - 7.5) - pen) if st["offMarr"] else 0.0
    return sig(1.4 * (st["n"] + 0.7 * st["top3"] + 0.3 * st["aces"] - 6.6)) if (st["has7"] and st["n"] >= 4) else 0.0


def comp_p(hand: List[int], ci: int, trump: int, k: str, pen: float = 0.0) -> float:
    return comp_p0(hand, ci, trump, k, pen) * (1.0 if book_comp_ok(hand, ci, trump, k) else 0.82)


def estimate(hand: List[int], ci: int, trump: int, disc: Optional[List[int]]) -> float:
    if check_req(hand, ci, trump):
        return -1e9
    pen = sum(pts(x) for x in disc) / 10 * 0.6 if disc else 0.0
    ev = 0.0
    for k, v in CONTRACTS[ci]["comps"].items():
        p = comp_p(hand, ci, trump, k, pen)
        ev += p * v - (1 - p) * v * (2 if k == "ulti" else 1)
    return ev


_BID_CACHE: Dict[Tuple[Tuple[int, ...], int], List[dict]] = {}


def bid_options(h: List[int], min_ci: int, noise: float = 0.0, rng: Optional[random.Random] = None) -> List[dict]:
    """A zaj nélküli rangsor kézenként gyorsítótárazva; a zajt (a JS-ben ez csak döntetlen-törő) külön adjuk hozzá."""
    key = (tuple(sorted(h)), min_ci)
    base = _BID_CACHE.get(key)
    if base is None:
        base = _bid_options_raw(list(h), min_ci)
        if len(_BID_CACHE) > 300000:
            _BID_CACHE.clear()
        _BID_CACHE[key] = base
    if not noise or not rng:
        return [dict(b) for b in base]
    return _bid_options_noisy(h, min_ci, noise, rng)


def _bid_options_raw(h: List[int], min_ci: int, noise: float = 0.0, rng: Optional[random.Random] = None) -> List[dict]:
    res = []
    for ci in range(min_ci, len(CONTRACTS)):
        c = CONTRACTS[ci]
        best = None
        for s in suits_for(ci):
            for i in range(len(h)):
                for j in range(i + 1, len(h)):
                    rest = [x for q, x in enumerate(h) if q != i and q != j]
                    ev = estimate(rest, ci, s, [h[i], h[j]] if c["kind"] == "trump" else None)
                    if noise and rng:
                        ev += rng.random() * noise
                    if best is None or ev > best["ev"]:
                        best = {"ev": ev, "ci": ci, "suit": s, "disc": [h[i], h[j]]}
        if best and best["ev"] > -1e8:
            res.append(best)
    res.sort(key=lambda b: -b["ev"])
    return res


def _bid_options_noisy(h: List[int], min_ci: int, noise: float, rng: random.Random) -> List[dict]:
    return _bid_options_raw(list(h), min_ci, noise, rng)


def bot_wants_pickup(g: Game, p: int, rng: random.Random) -> bool:
    lo = g.min_bid_index()
    if lo >= len(CONTRACTS):
        return False
    best = -1e9
    for ci in range(lo, len(CONTRACTS)):
        for s in suits_for(ci):
            best = max(best, estimate(g.hands[p], ci, s, None))
    return best > 0.4 + rng.random() * 0.5


def bot_bid_action(g: Game, p: int, rng: random.Random) -> dict:
    if g.holding12(p):
        b = bid_options(g.hands[p], g.min_bid_index(), 0.05, rng)[0]
        return {"type": "bid", **b}
    if g.bid and g.bid[2] == p:
        if not g.can_pick_up(p):
            return {"type": "pass"}
        opts = bid_options(g.hands[p] + g.talon, g.min_bid_index())
        b = opts[0] if opts else None
        cur = estimate(g.hands[p], g.bid[0], g.bid[1], None)
        if b and b["ev"] > 0.5 and b["ev"] > cur + 1:
            return {"type": "pickup"}
        return {"type": "concede"} if cur < -concede_loss(g.bid[0], None, 1) - 0.5 else {"type": "pass"}
    if g.can_pick_up(p) and bot_wants_pickup(g, p, rng):
        return {"type": "pickup"}
    return {"type": "pass"}


def kontra_judge(g: Game, p: int, k: str) -> bool:
    c = CONTRACTS[g.ci]
    h, t = g.hands[p], g.trump
    if p == g.decl:
        pr = comp_p(h, g.ci, t, k)
        under = False
        for ci2 in range(g.ci + 1, len(CONTRACTS)):
            c2 = CONTRACTS[ci2]
            if under:
                break
            if c2["kind"] != c["kind"] or c2["red"] != c["red"] or k not in c2["comps"]:
                continue
            if not check_req(h, ci2, t) and book_check_ok(h, ci2, t) and estimate(h, ci2, t, None) > 1:
                under = True
        return pr > 0.85 or (pr > 0.7 and under)
    if g.must_kontra(p, k):
        return True
    pa = g.partner_of(p)
    if is_trump_contract(g.ci) and k != "durch":
        f, st = hand_facts(h, t), trump_stats(h, t)
        tops = (1 if f["hasA"] else 0) + (1 if f["hasX"] else 0)
        if k == "parti":
            pm = sum(v for _, v in g.marr[pa])
            mm = sum(v for _, v in g.marr[p])
            book_y = pm >= 20 and mm >= 20 and f["ut"] >= 2
            return book_y or st["S"] >= 3.6 or (f["n"] >= 4 and tops >= 1 and st["aces"] >= 1)
        if k in ("c40", "c20"):
            return f["n"] >= 4 or (f["n"] >= 3 and f["ut"] >= 2) or (f["n"] >= 2 and f["ut"] >= 2 and f["voids"] >= 1)
        if k == "ulti":
            return f["n"] >= 4 or (f["n"] == 3 and f["ut"] >= 2)
    if k == "betli":
        low = sum(1 for s in range(4) if any(suit(x) == s and rank(x) <= 1 for x in h))
        return low >= 3
    return any(len(of_suit(h, s)) >= 3 and any(rank(x) >= 6 for x in of_suit(h, s)) for s in range(4))


def bot_kontra(g: Game, p: int) -> List[str]:
    return [k for k in g.kontra_options(p) if kontra_judge(g, p, k)]


def play_auction_js(g: Game, rng: random.Random, seats_js: Optional[set] = None, custom=None) -> None:
    """Licit + kontra végigjátszása. A `custom(g, p, rng)` a nem-JS ülések licit-döntése."""
    guard = 0
    while g.phase == "bid" and guard < 80:
        guard += 1
        p = g.turn
        a = custom(g, p, rng) if (custom and (seats_js is None or p not in seats_js)) else bot_bid_action(g, p, rng)
        t = a["type"]
        if t == "bid":
            g.do_bid(p, a["disc"], a["ci"], a["suit"])
        elif t == "pickup":
            g.do_pickup(p)
        elif t == "concede":
            g.do_concede(p)
            return
        else:
            g.do_pass(p)

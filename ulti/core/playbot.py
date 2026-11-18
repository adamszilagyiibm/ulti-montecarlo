"""
playbot.py - Gyors, determinisztikus kijátszás-politika a rules.Game-hez.

A JS botChoose pontos portja (ulti.html). Ez a tanulás rollout-politikája: a licit/kontra döntések
értékét azzal mérjük, hogy a leosztást ezzel játsszuk végig. A test_rules.py lépésenként
összeveti a JS botChoose-szal (ugyanabban az állásban ugyanazt a lapot kell adnia).
"""
from __future__ import annotations

from typing import Callable, List, Sequence, TypeVar

from rules import CONTRACTS, Game, nxt, power, pts, rank, suit

T = TypeVar("T")


def _min_by(a: Sequence[T], f: Callable[[T], float]) -> T:
    m = a[0]
    fm = f(m)
    for x in a[1:]:
        fx = f(x)
        if fx < fm:
            m, fm = x, fx
    return m


def _max_by(a: Sequence[T], f: Callable[[T], float]) -> T:
    m = a[0]
    fm = f(m)
    for x in a[1:]:
        fx = f(x)
        if fx > fm:
            m, fm = x, fx
    return m


def _friendly(g: Game, a: int, b: int) -> bool:
    return (a == g.decl) == (b == g.decl)


def _would_win(g: Game, card: int) -> bool:
    if not g.trick:
        return True
    return g.beats(card, g.trick[g.trick_winner_idx(g.trick)][1])


def _is_top(g: Game, card: int, p: int) -> bool:
    tg = g.trump_game
    pc = power(card, tg)
    s = suit(card)
    return not any(suit(x) == s and power(x, tg) > pc for x in g.outstanding(p))


def _stopper_suit(g: Game, p: int, s: int) -> bool:
    mine = [x for x in g.hands[p] if suit(x) == s]
    if not mine:
        return False
    best = max(power(x, g.trump_game) for x in mine)
    higher = sum(1 for x in g.outstanding(p) if suit(x) == s and power(x, g.trump_game) > best)
    return len(mine) > higher


def _partner_suits(g: Game, p: int) -> List[int]:
    pa = g.partner_of(p)
    return [suit(tr[0][1]) for tr in g.hist if tr[0][0] == pa]


def bot_choose(g: Game, p: int) -> int:
    c = CONTRACTS[g.ci]
    comps = c["comps"]
    tg, t = g.trump_game, g.trump
    decl = p == g.decl
    L = g.legal_cards(p)
    ulti = "ulti" in comps

    if ulti and decl and g.trick_no < 9:
        f = [x for x in L if not (suit(x) == t and rank(x) == 0)]
        if len(f) < len(L) and f:
            L = f
    if len(L) == 1:
        return L[0]
    if tg and not ulti and g.trick_no < 9:
        seven = next((x for x in L if suit(x) == t and rank(x) == 0), None)
        if seven is not None:
            my_other = sum(1 for x in g.hands[p] if suit(x) == t and rank(x) != 0)
            out_t = sum(1 for x in g.outstanding(p) if suit(x) == t)
            if my_other >= 1 and my_other >= out_t:
                L = [x for x in L if x != seven]
            elif g.trick_no >= 5:
                return seven
    if len(L) == 1:
        return L[0]

    def P(x: int) -> int:
        return power(x, tg)

    def is_t(x: int) -> bool:
        return tg and suit(x) == t

    def cost(x: int) -> float:
        return pts(x) * 3 + P(x) + (6 if is_t(x) else 0)

    tr = g.trick
    lead = not tr

    if c["kind"] == "betli":
        if decl:
            if lead:
                out = g.outstanding(p)
                return _min_by(L, lambda x: P(x) * 10 - sum(1 for y in out if suit(y) == suit(x) and rank(y) < rank(x)))
            lo = [x for x in L if not _would_win(g, x)]
            return _max_by(lo, P) if lo else _min_by(L, P)
        if not lead and tr[g.trick_winner_idx(tr)][0] == g.decl:
            lo = [x for x in L if not _would_win(g, x)]
            if lo:
                return _max_by(lo, P)
        if lead:
            pa = g.partner_of(p)
            shed = [x for x in L if g.known_void(pa, suit(x)) and not g.known_void(g.decl, suit(x))]
            if shed:
                return _max_by(shed, P)
            safe = [x for x in L if not g.known_void(g.decl, suit(x))]
            if safe and len(safe) < len(L):
                return _min_by(safe, P)
        return _min_by(L, P)

    if "durch" in comps:
        if decl:
            if lead:
                tops = [x for x in L if _is_top(g, x, p)]
                return _max_by(tops or L, P)
            w = [x for x in L if _would_win(g, x)]
            if w:
                return _min_by(w, P) if len(tr) == 2 else _max_by(w, P)
            return _min_by(L, P)
        if lead:
            tops = [x for x in L if _is_top(g, x, p)]
            return tops[0] if tops else _min_by(L, P)

        def keep(x: int) -> float:
            return (100 if _stopper_suit(g, p, suit(x)) else 0) + P(x)

        if tr[g.trick_winner_idx(tr)][0] != g.decl:
            return _min_by(L, keep)
        w = [x for x in L if _would_win(g, x)]
        return _min_by(w, P) if w else _min_by(L, keep)

    if lead:
        out = g.outstanding(p)
        out_tr = [y for y in out if suit(y) == t] if tg else []
        my_tr = [x for x in L if is_t(x)]
        if decl and out_tr and my_tr:
            hi = _max_by(my_tr, P)
            if P(hi) > max(P(y) for y in out_tr):
                return hi
        if decl and ulti and out_tr:
            my = [x for x in my_tr if rank(x) != 0]
            if len(my) > len(out_tr):
                return _max_by(my, P)

        def dv(s: int) -> bool:
            return (not decl) and g.known_void(g.decl, s)

        if not decl and ulti:
            force = [x for x in L if not is_t(x) and dv(suit(x)) and not pts(x)]
            if force:
                return _min_by(force, P)
        sure = [x for x in L if not is_t(x) and _is_top(g, x, p) and not dv(suit(x))]
        if sure:
            return _max_by(sure, lambda y: pts(y) * 10 + P(y))
        if decl and ulti and out_tr:
            groups = [[x for x in L if suit(x) == s] for s in range(4) if s != t]
            groups = sorted([a for a in groups if len(a) >= 3], key=lambda a: -len(a))
            if groups:
                return _max_by(groups[0], P)
        if not decl:
            pal = [s for s in _partner_suits(g, p) if s != t and not dv(s)]
            ps = [x for x in L if suit(x) in pal and not is_t(x)]
            if ps:
                np2 = [x for x in ps if not pts(x)]
                return _min_by(np2 or ps, P)
        non = [x for x in L if not is_t(x)]
        pool0 = non or L
        pool = [x for x in pool0 if not dv(suit(x))] or pool0
        npts = [x for x in pool if not pts(x)]
        return _min_by(npts or pool, P)

    w_idx = g.trick_winner_idx(tr)
    wp, wc = tr[w_idx]
    last = len(tr) == 2
    if _friendly(g, wp, p):
        safe = last or _friendly(g, nxt(p), p) or (_is_top(g, wc, p) and (not tg or suit(wc) == t))
        if safe:
            return _max_by(L, lambda y: (0 if is_t(y) else pts(y) * 20) - cost(y))
        return _min_by(L, cost)
    wins = [x for x in L if _would_win(g, x)]
    if wins:
        if last:
            return _min_by(wins, lambda y: P(y) + (6 if is_t(y) else -pts(y) * 1.5))
        safe_w = [x for x in wins if _is_top(g, x, p)]
        if safe_w:
            return _min_by(safe_w, P)
        if sum(pts(x[1]) for x in tr) >= 10:
            return _min_by(wins, P)
        if len(wins) == len(L):
            return _min_by(wins, P)
    return _min_by(L, cost)

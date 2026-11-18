"""
mcplay.py - Determinizált Monte Carlo kijátszás a rules.Game-en, a heurisztika (playbot) a rollout-politika.

A heurisztikus tudás (playbot.bot_choose) és a keresés együtt dolgozik:
  * a nem látott lapokat véletlenül osztjuk szét, a tudott renonszokat (Game.known_void) betartva,
  * minden jelölt lapot ugyanazokon a determinizációkon értékelünk (közös véletlen, kisebb szórás),
  * a rollout-ban a többiek playbot-ot játszanak, epsilon eséllyel véletlen szabályos lapot,
  * a célfüggvény a PÉNZ (result["change"]), nem a nyerési arány.

Információs szabály: a döntő játékos csak azt látja, amit valódi játékban is látna
(saját lapok, kijátszott lapok, a felvevő a talont). A többi kéz a látatlan lapokból töltődik fel.
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Set

from playbot import bot_choose
from rules import Game, pts, suit


def _deal_unseen(g: Game, p: int, rng: random.Random, fixed: Optional[Set[int]] = None) -> None:
    """A g-ben (klón) a p-n kívüli kezeket újraosztja a látatlan lapokból. Felvevői nézetben a talon ismert.
    fixed: azok a játékosok, akiknek a keze p számára is ismert (pl. nyitott játékban az első ütés után): azt nem érinti."""
    fx = set(fixed or ())
    pool = [c for c in g.outstanding(p) if not any(c in g.hands[q] for q in fx)]
    others = [q for q in range(3) if q != p and q not in fx]
    sizes = {q: len(g.hands[q]) for q in others}
    voids = {q: {s for s in range(4) if g.known_void(q, s)} for q in others}
    for _ in range(25):
        rng.shuffle(pool)
        hands: Dict[int, List[int]] = {q: [] for q in others}
        rest: List[int] = []
        ok = True
        # a legkorlátozottabb játékos kapja először a lapjait
        for q in sorted(others, key=lambda x: -len(voids[x])):
            take = [c for c in pool if suit(c) not in voids[q] and not any(c in h for h in hands.values())]
            if len(take) < sizes[q]:
                ok = False
                break
            hands[q] = take[:sizes[q]]
        if not ok:
            continue
        used = {c for h in hands.values() for c in h}
        rest = [c for c in pool if c not in used]
        break
    else:  # nem teljesíthető a renonsz-megkötés: korlátozás nélkül osztunk
        rng.shuffle(pool)
        hands = {}
        k = 0
        for q in others:
            hands[q] = pool[k:k + sizes[q]]
            k += sizes[q]
        rest = pool[k:]
    for q in others:
        g.hands[q] = hands[q]
    if p != g.decl and len(rest) == len(g.talon):
        g.talon = rest  # védőként a talon sem ismert, a maradék lapok adják
        g.talon_pts = sum(pts(c) for c in rest)  # a valódi talon pontja nem szivároghat


def _rollout(g: Game, rng: random.Random, eps: float) -> None:
    guard = 0
    while g.phase == "play" and guard < 64:
        guard += 1
        if len(g.trick) == 3:
            g.collect_trick()
            continue
        q = g.turn
        legal = g.legal_cards(q)
        c = rng.choice(legal) if (len(legal) > 1 and rng.random() < eps) else bot_choose(g, q)
        g.play_card(q, c)
    if g.phase == "play" and len(g.trick) == 3:
        g.collect_trick()


def mc_choose(g: Game, p: int, rng: random.Random, samples: int = 12, eps: float = 0.0,
              fixed: Optional[Set[int]] = None, budget: Optional[float] = None, min_samples: int = 4) -> int:
    """A p játékos lapja: a legtöbb átlagpénzt hozó szabályos lap a determinizált rollout-okon.
    fixed: azok a játékosok, akiknek a keze p számára ismert (nyitott játék). budget: másodperc; a mintavétel ennyi idő után
    leáll (legalább min_samples minta után), így minden jelölt ugyanannyi mintát kap."""
    legal = g.legal_cards(p)
    if len(legal) == 1:
        return legal[0]
    totals = {c: 0.0 for c in legal}
    t0 = time.monotonic()
    for i in range(samples):
        if budget is not None and i >= min_samples and time.monotonic() - t0 > budget:
            break
        world = g.clone()
        _deal_unseen(world, p, rng, fixed)
        seed = rng.random()
        for c in legal:
            sim = world.clone()
            sim.play_card(p, c)
            _rollout(sim, random.Random(seed), eps)
            totals[c] += sim.result["change"][p]
    return max(legal, key=lambda c: totals[c])


class MCPlay:
    """Bármely licit/kontra politika kijátszását Monte Carlo keresésre cseréli."""

    def __init__(self, base: Any, samples: int = 12, eps: float = 0.0, name: Optional[str] = None):
        self.base = base
        self.samples = samples
        self.eps = eps
        self.name = name or f"{getattr(base, 'name', 'x')}+mc{samples}"

    def bid_action(self, g: Game, p: int, rng: random.Random) -> dict:
        return self.base.bid_action(g, p, rng)

    def kontra(self, g: Game, p: int, rng: random.Random) -> List[str]:
        return self.base.kontra(g, p, rng)

    def card(self, g: Game, p: int, rng: random.Random) -> int:
        return mc_choose(g, p, rng, self.samples, self.eps)

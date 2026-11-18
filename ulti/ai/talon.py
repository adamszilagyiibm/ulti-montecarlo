"""
talon.py - Mi lehet a talonban? A felvevő nem véletlen két lapot tesz le: szinte soha nem adut, tízest vagy ászt.

A modell (szakasz x játékcsoport x rang) szerint tanulja a leadási arányt:
    rate[szakasz][csoport][rang] = (leadott lapok) / (a 12 lapos kézben lévő ilyen lapok)
Szakasz: 0 = friss talon (a licitáló az osztásból tett le), 1 = újrahasznosított talon (a licitáló
előbb felvette az előző talont, és abból a 12 lapból tett le újra). Az utóbbi eloszlása más.
Csoport: 0 = adu színű lap (színes játék), 1 = mellék szín (színes játék), 2 = betli, 3 = durchmars.

Posterior (a rablást fontolgató játékos szemszögéből): egy ismeretlen lap a talonban, a licitáló kezében vagy
a harmadik játékos kezében lehet. A licitáló 12 lapjából a lap r eséllyel került a talonba. Ha a licitáló az
én korábban lerakott lapjaimat vette fel, azok biztosan nála voltak (a harmadik kéz kizárt).
"""
from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from rules import CONTRACTS, Game, is_trump_contract, rank, suit

N_STAGES = 2
N_GROUPS = 4

# Mért kiinduló arányok (JS-bot licitjeiből); a fit_talon.py a politikákból újraszámolja.
_BASE = np.array([
    [0.060, 0.050, 0.050, 0.000, 0.050, 0.010, 0.030, 0.000],
    [0.200, 0.210, 0.190, 0.012, 0.200, 0.160, 0.140, 0.003],
    [0.050, 0.050, 0.050, 0.100, 0.120, 0.250, 0.300, 0.350],
    [0.150, 0.150, 0.150, 0.150, 0.150, 0.150, 0.150, 0.150],
])
DEFAULT_RATES = np.stack([_BASE, _BASE])


def group_of(ci: int, suit_c: int, trump: int) -> int:
    if is_trump_contract(ci):
        return 0 if suit_c == trump else 1
    return 2 if CONTRACTS[ci]["kind"] == "betli" else 3


def class_index(ci: int, trump: int, card: int) -> Tuple[int, int]:
    return group_of(ci, suit(card), trump), rank(card)


def stage_of(blog: Sequence[tuple], idx: int) -> int:
    """A blog[idx] licit szakasza: 1, ha a licitáló közvetlenül előtte felvette a talont."""
    if idx <= 0:
        return 0
    prev = blog[idx - 1]
    return 1 if prev[0] == "pickup" and prev[1] == blog[idx][1] else 0


class TalonModel:
    def __init__(self, rates: Optional[np.ndarray] = None):
        self.rates = np.array(rates if rates is not None else DEFAULT_RATES, dtype=float)

    # ---------- mentés ----------
    def to_json(self) -> List[List[List[float]]]:
        return [[[float(x) for x in row] for row in st] for st in self.rates]

    @classmethod
    def from_json(cls, d: Optional[Sequence]) -> "TalonModel":
        if not d:
            return cls()
        a = np.array(d, dtype=float)
        return cls(a) if a.shape == (N_STAGES, N_GROUPS, 8) else cls()

    # ---------- tanulás ----------
    @staticmethod
    def count(stage: int, ci: int, trump: int, hand12: Iterable[int], disc: Iterable[int], into: np.ndarray) -> None:
        """into: (2, N_STAGES, N_GROUPS, 8): [0] a kézben lévők, [1] a leadottak száma."""
        for c in hand12:
            gi, r = class_index(ci, trump, c)
            into[0, stage, gi, r] += 1
        for c in disc:
            gi, r = class_index(ci, trump, c)
            into[1, stage, gi, r] += 1

    @classmethod
    def from_counts(cls, counts: np.ndarray, prior: float = 6.0) -> "TalonModel":
        """Bayes-simítás: kevés adatnál a kiinduló arányhoz húz."""
        return cls((counts[1] + prior * DEFAULT_RATES) / (counts[0] + prior))

    # ---------- becslés ----------
    @staticmethod
    def with_owner(g: Game, p: int) -> Set[int]:
        """A p által korábban lerakott lapok, amelyek biztosan a jelenlegi licitálónál vannak:
        p lerakta, a licitáló felvette a talont, majd újralicitált. (Ekkor a harmadik játékosnál nincsenek.)"""
        talon_owner: Optional[int] = None
        talon_cards: Set[int] = set()
        picked: Optional[Tuple[int, Set[int]]] = None
        held: Dict[int, Set[int]] = {}
        for e in g.blog:
            if e[0] == "bid":
                if picked is not None and e[1] == picked[0]:
                    held[e[1]] = picked[1]
                else:
                    held.pop(e[1], None)
                picked = None
                talon_owner, talon_cards = e[1], set(e[5])
            elif e[0] == "pickup":
                picked = (e[1], set(talon_cards) if talon_owner == p else set())
        return held.get(g.bid[2], set()) if g.bid else set()

    def weights(self, g: Game, p: int, pool: Sequence[int]) -> List[float]:
        """Annak esélye (odds), hogy a pool lapja a talonban van, a licitáló szemszögéből adott szakaszban."""
        ci, tr, owner = g.bid
        last_bid = max(i for i, e in enumerate(g.blog) if e[0] == "bid" and e[1] == owner)
        st = stage_of(g.blog, last_bid)
        known = self.with_owner(g, p)
        out = []
        for c in pool:
            gi, r = class_index(ci, tr, c)
            rt = min(max(float(self.rates[st][gi][r]), 1e-4), 0.95)
            if c in known:
                out.append(rt / (1.0 - rt))
            else:
                out.append(12.0 * rt / (12.0 * (1.0 - rt) + 10.0))
        return out

    def sample(self, g: Game, p: int, rng: random.Random) -> List[int]:
        """Két lap a talonra a posteriorból (visszatevés nélkül)."""
        pool = g.outstanding(p)
        w = self.weights(g, p, pool)
        pick: List[int] = []
        for _ in range(2):
            tot = sum(w)
            x = rng.random() * tot
            acc = 0.0
            sel = -1
            for i, wi in enumerate(w):
                acc += wi
                if x <= acc and wi > 0:
                    sel = i
                    break
            if sel < 0:
                sel = max(range(len(w)), key=lambda j: w[j])
            pick.append(pool[sel])
            w[sel] = 0.0
        return pick

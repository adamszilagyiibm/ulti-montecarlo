"""
features.py - Kézjellemzők a tanulható licit/kontra modellekhez.

A kézből (a talonlerakás UTÁN: 10 lap) és az aduból számolt, olcsón számítható mennyiségek. A modell
ezekből tanulja meg, valójában mennyi egy vállalásrész sikerének esélye. A képlet nem rögzített,
a súlyokat a szimulációk (valódi szabályok, valódi pontozás) határozzák meg.
"""
from __future__ import annotations

from typing import List, Sequence

from rules import TP, pts

N_FEATURES = 24
FEATURE_NAMES = [
    "bias", "n_trump", "n_trump_sq", "has_A", "has_X", "has_K", "has_F", "has_7", "trump_run", "top3",
    "side_aces", "side_tens_covered", "side_tens_bare", "voids", "singles", "long_side", "has40", "n_marr_side",
    "marr_pts", "high_cards", "low_cards", "betli_risk", "durch_loss", "disc_pts",
]


def _suit_cards(h: Sequence[int], s: int) -> List[int]:
    lo = s << 3
    return [x for x in h if lo <= x < lo + 8]


def hand_features(h: Sequence[int], trump: int, disc: Sequence[int] = ()) -> List[float]:
    """h: a 10 lap lerakás után. trump: -1, ha nincs adu (betli, színtelen durchmars)."""
    hs = set(h)
    f = [0.0] * N_FEATURES
    f[0] = 1.0
    if trump >= 0:
        tc = _suit_cards(h, trump)
        n = len(tc)
        f[1] = n
        f[2] = n * n / 8.0
        base = trump << 3
        f[3] = 1.0 if base + 7 in hs else 0.0
        f[4] = 1.0 if base + 3 in hs else 0.0
        f[5] = 1.0 if base + 6 in hs else 0.0
        f[6] = 1.0 if base + 5 in hs else 0.0
        f[7] = 1.0 if base in hs else 0.0
        tp = sorted((TP[x & 7] for x in tc), reverse=True)
        run = 0
        while run < len(tp) and tp[run] == 7 - run:
            run += 1
        f[8] = run
        f[9] = f[3] + f[4] + f[5]
        f[16] = 1.0 if (base + 6 in hs and base + 5 in hs) else 0.0
    side_aces = covered = bare = voids = singles = long_side = n_marr = marr_pts = 0
    betli_risk = 0
    durch_loss = 0
    for s in range(4):
        cs = _suit_cards(h, s)
        b = s << 3
        rs = sorted(x & 7 for x in cs)
        for i, r in enumerate(rs):
            if r > 2 * i:
                betli_risk += 1
        if s != trump:
            if not cs:
                voids += 1
            elif len(cs) == 1:
                singles += 1
            if b + 7 in hs:
                side_aces += 1
                long_side = max(long_side, len(cs))
                if b + 3 in hs:
                    covered += 1
            elif b + 3 in hs:
                bare += 1
            if b + 6 in hs and b + 5 in hs:
                n_marr += 1
                marr_pts += 20
        elif b + 6 in hs and b + 5 in hs:
            marr_pts += 40
        ds = sorted((x & 7 for x in cs), reverse=True)
        if ds:
            run = 0
            while run < len(ds) and ds[run] == 7 - run:
                run += 1
            if run < len(ds) and run < 8 - len(ds):
                durch_loss += len(ds) - run
    f[10] = side_aces
    f[11] = covered
    f[12] = bare
    f[13] = voids
    f[14] = singles
    f[15] = long_side
    f[17] = n_marr
    f[18] = marr_pts / 20.0
    f[19] = sum(1 for x in h if (x & 7) >= 5)
    f[20] = sum(1 for x in h if (x & 7) <= 1)
    f[21] = betli_risk
    f[22] = durch_loss
    f[23] = sum(pts(x) for x in disc) / 10.0 if disc else 0.0
    return f

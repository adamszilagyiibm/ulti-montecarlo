"""fit_talon.py - A talon-modell (mit tesz le a licitáló) tanulása lejátszott licitekből.

A licitáló 12 lapos kezéből a ténylegesen leadott két lap osztályonkénti aránya, szakaszonként
(friss talon / felvétel utáni újralicit). Az ellenfél-politikák keverékéből tanul, hogy a becslés ne
egyetlen botra illeszkedjen.

Használat: python fit_talon.py [leosztás] [--path learned_policy.json]
"""
from ulti.paths import POLICY
import argparse
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

import arena
from policy import LearnedPolicy
from rules import Game
from talon import N_GROUPS, N_STAGES, TalonModel


def _count(args):
    seeds, path = args
    pols_all = [arena.JsPolicy(), LearnedPolicy.load(Path(path))]
    counts = np.zeros((2, N_STAGES, N_GROUPS, 8))
    for sd in seeds:
        g = Game.new_deal(sd % 3, random.Random(sd))
        rng = random.Random(sd * 31 + 5)
        pols = [pols_all[(sd + i) % 2] for i in range(3)]
        guard = 0
        while g.phase == "bid" and guard < 80:
            guard += 1
            p = g.turn
            a = pols[p].bid_action(g, p, rng)
            t = a["type"]
            if t == "bid":
                stage = 1 if g.blog and g.blog[-1] == ("pickup", p) else 0
                TalonModel.count(stage, a["ci"], a["suit"], g.hands[p], a["disc"], counts)
                g.do_bid(p, a["disc"], a["ci"], a["suit"])
            elif t == "pickup":
                g.do_pickup(p)
            elif t == "concede":
                break
            else:
                g.do_pass(p)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("deals", type=int, nargs="?", default=4000)
    ap.add_argument("--path", default=str(POLICY / "learned_policy.json"))
    a = ap.parse_args()
    seeds = list(range(700_000, 700_000 + a.deals))
    chunks = [(seeds[i::32], a.path) for i in range(32)]
    with ProcessPoolExecutor(max_workers=8) as ex:
        counts = sum(ex.map(_count, chunks))
    pol = LearnedPolicy.load(Path(a.path))
    pol.talon = TalonModel.from_counts(counts)
    pol.save(Path(a.path))
    print("licitek szakaszonként:", [int(counts[1, s].sum() / 2) for s in range(N_STAGES)])
    names = ["adu", "mellék", "betli", "durch"]
    for s in range(N_STAGES):
        print(f"szakasz {s} (0=friss, 1=felvétel után):")
        for gi in range(N_GROUPS):
            print(f"  {names[gi]:7s}", " ".join(f"{x:.3f}" for x in pol.talon.rates[s][gi]))


if __name__ == "__main__":
    main()

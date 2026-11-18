"""
recal.py - A védői sikerbecslő modellek ("d_*") újratanítása a TELJES licitmenetű önjátékból.

Miért: a learn.py a védői sorokat olyan leosztásokból gyűjtötte, ahol csak az első kéz licitált (35% véletlen vállalással).
A valódi licit (rablással, jó vállalásokkal) után a felvevő sokkal gyakrabban teljesít, így a modell a kis esélyeket
alulbecsülte (2%-os jóslatnál ~22% volt a tényleges). Itt a sorokat a kontra-fázisból vesszük: a védő 10 lapja, a
vállalás környezete -> a komponens ténylegesen teljesült-e (kontra nélküli kijátszással, ami a playbotnál ugyanaz).

Használat:  python recal.py --init learned_policy.json --out /tmp/lp_recal.json --deals 40000
"""
from __future__ import annotations

from ulti.paths import POLICY
import argparse
import random
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

import arena
from features import hand_features
from learn import MIN_ROWS, WORKERS, _pick_random, fit_logistic
from playbot import bot_choose
from policy import LearnedPolicy, ctx_of, model_key
from rules import Game

Data = Dict[str, Tuple[np.ndarray, np.ndarray]]


def _worker(args) -> Dict[str, Tuple[list, list]]:
    pol_json, seeds, eps = args
    pol = LearnedPolicy.from_json(pol_json)
    out: Dict[str, Tuple[list, list]] = {}
    for sd in seeds:
        rng = random.Random(sd)
        g = Game.new_deal(sd % 3, rng)
        first = True
        while g.phase == "bid":
            p = g.turn
            if first and rng.random() < eps:
                ci, su, disc = _pick_random(g, rng)
                g.do_bid(p, disc, ci, su)
            else:
                a = pol.bid_action(g, p, rng)
                if a["type"] == "bid":
                    g.do_bid(p, a["disc"], a["ci"], a["suit"])
                elif a["type"] == "pickup":
                    g.do_pickup(p)
                elif a["type"] == "concede":
                    g.do_concede(p)
                else:
                    g.do_pass(p)
            first = False
        if g.phase != "kontra":
            continue
        trump = g.trump if g.trump_game else -1
        ctx = ctx_of(g.ci)
        rows = [(q, np.concatenate([hand_features(g.hands[q], trump, ()), ctx])) for q in range(3) if q != g.decl]
        while g.phase == "kontra":
            g.do_kontra(g.turn, [])
        while g.phase == "play":
            if len(g.trick) == 3:
                g.collect_trick()
                continue
            q = g.turn
            g.play_card(q, bot_choose(g, q))
        win = {r["k"]: r["win"] for r in g.result["rows"] if r["k"]}
        for k, w in win.items():
            key = "d_" + model_key(g.ci, k)
            d = out.setdefault(key, ([], []))
            for _, x in rows:
                d[0].append(x)
                d[1].append(1.0 if w else 0.0)
    return out


def gather_defender(pol: LearnedPolicy, seed0: int, n: int, eps: float) -> Data:
    seeds = list(range(seed0, seed0 + n))
    chunks = [(pol.to_json(), seeds[i::WORKERS * 4], eps) for i in range(WORKERS * 4)]
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        parts = list(ex.map(_worker, chunks))
    merged: Dict[str, List[Tuple[list, list]]] = {}
    for part in parts:
        for k, v in part.items():
            merged.setdefault(k, []).append(v)
    return {k: (np.array([x for v in vs for x in v[0]]), np.array([y for v in vs for y in v[1]])) for k, vs in merged.items()}


def refit(pol: LearnedPolicy, data: Data) -> LearnedPolicy:
    """Csak a védői modellek cserélődnek, a többi súly és a paraméterek változatlanok."""
    w = {k: v.copy() for k, v in pol.w.items()}
    for key, (X, y) in data.items():
        if len(y) >= MIN_ROWS:
            w[key] = fit_logistic(X, y)
    return LearnedPolicy(w, dict(pol.params), pol.talon)


def calibration(pol: LearnedPolicy, data: Data) -> Tuple[float, float]:
    """(Brier-hiba, log-veszteség) a védői soron."""
    se = ll = n = 0.0
    for key, (X, y) in data.items():
        if key not in pol.w:
            continue
        z = X @ pol.w[key]
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        se += float(((p - y) ** 2).sum())
        ll += float(-(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))).sum())
        n += len(y)
    return se / n, ll / n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", default=str(POLICY / "learned_policy.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--deals", type=int, default=40000)
    ap.add_argument("--eps", type=float, default=0.15)
    ap.add_argument("--seed0", type=int, default=3_000_000)
    ap.add_argument("--test-deals", type=int, default=8000)
    a = ap.parse_args()
    pol = LearnedPolicy.load(Path(a.init))
    t = time.time()
    train = gather_defender(pol, a.seed0, a.deals, a.eps)
    test = gather_defender(pol, 6_000_000, a.test_deals, a.eps)         # a tanításban nem látott leosztások
    print(f"adat: {sum(len(v[1]) for v in train.values())} sor ({time.time() - t:.0f} s)")
    new = refit(pol, train)
    for name, p in (("régi", pol), ("új", new)):
        b, l = calibration(p, test)
        print(f"{name:5s} védői modell, teszt-adaton: Brier {b:.4f}  log-veszteség {l:.4f}")
    new.save(Path(a.out))
    print("mentve:", a.out)


if __name__ == "__main__":
    main()

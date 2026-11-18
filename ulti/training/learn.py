"""
learn.py - A licit/kontra politika tanulása önjátékból, VALÓDI szabályokkal és valódi pénzzel.

Körönként:
  1. Adatgyűjtés: leosztás -> (vállalás, adu, talonlerakás) -> végigjátszás a playbottal -> a vállalásrészek
     tényleges teljesülése. Az első körben véletlen döntések, később a tanult politika (+ felfedezés).
  2. Illesztés: komponensenként logisztikus regresszió (felvevői és védői nézet), a "csendes" tételekre
     ridge-regresszió.
  3. Mérés a páros mérőpadon (arena.py): a tanult ülés pénze a JS-botos ülés pénzéhez képest.
A legjobb kör (külön, a tanításban nem látott leosztásokon mérve) kerül mentésre.

Használat:  python learn.py --rounds 4 --deals 6000 --eval 240
"""
from __future__ import annotations

import argparse
import functools
import random
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

import arena
from features import hand_features
from playbot import bot_choose
from policy import DEFAULT_PATH, MODEL_KEYS, LearnedPolicy, ctx_of, make_learned, model_key, zero_weights
from rules import CONTRACTS, Game, suits_for

WORKERS = 8
TRAIN_SEED0 = 100_000     # tanító leosztások
TUNE_SEED0 = 500_000      # küszöb-hangoló leosztások
TEST_SEED0 = 900_000      # végső, érintetlen mérés


# ---------------------------------------------------------------- adatgyűjtés
def _pick_random(g: Game, rng: random.Random):
    p = g.fh
    hand = g.hands[p]
    for _ in range(200):
        ci = rng.randrange(len(CONTRACTS))
        su = rng.choice(suits_for(ci))
        disc = rng.sample(hand, 2)
        if g.validate_bid(p, disc, ci, su) is None:
            return ci, su, disc
    raise RuntimeError("nincs érvényes licit")


def _gen_worker(args):
    weights_json, seeds, eps = args
    pol = LearnedPolicy.from_json(weights_json) if weights_json else None
    out: Dict[str, list] = {}

    def add(key: str, x: np.ndarray, y: float) -> None:
        d = out.setdefault(key, [[], []])
        d[0].append(x)
        d[1].append(y)

    for sd in seeds:
        rng = random.Random(sd)
        g = Game.new_deal(sd % 3, rng)
        p = g.fh
        if pol is not None and rng.random() > eps:
            b = pol.best_option(g.hands[p], 0, True)
            ci, su, disc = b[1], b[2], b[3]
        else:
            ci, su, disc = _pick_random(g, rng)
        g.do_bid(p, disc, ci, su)
        while g.phase == "bid":
            g.do_pass(g.turn)
        while g.phase == "kontra":
            g.do_kontra(g.turn, [])
        decl, talon = g.decl, tuple(g.talon)
        start = {q: list(g.hands[q]) for q in range(3)}
        while g.phase == "play":
            if len(g.trick) == 3:
                g.collect_trick()
                continue
            q = g.turn
            g.play_card(q, bot_choose(g, q))
        rows = {r["k"]: r for r in g.result["rows"] if r["k"]}
        silent = sum(r["delta"] for r in g.result["rows"] if not r["k"])
        trump_f = su if g.trump_game else -1
        ctx = ctx_of(ci)
        xd = np.concatenate([hand_features(start[decl], trump_f, talon), ctx])
        add("silent", xd, float(silent))
        for k in CONTRACTS[ci]["comps"]:
            win = 1.0 if rows[k]["win"] else 0.0
            add(model_key(ci, k), xd, win)
            for q in range(3):
                if q != decl:
                    xq = np.concatenate([hand_features(start[q], trump_f, ()), ctx])
                    add("d_" + model_key(ci, k), xq, win)
    return {k: (np.array(v[0]), np.array(v[1])) for k, v in out.items()}


def gather(pol: Optional[LearnedPolicy], seed0: int, n: int, eps: float) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    wj = pol.to_json() if pol else None
    seeds = list(range(seed0, seed0 + n))
    chunks = [(wj, seeds[i::WORKERS * 4], eps) for i in range(WORKERS * 4)]
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        parts = list(ex.map(_gen_worker, chunks))
    merged: Dict[str, List[Tuple[np.ndarray, np.ndarray]]] = {}
    for part in parts:
        for k, (X, y) in part.items():
            merged.setdefault(k, []).append((X, y))
    return {k: (np.vstack([a for a, _ in v]), np.concatenate([b for _, b in v])) for k, v in merged.items()}


# ---------------------------------------------------------------- illesztés
def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float = 3.0, iters: int = 25) -> np.ndarray:
    d = X.shape[1]
    mu, sd = X.mean(0), X.std(0)
    mu[0], sd[0] = 0.0, 1.0           # bias
    sd[sd < 1e-9] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(d)
    reg = np.full(d, lam)
    reg[0] = 1e-6
    for _ in range(iters):
        z = np.clip(Z @ w, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        g = Z.T @ (p - y) + reg * w
        H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg)
        step = np.linalg.solve(H, g)
        w = w - step
        if np.abs(step).max() < 1e-7:
            break
    w_orig = w / sd
    w_orig[0] = w[0] - np.sum(w[1:] * mu[1:] / sd[1:])
    return w_orig


def fit_ridge(X: np.ndarray, y: np.ndarray, lam: float = 30.0) -> np.ndarray:
    reg = np.full(X.shape[1], lam)
    reg[0] = 1e-6
    return np.linalg.solve(X.T @ X + np.diag(reg), X.T @ y)


MIN_ROWS = 40


def fit_policy(data: Dict[str, Tuple[np.ndarray, np.ndarray]], base: Optional[LearnedPolicy] = None) -> LearnedPolicy:
    w = zero_weights()
    for k in MODEL_KEYS:
        w[k][0] = -2.0
    pol = LearnedPolicy(w, base.params if base else None)
    for key, (X, y) in data.items():
        if len(y) < MIN_ROWS:
            continue
        pol.w[key] = fit_ridge(X, y) if key == "silent" else fit_logistic(X, y)
    return pol


# ---------------------------------------------------------------- mérés
def evaluate_policy(pol: LearnedPolicy, seed0: int, deals: int, tmp: Path) -> dict:
    pol.save(tmp)
    return arena.evaluate(functools.partial(make_learned, str(tmp)), deals=deals, seed0=seed0, workers=WORKERS)


def summarize(r: dict) -> str:
    return (f"pénz/ülés: tanult {r['test_money']}  JS {r['base_money']}  | páros különbség {r['paired_diff']} "
            f"| felvevő arány tanult {r['declares_test']:.2f} JS {r['declares_base']:.2f} | bedobás {r['concede_rate_test']:.3f}")


TUNE_GRID: Dict[str, List[float]] = {
    "pickup_thr": [-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 6.0],
    "n_talon": [4, 8, 16],
    "kt": [0.10, 0.15, 0.20, 0.25, 0.35, 0.45, 0.55],
    "rt": [0.55, 0.65, 0.75, 0.8, 0.9, 0.97],
    "own_thr": [0.0, 0.5, 1.5],
    "own_gap": [0.0, 1.0, 2.0],
    "concede_margin": [0.5, 1.0, 2.0],
    "fail_mult": [0.8, 1.0, 1.25],
}


def tune_params(pol: LearnedPolicy, deals: int, tmp: Path) -> Tuple[LearnedPolicy, float]:
    best = evaluate_policy(pol, TUNE_SEED0, deals, tmp)["paired_diff"].mean
    print(f"    kiinduló hangolási érték: {best:+.3f}", flush=True)
    for name, vals in TUNE_GRID.items():
        cur = pol.params[name]
        for v in vals:
            if v == cur:
                continue
            cand = LearnedPolicy(pol.w, {**pol.params, name: v}, pol.talon)
            m = evaluate_policy(cand, TUNE_SEED0, deals, tmp)["paired_diff"].mean
            if m > best + 1e-9:
                best, pol = m, cand
                print(f"    {name}: {cur} -> {v}  ({best:+.3f})", flush=True)
                cur = v
    return pol, best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--deals", type=int, default=6000)
    ap.add_argument("--eval", type=int, default=240)
    ap.add_argument("--tune", type=int, default=150, help="hangolási leosztások (0 = nincs hangolás)")
    ap.add_argument("--out", default=str(DEFAULT_PATH))
    ap.add_argument("--init", default="", help="induló politika (mentett json): az adat a saját licitjéből gyűlik (on-policy)")
    ap.add_argument("--eps", type=float, default=0.35, help="felfedezés: ennyi eséllyel véletlen licit (a nem-első körökben)")
    ap.add_argument("--seed0", type=int, default=TRAIN_SEED0)
    a = ap.parse_args()
    tmp = Path(a.out).with_suffix(".tmp.json")
    data: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    pol: Optional[LearnedPolicy] = LearnedPolicy.load(Path(a.init)) if a.init else None
    best_pol, best_score = None, -1e9
    for rnd in range(a.rounds):
        t0 = time.time()
        eps = 1.0 if pol is None else a.eps
        new = gather(pol, a.seed0 + rnd * a.deals, a.deals, eps)
        for k, (X, y) in new.items():
            data[k] = (np.vstack([data[k][0], X]), np.concatenate([data[k][1], y])) if k in data else (X, y)
        pol = fit_policy(data, pol)
        n_rows = sum(len(v[1]) for v in data.values())
        print(f"[kör {rnd + 1}] adat: {n_rows} sor | {time.time() - t0:.0f} s", flush=True)
        r = evaluate_policy(pol, TEST_SEED0 - 50_000, a.eval, tmp)
        print("    " + summarize(r), flush=True)
        score = r["paired_diff"].mean
        if score > best_score:
            best_score, best_pol = score, pol
            best_pol.save(Path(a.out))
            print(f"    -> mentve ({score:+.3f})", flush=True)
    if a.tune and best_pol is not None:
        print("[hangolás] döntési küszöbök", flush=True)
        tuned, _ = tune_params(best_pol, a.tune, tmp)
        tuned.save(Path(a.out))
        print(f"    küszöbök: {tuned.params}", flush=True)
    tmp.unlink(missing_ok=True)
    print("[kész] mentve:", a.out)


if __name__ == "__main__":
    main()

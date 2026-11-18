"""
league.py - Liga-tanítás: a licit/kontra politika NEM csak a JS-bot ellen tanul, hanem egy ligában,
ahol a másik két ülésen a JS-bot és a korábbi generációk (a bajnok és elődei) ülnek.

Körönként:
  1. Adat: teljes licitmenetű leosztások, ahol minden ülésre a liga egy tagja kerül (súlyozva). A felvevő és a két védő
     nézete is rögzül (a kijátszást a playbot adja, a kontra nem befolyásolja, ezért a kontra nélküli kijátszás elég).
  2. Illesztés: a bajnok súlyai felett csak a megfelelő számú sorral rendelkező modellek cserélődnek. A paraméterek
     (küszöbök) és a talon-modell a bajnoké marad.
  3. Kapu: a jelölt csak akkor lesz az új bajnok, ha a PÁROS mérésen (tanításban nem látott leosztások) a
     bajnok ellen NEM veszít szignifikánsan, ÉS a JS ellen sem romlik. Egyébként elvetjük (őszinte negatív eredmény is eredmény).
A kimenetek: learned_league.json (a legjobb jelölt), league_results.json (minden mérés). A learned_policy.json-hoz nem nyúl.

Használat:  python league.py --rounds 3 --deals 6000 --eval 300
"""
from __future__ import annotations

from ulti.paths import RESEARCH, WORK
import argparse
import functools
import json
import random
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import arena
from features import hand_features
from learn import MIN_ROWS, _pick_random, fit_logistic, fit_ridge
from playbot import bot_choose
from policy import DEFAULT_PATH, LearnedPolicy, ctx_of, make_learned, model_key
from rules import Game

WORKERS = 3
HERE = Path(__file__).parent
OUT_POLICY = RESEARCH / "learned_league.json"
OUT_RESULTS = RESEARCH / "league_results.json"
GEN_DIR = WORK / "league_gens"
TRAIN_SEED0 = 20_000_000
EVAL_SEED0 = 30_000_000      # a tanításban és a kapu mérésben nem látott leosztások (végső jelentés)
GATE_SEED0 = 25_000_000      # a kapu-mérés leosztásai

Data = Dict[str, Tuple[np.ndarray, np.ndarray]]
PoolSpec = List[Tuple[str, Optional[dict], float]]    # (név, politika-json | None=JS, súly)


# ---------------------------------------------------------------- adatgyűjtés
def _build_pool(spec: PoolSpec):
    out = []
    for name, js, w in spec:
        pol = arena.JsPolicy() if js is None else LearnedPolicy.from_json(js)
        out.append((name, pol, w))
    return out


def _worker(args) -> Dict[str, Tuple[list, list]]:
    spec, seeds, eps = args
    pool = _build_pool(spec)
    weights = [w for _, _, w in pool]
    out: Dict[str, Tuple[list, list]] = {}

    def add(key: str, x: np.ndarray, y: float) -> None:
        d = out.setdefault(key, ([], []))
        d[0].append(x)
        d[1].append(y)

    for sd in seeds:
        rng = random.Random(sd)
        g = Game.new_deal(sd % 3, rng)
        seats = [rng.choices(pool, weights)[0][1] for _ in range(3)]
        first = True
        guard = 0
        while g.phase == "bid" and guard < 80:
            guard += 1
            p = g.turn
            if first and rng.random() < eps:
                ci, su, disc = _pick_random(g, rng)
                g.do_bid(p, disc, ci, su)
            else:
                a = seats[p].bid_action(g, p, rng)
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
        decl, talon, ci = g.decl, tuple(g.talon), g.ci
        trump_f = g.trump if g.trump_game else -1
        ctx = ctx_of(ci)
        xd = np.concatenate([hand_features(g.hands[decl], trump_f, talon), ctx])
        xdef = [np.concatenate([hand_features(g.hands[q], trump_f, ()), ctx]) for q in range(3) if q != decl]
        while g.phase == "kontra":
            g.do_kontra(g.turn, [])
        while g.phase == "play":
            if len(g.trick) == 3:
                g.collect_trick()
                continue
            q = g.turn
            g.play_card(q, bot_choose(g, q))
        rows = {r["k"]: r for r in g.result["rows"] if r["k"]}
        add("silent", xd, float(sum(r["delta"] for r in g.result["rows"] if not r["k"])))
        for k, r in rows.items():
            win = 1.0 if r["win"] else 0.0
            mk = model_key(ci, k)
            add(mk, xd, win)
            for x in xdef:
                add("d_" + mk, x, win)
    return out


def gather(spec: PoolSpec, seed0: int, n: int, eps: float, workers: int = WORKERS) -> Data:
    seeds = list(range(seed0, seed0 + n))
    chunks = [(spec, seeds[i::workers * 4], eps) for i in range(workers * 4)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(_worker, chunks))
    merged: Dict[str, List[Tuple[list, list]]] = {}
    for part in parts:
        for k, v in part.items():
            merged.setdefault(k, []).append(v)
    return {k: (np.array([x for v in vs for x in v[0]]), np.array([y for v in vs for y in v[1]])) for k, vs in merged.items()}


def merge(a: Data, b: Data) -> Data:
    out = dict(a)
    for k, (X, y) in b.items():
        out[k] = (np.vstack([out[k][0], X]), np.concatenate([out[k][1], y])) if k in out else (X, y)
    return out


# ---------------------------------------------------------------- illesztés
def refit(champ: LearnedPolicy, data: Data, lam: float = 3.0, blend: float = 1.0) -> LearnedPolicy:
    """blend: 1 = teljesen az új illesztés, 0 = a bajnok súlyai (köztes: lineáris keverés)."""
    w = {k: v.copy() for k, v in champ.w.items()}
    for key, (X, y) in data.items():
        if len(y) < MIN_ROWS or key not in w:
            continue
        new = fit_ridge(X, y) if key == "silent" else fit_logistic(X, y, lam=lam)
        w[key] = (1.0 - blend) * w[key] + blend * new
    return LearnedPolicy(w, dict(champ.params), champ.talon)


# ---------------------------------------------------------------- mérés
def _maker(path: Optional[str]):
    return arena.make_js if path is None else functools.partial(make_learned, path)


def measure(test_path: str, base_path: Optional[str], deals: int, seed0: int) -> Dict[str, Any]:
    """Páros mérés: a test ülés pénze a base ülés pénzéhez képest (a másik két ülés mindig a base)."""
    r = arena.evaluate(_maker(test_path), deals=deals, seed0=seed0, workers=WORKERS, base_maker=_maker(base_path))
    d = r["paired_diff"]
    return {"n": r["n"], "mean": round(d.mean, 4), "ci95": round(1.96 * d.se, 4),
            "declares_test": round(r["declares_test"], 3), "declares_base": round(r["declares_base"], 3),
            "concede": round(r["concede_rate_test"], 4)}


def significantly_worse(m: Dict[str, Any]) -> bool:
    return m["mean"] + m["ci95"] < 0


def save_data(data: Data) -> None:
    flat = {}
    for k, (X, y) in data.items():
        flat["X__" + k] = X
        flat["y__" + k] = y
    np.savez_compressed(GEN_DIR / "data.npz", **flat)


def load_data() -> Data:
    f = GEN_DIR / "data.npz"
    if not f.exists():
        return {}
    z = np.load(f)
    return {k[3:]: (z[k], z["y__" + k[3:]]) for k in z.files if k.startswith("X__")}


def load_results() -> dict:
    if OUT_RESULTS.exists():
        return json.loads(OUT_RESULTS.read_text(encoding="utf-8"))
    return {"rounds": [], "final": {}, "config": {}}


def save_results(res: dict) -> None:
    OUT_RESULTS.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")


def pool_spec(champ: LearnedPolicy, gens: Sequence[Tuple[str, LearnedPolicy]], js_w: float, champ_w: float) -> PoolSpec:
    spec: PoolSpec = [("js", None, js_w), ("champion", champ.to_json(), champ_w)]
    for name, p in gens:
        spec.append((name, p.to_json(), 1.0))
    return spec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", default=str(DEFAULT_PATH))
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--deals", type=int, default=6000, help="tanító leosztás körönként")
    ap.add_argument("--eval", type=int, default=300, help="kapu-mérés leosztásai (x3 ülés)")
    ap.add_argument("--eps", type=float, default=0.15)
    ap.add_argument("--js-weight", type=float, default=2.0)
    ap.add_argument("--champ-weight", type=float, default=2.0)
    ap.add_argument("--lam", type=float, default=3.0)
    ap.add_argument("--blend", type=float, default=1.0)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    res = load_results() if a.resume else {"rounds": [], "final": {}, "config": {}}
    res["config"] = vars(a) | {"workers": WORKERS}
    start = Path(a.init)
    champ_path = GEN_DIR / "champion.json"
    if not (a.resume and champ_path.exists()):
        champ_path.write_text(start.read_text(encoding="utf-8"), encoding="utf-8")
    champ = LearnedPolicy.load(champ_path)
    if not (GEN_DIR / "gen0.json").exists():
        LearnedPolicy.load(start).save(GEN_DIR / "gen0.json")
    gens: List[Tuple[str, LearnedPolicy]] = [("gen0", LearnedPolicy.load(GEN_DIR / "gen0.json"))]
    data: Data = {}
    if a.resume:
        data = load_data()
        gens = [(f.stem, LearnedPolicy.load(f)) for f in sorted(GEN_DIR.glob("gen*.json"))] or gens
    for rnd in range(len(res["rounds"]), a.rounds):
        t0 = time.time()
        spec = pool_spec(champ, gens, a.js_weight, a.champ_weight)
        new = gather(spec, TRAIN_SEED0 + rnd * a.deals, a.deals, a.eps)
        data = merge(data, new)
        cand = refit(champ, data, a.lam, a.blend)
        cpath = GEN_DIR / f"cand_{rnd + 1}.json"
        cand.save(cpath)
        n_rows = sum(len(v[1]) for v in data.values())
        t1 = time.time()
        vs_champ = measure(str(cpath), str(champ_path), a.eval, GATE_SEED0)
        vs_js = measure(str(cpath), None, a.eval, GATE_SEED0 + 100_000)
        base_js = measure(str(champ_path), None, a.eval, GATE_SEED0 + 100_000)
        accept = (not significantly_worse(vs_champ)) and vs_champ["mean"] >= 0 and vs_js["mean"] >= base_js["mean"] - 0.2
        rec = {"round": rnd + 1, "rows": n_rows, "gather_s": round(t1 - t0), "eval_s": round(time.time() - t1),
               "cand_vs_champion": vs_champ, "cand_vs_js": vs_js, "champion_vs_js": base_js, "accepted": accept}
        res["rounds"].append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        if accept:
            gens.append((f"gen{rnd + 1}", champ))
            champ.save(GEN_DIR / f"gen{rnd + 1}.json")
            champ = cand
            cand.save(champ_path)
            cand.save(OUT_POLICY)
        save_results(res)
        save_data(data)
    print("[kész]", flush=True)


if __name__ == "__main__":
    main()

"""
book_policy.py - LearnedPolicy + könyvi jellemzők (book_features.py), és a mérésük (A/B) önjáték-tanítással.

A BookPolicy a LearnedPolicy minden döntési logikáját megtartja (licit, talon, rablás, bedobás, kontra);
az egyetlen különbség, hogy a komponens-modellek a 24 alap jellemző mellett a könyvből származó NB jellemzőt is kapnak
(a "book_mask" által bekapcsolt csoportokból). A súlyokat a szimuláció (valódi szabály, valódi pénz) tanulja, a könyv
csak azt mondja meg, MIRE figyeljen a modell.

A meglévő learned_policy.json-t nem módosítja. Az induló paraméterek és a talon-modell onnan jönnek (másolatként).

Használat (mind kis adagokban futtatható):
    python book_policy.py gather <leosztás> [seed0]            # a közös, véletlen licitű tanítóadat (/tmp/bf/random.npz)
    python book_policy.py train <változat> [kör] [leosztás]    # egy változat tanítása (/tmp/bf/<változat>.json)
    python book_policy.py eval <teszt.json> <alap.json|js> <leosztás> <seed0> [kimenet.json]
Változatok: base (nincs könyvi jellemző) | all | talon | szerkezet | ulti40_20 | betli | durch
"""
from __future__ import annotations

from ulti.paths import RESEARCH
import functools
import json
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import arena
from book_features import BOOK_NAMES, GROUPS, NB, book_vector, group_mask
from features import N_FEATURES, hand_features
from learn import fit_logistic, fit_ridge, MIN_ROWS
from playbot import bot_choose
from policy import (CTX_NAMES, DEFAULT_PATH, DIM, MODEL_KEYS, N_CTX, TRUMP_IDX, LearnedPolicy, ctx_of, model_key,
                    sigmoid, zero_weights, SILENT_CLIP)
from rules import CONTRACTS, Game, suits_for
from talon import TalonModel

WORKERS = 3
TMP = Path("/tmp/bf")
BOOK_DEFAULT_PATH = RESEARCH / "learned_bookfeat.json"
TRAIN_SEED0 = 100_000


def _bw_zero() -> Dict[str, np.ndarray]:
    return {k: np.zeros(NB) for k in zero_weights()}


class BookPolicy(LearnedPolicy):
    """LearnedPolicy + könyvi jellemzők. A súlyok: self.w (alap + környezet, mint eddig) és self.wb (könyvi, NB)."""
    name = "learned+book"

    def __init__(self, weights=None, params=None, talon=None, bweights=None, groups: Sequence[str] = ()):
        super().__init__(weights, params, talon)
        self.wb = bweights if bweights is not None else _bw_zero()
        self.groups = list(groups)
        self.mask = np.array(group_mask(self.groups))

    # ---- mentés / betöltés
    def to_json(self) -> dict:
        d = super().to_json()
        d["book_weights"] = {k: [float(x) for x in v] for k, v in self.wb.items()}
        d["book_groups"] = self.groups
        d["book_names"] = BOOK_NAMES
        return d

    @classmethod
    def from_json(cls, d: dict) -> "BookPolicy":
        base = LearnedPolicy.from_json(d)
        wb = _bw_zero()
        names = d.get("book_names", BOOK_NAMES)
        for k, v in d.get("book_weights", {}).items():
            if k in wb:
                for i, nm in enumerate(names):
                    if nm in BOOK_NAMES and i < len(v):
                        wb[k][BOOK_NAMES.index(nm)] = v[i]
        return cls(base.w, base.params, base.talon, wb, d.get("book_groups", []))

    @classmethod
    def load(cls, path: Path = BOOK_DEFAULT_PATH) -> "BookPolicy":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    # ---- értékelés (a LearnedPolicy többi metódusa ezekre épül)
    def _bvec(self, h, trump: int, disc) -> np.ndarray:
        return np.array(book_vector(h, trump, disc)) * self.mask

    def _rows(self, cards: List[int], discard: bool):
        cards = list(cards)
        n = len(cards)
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)] if discard else [None]
        F = np.zeros((len(TRUMP_IDX), len(pairs), N_FEATURES + NB))
        has7 = np.zeros((len(TRUMP_IDX), len(pairs)), dtype=bool)
        for pi, pr in enumerate(pairs):
            if pr is None:
                rest, disc = cards, ()
            else:
                rest = [c for q, c in enumerate(cards) if q != pr[0] and q != pr[1]]
                disc = (cards[pr[0]], cards[pr[1]])
            rs = set(rest)
            for ti, t in enumerate(TRUMP_IDX):
                F[ti, pi, :N_FEATURES] = hand_features(rest, t, disc)
                F[ti, pi, N_FEATURES:] = self._bvec(rest, t, disc)
                has7[ti, pi] = t >= 0 and (t * 8) in rs
        return cards, pairs, F, has7

    def _lin(self, key: str, F: np.ndarray, ctx: np.ndarray) -> np.ndarray:
        w = self.w[key]
        return F[:, :N_FEATURES] @ w[:N_FEATURES] + F[:, N_FEATURES:] @ self.wb[key] + ctx @ w[N_FEATURES:]

    def _ev_contract(self, ci: int, F_t: np.ndarray, has7_t: np.ndarray) -> np.ndarray:
        c = CONTRACTS[ci]
        ctx = ctx_of(ci)
        fm = self.params["fail_mult"]
        ev = np.zeros(F_t.shape[0])
        for k, v in c["comps"].items():
            p = sigmoid(self._lin(model_key(ci, k), F_t, ctx))
            loss = v * (2 if k == "ulti" else 1) * fm
            ev += p * v - (1.0 - p) * loss
        ev += np.clip(self._lin("silent", F_t, ctx), -SILENT_CLIP, SILENT_CLIP)
        if "ulti" in c["comps"]:
            ev = np.where(has7_t, ev, -1e9)
        return ev

    def _vec(self, hand, trump: int, disc) -> np.ndarray:
        return np.concatenate([hand_features(hand, trump, disc), self._bvec(hand, trump, disc)])[None, :]

    def hand_value(self, hand: List[int], ci: int, trump: int, disc: Tuple[int, ...] = ()) -> float:
        has7 = np.array([trump >= 0 and (trump * 8) in set(hand)])
        return float(self._ev_contract(ci, self._vec(hand, trump, disc), has7)[0])

    def p_success(self, hand: List[int], ci: int, trump: int, k: str, defender: bool, disc: Tuple[int, ...] = ()) -> float:
        key = ("d_" if defender else "") + model_key(ci, k)
        x = self._vec(hand, trump, () if defender else disc)
        return float(sigmoid(self._lin(key, x, ctx_of(ci)))[0])


_BCACHE: Dict[str, BookPolicy] = {}


def make_book(path: str = str(BOOK_DEFAULT_PATH)) -> BookPolicy:
    if path not in _BCACHE:
        _BCACHE[path] = BookPolicy.load(Path(path))
    return _BCACHE[path]


# ---------------------------------------------------------------- tanítás
def _gen_worker(args):
    """Önjáték: adat a (vállalás, adu, talon) döntésekből és a tényleges teljesülésből. X = [alap(24), könyvi(NB), ctx]."""
    pol_path, seeds, eps = args
    pol = BookPolicy.load(Path(pol_path)) if pol_path else None
    out: Dict[str, list] = {}

    def add(key: str, x: np.ndarray, y: float) -> None:
        d = out.setdefault(key, [[], []])
        d[0].append(x)
        d[1].append(y)

    from learn import _pick_random
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

        def vec(q: int, disc_: Sequence[int]) -> np.ndarray:
            return np.concatenate([hand_features(start[q], trump_f, disc_), book_vector(start[q], trump_f, disc_), ctx])

        xd = vec(decl, talon)
        add("silent", xd, float(silent))
        xdef = {q: vec(q, ()) for q in range(3) if q != decl}
        for k in CONTRACTS[ci]["comps"]:
            win = 1.0 if rows[k]["win"] else 0.0
            add(model_key(ci, k), xd, win)
            for q, xq in xdef.items():
                add("d_" + model_key(ci, k), xq, win)
    return {k: (np.array(v[0]), np.array(v[1])) for k, v in out.items()}


def gather(pol_path: Optional[str], seed0: int, n: int, eps: float) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    seeds = list(range(seed0, seed0 + n))
    chunks = [(pol_path, seeds[i::WORKERS * 4], eps) for i in range(WORKERS * 4)]
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        parts = list(ex.map(_gen_worker, chunks))
    merged: Dict[str, list] = {}
    for part in parts:
        for k, (X, y) in part.items():
            merged.setdefault(k, []).append((X, y))
    return {k: (np.vstack([a for a, _ in v]), np.concatenate([b for _, b in v])) for k, v in merged.items()}


def _cat(a, b):
    out = dict(a)
    for k, (X, y) in b.items():
        out[k] = (np.vstack([out[k][0], X]), np.concatenate([out[k][1], y])) if k in out else (X, y)
    return out


def fit_book_policy(data: Dict[str, Tuple[np.ndarray, np.ndarray]], groups: Sequence[str], ref: LearnedPolicy) -> BookPolicy:
    """Illesztés. A (ref) politikától csak a paramétereket és a talon-modellt vesszük át."""
    mask = np.array(group_mask(groups))
    w = zero_weights()
    for k in MODEL_KEYS:
        w[k][0] = -2.0
    wb = _bw_zero()
    for key, (X, y) in data.items():
        if len(y) < MIN_ROWS:
            continue
        X = X.copy()
        X[:, N_FEATURES:N_FEATURES + NB] *= mask
        v = fit_ridge(X, y) if key == "silent" else fit_logistic(X, y)
        w[key] = np.concatenate([v[:N_FEATURES], v[N_FEATURES + NB:]])
        wb[key] = v[N_FEATURES:N_FEATURES + NB] * mask
    return BookPolicy(w, dict(ref.params), ref.talon, wb, groups)


def variant_groups(name: str) -> List[str]:
    if name == "base":
        return []
    if name == "all":
        return list(GROUPS)
    if name in GROUPS:
        return [name]
    raise SystemExit(f"ismeretlen változat: {name}")


def cmd_gather(n: int, seed0: int) -> None:
    t = time.time()
    d = gather(None, seed0, n, 1.0)
    np.savez_compressed(TMP / "random.npz", **{f"X|{k}": v[0] for k, v in d.items()}, **{f"y|{k}": v[1] for k, v in d.items()})
    print(f"véletlen adat: {sum(len(v[1]) for v in d.values())} sor, {time.time() - t:.0f} s", flush=True)


def load_random() -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    z = np.load(TMP / "random.npz")
    return {k[2:]: (z[k], z["y|" + k[2:]]) for k in z.files if k.startswith("X|")}


def cmd_train(name: str, rounds: int, deals: int, seed0: int) -> None:
    """0. kör: a közös véletlen adat; a további körök: a változat SAJÁT politikájával (on-policy, eps=0.35)."""
    t0 = time.time()
    ref = LearnedPolicy.load(DEFAULT_PATH)
    groups = variant_groups(name)
    data = load_random()
    pol = fit_book_policy(data, groups, ref)
    out = TMP / f"{name}.json"
    for r in range(rounds):
        pol.save(out)
        new = gather(str(out), seed0 + r * deals, deals, 0.35)
        data = _cat(data, new)
        pol = fit_book_policy(data, groups, ref)
        print(f"  [{name}] kör {r + 1}: {sum(len(v[1]) for v in data.values())} sor, {time.time() - t0:.0f} s", flush=True)
    pol.save(out)
    print(f"[{name}] kész: {out}", flush=True)


def _maker_for(path: str):
    if path == "js":
        return arena.make_js
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    return functools.partial(make_book, path) if "book_weights" in p else functools.partial(_make_plain, path)


def _make_plain(path: str):
    from policy import make_learned
    return make_learned(path)


def cmd_eval(test: str, base: str, deals: int, seed0: int, out: Optional[str]) -> None:
    t = time.time()
    r = arena.evaluate(_maker_for(test), deals=deals, seed0=seed0, workers=WORKERS, base_maker=_maker_for(base))
    d = r["paired_diff"]
    print(f"{test} vs {base} (seed {seed0}+{deals}, n={r['n']}): páros különbség {d} | tanult {r['test_money']} alap {r['base_money']} "
          f"| felvevő {r['declares_test']:.2f}/{r['declares_base']:.2f} | bedobás {r['concede_rate_test']:.3f} | {time.time() - t:.0f} s", flush=True)
    if out:
        p = Path(out)
        rows = json.loads(p.read_text()) if p.exists() else []
        rows.append({"test": test, "base": base, "deals": deals, "seed0": seed0, "n": r["n"], "mean": d.mean, "ci95": 1.96 * d.se,
                     "test_money": r["test_money"].mean, "base_money": r["base_money"].mean,
                     "declares_test": r["declares_test"], "declares_base": r["declares_base"]})
        p.write_text(json.dumps(rows, indent=1))


def cmd_rows(test: str, deals: int, seed0: int, out: str) -> None:
    """A vizsgált politika a JS ellen; soronként (leosztás x ülés) a páros pénzkülönbség -> npy. Így két változat
    UGYANAZON a mintán, soronként párosítva hasonlítható (a leosztás-szórás kiesik)."""
    t = time.time()
    maker, bm = _maker_for(test), arena.make_js
    jobs = [(maker, s, seed0 + i, bm) for i in range(deals) for s in range(3)]
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        rows = list(ex.map(arena._one, jobs, chunksize=max(1, len(jobs) // (WORKERS * 8))))
    diff = np.array([r["test"] - r["base"] for r in rows], dtype=float)
    decl = np.array([r["decl_test"] for r in rows], dtype=float)
    np.savez(out, diff=diff, decl=decl)
    se = diff.std(ddof=1) / len(diff) ** 0.5
    print(f"{test} vs js (seed {seed0}+{deals}): {diff.mean():+.3f} ± {1.96 * se:.3f} | felvevő {decl.mean():.2f} | {time.time() - t:.0f} s", flush=True)


def main() -> None:
    a = sys.argv[1:]
    TMP.mkdir(exist_ok=True)
    if a and a[0] == "gather":
        cmd_gather(int(a[1]), int(a[2]) if len(a) > 2 else TRAIN_SEED0 + 5_000_000)
    elif a and a[0] == "train":
        cmd_train(a[1], int(a[2]) if len(a) > 2 else 2, int(a[3]) if len(a) > 3 else 4000, TRAIN_SEED0 + 6_000_000)
    elif a and a[0] == "rows":
        cmd_rows(a[1], int(a[2]), int(a[3]), a[4])
    elif a and a[0] == "eval":
        cmd_eval(a[1], a[2], int(a[3]), int(a[4]), a[5] if len(a) > 5 else None)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()

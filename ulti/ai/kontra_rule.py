"""
kontra_rule.py - Védői kontra / felvevői rekontra VÁRHATÓ-PÉNZ szabállyal (nem küszöb-alapú).

A jelenlegi LearnedPolicy.kontra küszöbös (p_success < kt). A fizetés aszimmetrikus (kontrával a tét duplázódik,
rekontrával négyszereződik, az ulti bukása kétszeres), ezért a helyes szabály: kontrázz, ha
    E[pénz | kontra] > E[pénz | nincs kontra].
Védőként a felvevő rekontrája (a felvevő a saját lapját ismeri) káros szelekció: ha rekontráz, a felvevő jellemzően nyer.
Ezt egy kis, szimulált adatból illesztett modell kezeli (komponensenként: q = P(rekontra | kontra), fr / fn = a felvevő
bukásának esélye rekontrával / anélkül, a védői p_success logitjából és a kontra-sorrendből).

Használat (egy sorral beköthető):
    from kontra_rule import make_kontra_policy
    pol = make_kontra_policy()                      # learned_policy.json + kontra_rule.json
Csak a kontra() változik, a licit/kijátszás a LearnedPolicy-é.

Újraillesztés:  python kontra_rule.py collect <seed0> <leosztás> <ki.pkl>   (4 worker)
                python kontra_rule.py fit <ki.pkl> [...] --recal <recal_politika.json>
"""
from __future__ import annotations

from ulti.paths import POLICY as POLICY_DIR
import json
import math
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from features import N_FEATURES, hand_features
from policy import DEFAULT_PATH, DIM, LearnedPolicy, ctx_of, sigmoid, zero_weights
from rules import CONTRACTS, Game

RULE_PATH = POLICY_DIR / "kontra_rule.json"
MODELED = ("parti", "c40", "ulti", "betli")     # ezekre van illesztett modell; a többi komponens a régi küszöbös szabályt kapja
_MODEL_CACHE: Dict[str, dict] = {}
_POL_CACHE: Dict[tuple, "ExpectedMoneyKontra"] = {}


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1.0 - 1e-3)
    return math.log(p / (1.0 - p))


def _sg(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def _dot(w: List[float], x: List[float]) -> float:
    return sum(a * b for a, b in zip(w, x))


def _util(v: float, ulti: int, m: int, f: float) -> float:
    """Egy védő várható pénze m szorzónál, ha a felvevő f eséllyel bukik (ulti: a bukás (m+1)-szeres)."""
    return v * (m * (2.0 * f - 1.0) + ulti * f)


def load_model(path: Path = RULE_PATH) -> dict:
    key = str(path)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = json.loads(Path(path).read_text(encoding="utf-8"))
    return _MODEL_CACHE[key]


class ExpectedMoneyKontra(LearnedPolicy):
    """LearnedPolicy, amelynek kontra()-ja várható-pénz alapú. Minden más (licit, talon, kijátszás) változatlan.

    mode (védő):   "ev"        illesztett modell (rekontra-várakozással), kontra ha a várható nyereség > margin
                   "analytic"  rekontra nélküli képlet: kontra ha v*(2f-1) > margin (f = 1 - p_success)
                   "thr"       komponensenkénti küszöb a kalibrált p-n (c variáns)
                   "base"      a régi küszöbös szabály
    rek (felvevő): "base" (alapértelmezés, a mért legjobb: a régi küszöbös rekontra) | "ev" (rekontra ha a várható nyereség > rek_margin; mérve nem jobb)
    src: "recal" (a kalibrált d_* súlyok a kontra_rule.json-ból) | "raw" (a politika saját d_* súlyai)
    """
    name = "learned+kontra-ev"

    def __init__(self, base: LearnedPolicy, model: dict, mode: str = "ev", rek: str = "base", src: str = "recal",
                 margin: float = 0.0, rek_margin: float = 0.0, only: Optional[tuple] = None,
                 sim_n: int = 0, sim_comps: tuple = ("betli",), sim_w: float = 1.0):
        super().__init__(base.w, base.params, base.talon)
        self.model = model
        self.mode, self.rek, self.src = mode, rek, src
        self.margin, self.rek_margin = float(margin), float(rek_margin)
        self.only = tuple(only) if only else None          # ha meg van adva: a védői új szabály csak ezekre a komponensekre él
        self.sim_n, self.sim_comps, self.sim_w = int(sim_n), tuple(sim_comps), float(sim_w)
        self._plain = LearnedPolicy(base.w, base.params, base.talon)      # a kontra() nélküli (régi) viselkedés a szimulációkhoz
        self.dw: Dict[str, np.ndarray] = {}
        for key, v in model["recal_weights"].items():
            a = np.array(v, dtype=float)
            w = np.zeros(DIM)
            w[:min(len(a), DIM)] = a[:DIM]
            self.dw[key] = w

    # ---- a sikerbecslés ----
    def _p_def(self, hand: List[int], ci: int, trump: int, k: str) -> float:
        if self.src == "raw":
            return self.p_success(hand, ci, trump, k, True)
        from policy import model_key
        w = self.dw["d_" + model_key(ci, k)]
        x = np.array(hand_features(hand, trump, ()))
        return float(sigmoid(x @ w[:N_FEATURES] + ctx_of(ci) @ w[N_FEATURES:]))

    # ---- védői döntés ----
    def defender_gain(self, g: Game, p: int, k: str, trump: int) -> Optional[float]:
        """A kontra várható pénz-többlete a nem-kontrához képest (None: nincs modell az adott komponensre)."""
        if k not in MODELED:
            return None
        pp = self._p_def(g.hands[p], g.ci, trump, k)
        v = CONTRACTS[g.ci]["comps"][k]
        u = 1 if k == "ulti" else 0
        if self.mode == "analytic":
            return _util(v, u, 2, 1.0 - pp) - _util(v, u, 1, 1.0 - pp)
        m = self.model["def_ev"][self.src].get(k)
        if m is None:
            return None
        x = [1.0, _logit(pp), 1.0 if g.kidx == 0 else 0.0]
        q, fr, fn = _sg(_dot(m["q"], x)), _sg(_dot(m["fr"], x)), _sg(_dot(m["fn"], x))
        fo = (1.0 - q) * fn + q * fr
        return (1.0 - q) * _util(v, u, 2, fn) + q * _util(v, u, 4, fr) - _util(v, u, 1, fo)

    def sim_gain(self, g: Game, p: int, k: str) -> float:
        """Szimulációs becslés: a védő kezéhez a felvevő feltételes világait (tutor._worlds_for) mintázza, és ugyanazokon a
        világokon, ugyanazzal a véletlennel lejátssza a kontrával és anélkül; a pénz átlag-különbsége (kontra - nincs).
        A véletlen a helyzetből származik (determinista, és nem fogyasztja a hívó rng-jét)."""
        import arena
        import tutor
        seed = hash((tuple(sorted(g.hands[p])), g.ci, g.kidx, k, g.decl)) & 0x7FFFFFFF
        rng = random.Random(seed)
        ws, seeds = tutor._worlds_for(g, p, self.sim_n, rng, self._plain)
        tot = 0.0
        for w, sd in zip(ws, seeds):
            res = []
            for ks in ([], [k]):
                x = w.clone()
                x.do_kontra(p, ks)
                arena.run_deal(x, [self._plain] * 3, random.Random(sd))
                res.append(x.result["change"][p])
            tot += res[1] - res[0]
        return tot / max(1, len(ws))

    def _defend(self, g: Game, p: int, k: str, trump: int) -> Optional[bool]:
        if self.mode == "base":
            return None
        if self.mode == "thr":
            t = self.model["thr"].get(k)
            return None if t is None or k not in MODELED else self._p_def(g.hands[p], g.ci, trump, k) < t
        gain = self.defender_gain(g, p, k, trump)
        if gain is None:
            return None
        if self.sim_n > 0 and k in self.sim_comps:
            gain += self.sim_w * self.sim_gain(g, p, k)
        return gain > self.margin

    # ---- felvevői rekontra ----
    def rekontra_gain(self, g: Game, p: int, k: str, trump: int) -> Optional[float]:
        if k not in MODELED or k not in self.model["decl_ev"]:
            return None
        pr = self.p_success(g.hands[p], g.ci, trump, k, False, tuple(g.talon))
        f = _sg(_dot(self.model["decl_ev"][k]["f"], [1.0, _logit(pr)]))      # a felvevő bukásának esélye
        n = sum(1 for q in range(3) if q != g.decl and g.kmult[q].get(k) == 2)
        v = CONTRACTS[g.ci]["comps"][k]
        return 2.0 * v * n * (1.0 - 2.0 * f)                                # E(4) - E(2), a felvevő szemszögéből

    def _rekontra(self, g: Game, p: int, k: str, trump: int) -> Optional[bool]:
        if self.rek != "ev":
            return None
        gain = self.rekontra_gain(g, p, k, trump)
        return None if gain is None else gain > self.rek_margin

    def kontra(self, g: Game, p: int, rng: random.Random) -> List[str]:
        opts = g.kontra_options(p)
        if not opts:
            return []
        old = set(LearnedPolicy.kontra(self, g, p, rng))
        trump = g.trump if g.trump_game else -1
        out: List[str] = []
        for k in opts:
            if p != g.decl and g.must_kontra(p, k):
                out.append(k)                                              # a szabály kényszere (a Game.do_kontra is hozzáadná)
                continue
            if p == g.decl:
                d = self._rekontra(g, p, k, trump)
            else:
                d = None if (self.only and k not in self.only) else self._defend(g, p, k, trump)
            if (k in old) if d is None else d:
                out.append(k)
        return out


def make_kontra_policy(path: str = str(DEFAULT_PATH), model_path: str = str(RULE_PATH), **kw) -> ExpectedMoneyKontra:
    """Folyamatonként egyszer betölti: learned_policy.json (licit, kijátszás) + kontra_rule.json (kontra-modell)."""
    key = (path, model_path, tuple(sorted(kw.items())))
    if key not in _POL_CACHE:
        _POL_CACHE[key] = ExpectedMoneyKontra(LearnedPolicy.load(Path(path)), load_model(Path(model_path)), **kw)
    return _POL_CACHE[key]


def default_policy(path: str = str(DEFAULT_PATH)) -> LearnedPolicy:
    """Az éles (szerver és tanító bot) politika: a tanult politika + a várható-pénz kontra. Egyetlen belépési pont, hogy a
    játék és az elemzés ugyanazt a szabályt használja. ULTI_KONTRA=threshold a régi küszöbös kontrára kapcsol; a könyv díjaival
    (rules.RULESET == "book") a kontra-modell nem érvényes (a friend díjtáblán illesztettük), ott a sima politika marad."""
    import os

    import rules
    if os.getenv("ULTI_KONTRA", "em") == "threshold" or rules.RULESET != "friend":
        from policy import make_learned
        return make_learned(path)
    return make_kontra_policy(path)


# ====================================================================== illesztés (offline)
class _Rec:
    """Az alappolitikát játssza, a kontra-döntések helyén (klón, ülés, alap-döntés) pillanatképet készít."""
    name = "rec"

    def __init__(self, base: LearnedPolicy):
        self.base, self.snaps = base, []

    def bid_action(self, g, p, rng):
        return self.base.bid_action(g, p, rng)

    def kontra(self, g, p, rng):
        out = self.base.kontra(g, p, rng)
        self.snaps.append((g.clone(), p, tuple(out)))
        return out

    def card(self, g, p, rng):
        return self.base.card(g, p, rng)


def _replay(snap: Game, p: int, ks: List[str], base, seed: int):
    import arena
    g = snap.clone()
    g.do_kontra(p, list(ks))
    arena.run_deal(g, [base] * 3, random.Random(seed * 7919 + 13))
    return g


def _collect_worker(args) -> list:
    path, recal_path, seeds = args
    import arena
    base = LearnedPolicy.load(Path(path))
    rec_pol = LearnedPolicy.load(Path(recal_path))
    rows = []
    for sd in seeds:
        r = _Rec(base)
        g = Game.new_deal(sd % 3, random.Random(sd))
        arena.run_deal(g, [r, r, r], random.Random(sd * 7919 + 13))
        for s0, p, bout in r.snaps:
            trump = s0.trump if s0.trump_game else -1
            decl = p == s0.decl
            opts = [k for k in s0.kontra_options(p) if not s0.must_kontra(p, k)]
            if not opts:
                continue
            m0 = _replay(s0, p, [], base, sd).result["change"][p]
            for k in opts:
                g1 = _replay(s0, p, [k], base, sd)
                win = {x["k"]: x["win"] for x in g1.result["rows"] if x["k"]}.get(k)
                dk = [ks for q, ks in g1.klog[len(s0.klog):] if q == s0.decl]
                tal = tuple(s0.talon) if decl else ()
                rows.append(dict(seed=sd, ci=s0.ci, k=k, v=CONTRACTS[s0.ci]["comps"][k], decl=decl, pos=s0.kidx,
                                 p_raw=base.p_success(s0.hands[p], s0.ci, trump, k, not decl, tal),
                                 p_rec=rec_pol.p_success(s0.hands[p], s0.ci, trump, k, not decl, tal),
                                 d=g1.result["change"][p] - m0, win=win, base=(k in bout),
                                 rek=None if decl else bool(dk and k in dk[-1])))
    return rows


def collect(seed0: int, n: int, out: str, path: str = str(DEFAULT_PATH), recal_path: str = "/tmp/lp_recal_keep.json",
            workers: int = 4) -> int:
    """Kontra-pillanatképek: minden (állapot, komponens)-re a kontra pénz-hatása (d) a nem-kontrához képest."""
    import pickle
    from concurrent.futures import ProcessPoolExecutor
    seeds = list(range(seed0, seed0 + n))
    chunks = [(path, recal_path, seeds[i::workers * 6]) for i in range(workers * 6)]
    with ProcessPoolExecutor(workers) as ex:
        rows = [x for part in ex.map(_collect_worker, chunks) for x in part]
    pickle.dump(rows, open(out, "wb"))
    return len(rows)


def _fit(X: np.ndarray, y: np.ndarray, lam: float = 2.0, it: int = 40) -> np.ndarray:
    w = np.zeros(X.shape[1])
    R = lam * np.diag(np.r_[1e-6, np.ones(X.shape[1] - 1)])
    for _ in range(it):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ w, -30, 30)))
        w -= np.linalg.solve((X * (p * (1 - p))[:, None]).T @ X + R, X.T @ (p - y) + R @ w)
    return w


def fit_model(rows: list, recal_path: str, base_path: str = str(DEFAULT_PATH)) -> dict:
    lg = lambda a: np.array([_logit(float(x)) for x in a])
    model: dict = {"def_ev": {}, "decl_ev": {}, "thr": {}}
    for src, key in (("recal", "p_rec"), ("raw", "p_raw")):
        model["def_ev"][src] = {}
        for k in MODELED:
            rs = [x for x in rows if x["k"] == k and not x["decl"]]
            X = np.c_[np.ones(len(rs)), lg([x[key] for x in rs]), np.array([x["pos"] == 0 for x in rs], float)]
            fail = np.array([not x["win"] for x in rs], float)
            rek = np.array([bool(x["rek"]) for x in rs], float)
            m = rek > 0
            model["def_ev"][src][k] = {"q": _fit(X, rek).tolist(), "fr": _fit(X[m], fail[m]).tolist(),
                                       "fn": _fit(X[~m], fail[~m]).tolist(), "n": len(rs)}
            if src == "recal":
                p = np.array([x[key] for x in rs])
                d = np.array([x["d"] for x in rs])
                o = np.argsort(p)
                model["thr"][k] = float(p[o][int(np.argmax(np.cumsum(d[o])))])
    for k in MODELED:
        rs = [x for x in rows if x["k"] == k and x["decl"]]
        if len(rs) < 40:
            continue
        X = np.c_[np.ones(len(rs)), lg([x["p_raw"] for x in rs])]
        model["decl_ev"][k] = {"f": _fit(X, np.array([not x["win"] for x in rs], float)).tolist(), "n": len(rs)}
    rw = json.loads(Path(recal_path).read_text(encoding="utf-8"))["weights"]
    model["recal_weights"] = {k: v for k, v in rw.items() if k.startswith("d_")}
    return model


def _main(argv: List[str]) -> None:
    if len(argv) >= 5 and argv[1] == "collect":
        print(collect(int(argv[2]), int(argv[3]), argv[4]), "sor")
    elif len(argv) >= 3 and argv[1] == "fit":
        import pickle
        recal = argv[argv.index("--recal") + 1] if "--recal" in argv else "/tmp/lp_recal_keep.json"
        files = [a for a in argv[2:] if a.endswith(".pkl")]
        rows = [x for f in files for x in pickle.load(open(f, "rb"))]
        prev = json.loads(RULE_PATH.read_text(encoding="utf-8")) if RULE_PATH.exists() else {}
        model = fit_model(rows, recal)
        if "results" in prev:
            model["results"] = prev["results"]
        RULE_PATH.write_text(json.dumps(model, indent=1), encoding="utf-8")
        print("mentve:", RULE_PATH, len(rows), "sor")
    else:
        print(__doc__)


if __name__ == "__main__":
    _main(sys.argv)

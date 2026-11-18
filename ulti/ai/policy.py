"""
policy.py - Tanulható licit/talon/rablás/bedobás/kontra/rekontra politika.

Felépítés:
  * komponensenként (parti, 40-100, 20-100, ulti, betli, durchmars) egy logisztikus modell adja a
    teljesülés esélyét a 10 lapos kézből (features.py) és a vállalás környezetéből (ctx),
  * a várható pénz a VALÓDI díjtáblából jön (rules.CONTRACTS): az ulti bukása kétszeres, a kontra duplázza,
  * a döntési küszöbök (rablás, bedobás, kontra, rekontra) a szimulált pénzből hangolhatók (learn.py).
A kijátszást a playbot adja (a barát JS botjának portja).
"""
from __future__ import annotations

from ulti.paths import POLICY as POLICY_DIR
import json
import random
from pathlib import Path
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np

from features import N_FEATURES, hand_features
from playbot import bot_choose
from rules import CONTRACTS, Game, concede_loss, suits_for
from talon import TalonModel

MODEL_KEYS = ["parti", "c40", "c20", "ulti", "betli", "durch", "sdurch", "asz4"]
CTX_NAMES = ["has_parti", "has_c40", "has_c20", "has_ulti", "has_durch", "open", "red", "has_asz4"]
N_CTX = len(CTX_NAMES)
DIM = N_FEATURES + N_CTX
DEFAULT_PATH = POLICY_DIR / "learned_policy.json"
SILENT_CLIP = 3.0   # a 'csendes' tételek (csendes ulti, csendes 100, ...) korrekciója legfeljebb ennyi

DEFAULT_PARAMS: Dict[str, float] = {
    "fail_mult": 1.0,      # a bukás súlya (kontra-kockázat)
    "pickup_thr": 0.5,     # rablás: a 10 lapos kéz legjobb értéke e fölött
    "own_thr": 0.5,        # saját talon visszavétele: az új legjobb érték e fölött
    "own_gap": 1.0,        # ... és legalább ennyivel jobb a mostaninál
    "concede_margin": 1.0, # bedobás, ha a játék értéke < -margin * bedobási díj
    "kt": 0.45,            # védő kontráz, ha a felvevő sikerének becsült esélye ez alatt van
    "rt": 0.9,             # felvevő rekontráz, ha a saját sikerének esélye e fölött van
    "n_talon": 8,          # rablásnál ennyi talon-mintát értékelünk (0 = a régi, talon nélküli szabály)
}


def model_key(ci: int, k: str) -> str:
    if k == "durch" and CONTRACTS[ci]["kind"] == "durch":
        return "sdurch"
    return k


@lru_cache(maxsize=None)
def _ctx_cached(comps: Tuple[str, ...], open_: bool, red: bool) -> np.ndarray:
    return np.array([
        1.0 if "parti" in comps else 0.0, 1.0 if "c40" in comps else 0.0, 1.0 if "c20" in comps else 0.0,
        1.0 if "ulti" in comps else 0.0, 1.0 if "durch" in comps else 0.0,
        1.0 if open_ else 0.0, 1.0 if red else 0.0, 1.0 if "asz4" in comps else 0.0,
    ])


def ctx_of(ci: int) -> np.ndarray:
    """A vállalás-környezet vektora (a rules.set_ruleset-tel váltott CONTRACTS-ra is helyes, nincs importkori befagyasztás)."""
    c = CONTRACTS[ci]
    return _ctx_cached(tuple(sorted(c["comps"])), bool(c["open"]), bool(c["red"]))


TRUMP_IDX = (-1, 0, 1, 2, 3)


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def zero_weights() -> Dict[str, np.ndarray]:
    w = {k: np.zeros(DIM) for k in MODEL_KEYS}
    w.update({"d_" + k: np.zeros(DIM) for k in MODEL_KEYS})
    w["silent"] = np.zeros(DIM)
    return w


class LearnedPolicy:
    name = "learned"

    def __init__(self, weights: Optional[Dict[str, np.ndarray]] = None, params: Optional[Dict[str, float]] = None,
                 talon: Optional[TalonModel] = None):
        self.w = weights if weights is not None else zero_weights()
        self.params = dict(DEFAULT_PARAMS)
        if params:
            self.params.update(params)
        self.talon = talon if talon is not None else TalonModel()

    # ---------- mentés / betöltés ----------
    def to_json(self) -> dict:
        return {"weights": {k: [float(x) for x in v] for k, v in self.w.items()}, "params": self.params,
                "feature_dim": N_FEATURES, "ctx": CTX_NAMES, "talon": self.talon.to_json()}

    @classmethod
    def from_json(cls, d: dict) -> "LearnedPolicy":
        if d.get("feature_dim") != N_FEATURES:
            raise ValueError("A mentett súlyok más jellemzőszámra készültek")
        w = zero_weights()
        for k, v in d["weights"].items():
            if k in w:
                a = np.array(v, dtype=float)
                w[k][:min(len(a), DIM)] = a[:DIM]       # régebbi mentés (kevesebb környezet-jellemző): a hiányzó súly 0
        return cls(w, d.get("params"), TalonModel.from_json(d.get("talon")))

    def save(self, path: Path = DEFAULT_PATH) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "LearnedPolicy":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    # ---------- értékelés ----------
    def _rows(self, cards: List[int], discard: bool):
        cards = list(cards)
        n = len(cards)
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)] if discard else [None]
        F = np.zeros((len(TRUMP_IDX), len(pairs), N_FEATURES))
        has7 = np.zeros((len(TRUMP_IDX), len(pairs)), dtype=bool)
        for pi, pr in enumerate(pairs):
            if pr is None:
                rest, disc = cards, ()
            else:
                rest = [c for q, c in enumerate(cards) if q != pr[0] and q != pr[1]]
                disc = (cards[pr[0]], cards[pr[1]])
            rs = set(rest)
            for ti, t in enumerate(TRUMP_IDX):
                F[ti, pi] = hand_features(rest, t, disc)
                has7[ti, pi] = t >= 0 and (t * 8) in rs
        return cards, pairs, F, has7

    def _ev_contract(self, ci: int, F_t: np.ndarray, has7_t: np.ndarray) -> np.ndarray:
        c = CONTRACTS[ci]
        ctx = ctx_of(ci)
        fm = self.params["fail_mult"]
        ev = np.zeros(F_t.shape[0])
        for k, v in c["comps"].items():
            w = self.w[model_key(ci, k)]
            p = sigmoid(F_t @ w[:N_FEATURES] + ctx @ w[N_FEATURES:])
            loss = v * (2 if k == "ulti" else 1) * fm
            ev += p * v - (1.0 - p) * loss
        ws = self.w["silent"]
        ev += np.clip(F_t @ ws[:N_FEATURES] + ctx @ ws[N_FEATURES:], -SILENT_CLIP, SILENT_CLIP)
        if "ulti" in c["comps"]:
            ev = np.where(has7_t, ev, -1e9)
        return ev

    def best_option(self, cards: List[int], min_ci: int, discard: bool) -> Optional[Tuple[float, int, int, List[int]]]:
        cards, pairs, F, has7 = self._rows(cards, discard)
        best = None
        for ci in range(min_ci, len(CONTRACTS)):
            for s in suits_for(ci):
                ti = s + 1 if s >= 0 else 0
                ev = self._ev_contract(ci, F[ti], has7[ti])
                j = int(np.argmax(ev))
                if ev[j] > -1e8 and (best is None or ev[j] > best[0]):
                    pr = pairs[j]
                    disc = [cards[pr[0]], cards[pr[1]]] if pr else []
                    best = (float(ev[j]), ci, s, disc)
        return best

    def ranked_options(self, cards: List[int], min_ci: int, k: int = 8) -> List[Tuple[float, int, int, List[int]]]:
        """A k legjobb (vállalás, adu) páros a legjobb talonletétellel, modell-érték szerint csökkenően."""
        cards, pairs, F, has7 = self._rows(cards, True)
        out: List[Tuple[float, int, int, List[int]]] = []
        for ci in range(min_ci, len(CONTRACTS)):
            for s in suits_for(ci):
                ev = self._ev_contract(ci, F[s + 1 if s >= 0 else 0], has7[s + 1 if s >= 0 else 0])
                j = int(np.argmax(ev))
                if ev[j] > -1e8:
                    pr = pairs[j]
                    out.append((float(ev[j]), ci, s, [cards[pr[0]], cards[pr[1]]]))
        out.sort(key=lambda o: -o[0])
        return out[:k]

    def hand_value(self, hand: List[int], ci: int, trump: int, disc: Tuple[int, ...] = ()) -> float:
        F = np.array([hand_features(hand, trump, disc)])
        has7 = np.array([trump >= 0 and (trump * 8) in set(hand)])
        return float(self._ev_contract(ci, F, has7)[0])

    def p_success(self, hand: List[int], ci: int, trump: int, k: str, defender: bool, disc: Tuple[int, ...] = ()) -> float:
        key = ("d_" if defender else "") + model_key(ci, k)
        w = self.w[key]
        x = np.array(hand_features(hand, trump, () if defender else disc))
        return float(sigmoid(x @ w[:N_FEATURES] + ctx_of(ci) @ w[N_FEATURES:]))

    # ---------- döntések ----------
    def bid_action(self, g: Game, p: int, rng: random.Random) -> dict:
        P = self.params
        if g.holding12(p):
            b = self.best_option(g.hands[p], g.min_bid_index(), True)
            if b is None:
                raise ValueError("nincs érvényes licit")
            return {"type": "bid", "ci": b[1], "suit": b[2], "disc": b[3]}
        if g.bid and g.bid[2] == p:
            if not g.can_pick_up(p):
                return {"type": "pass"}
            b = self.best_option(g.hands[p] + g.talon, g.min_bid_index(), True)
            ci0, su0 = g.bid[0], g.bid[1]
            cur = self.hand_value(g.hands[p], ci0, su0, tuple(g.talon))
            if b and b[0] > P["own_thr"] and b[0] > cur + P["own_gap"]:
                return {"type": "pickup"}
            if cur < -concede_loss(ci0, None, 1) * P["concede_margin"]:
                return {"type": "concede"}
            return {"type": "pass"}
        if g.can_pick_up(p) and self.wants_pickup(g, p, rng):
            return {"type": "pickup"}
        return {"type": "pass"}

    def defender_value(self, hand: List[int], ci: int, trump: int) -> float:
        """A védő várható pénze az álló licit ellen (ha nem veszünk fel): a felvevő sikerének becsült esélyéből."""
        ev = 0.0
        for k, v in CONTRACTS[ci]["comps"].items():
            ps = self.p_success(hand, ci, trump, k, True)
            ev += (1.0 - ps) * v * (2 if k == "ulti" else 1) - ps * v
        return ev

    def pickup_values(self, g: Game, p: int, rng: random.Random, n: Optional[int] = None) -> Tuple[float, float]:
        """(védőként maradok, felveszem a talont) várható pénz; a talon nem véletlen (talon.py), és a rablás után
        licitálni KELL: a licit-lépcső feljebb ugrik."""
        n = max(1, int(self.params["n_talon"]) if n is None else n)
        ci0, su0, _ = g.bid
        stay = self.defender_value(g.hands[p], ci0, su0)
        floor = -float(concede_loss(ci0, None, 1))
        total = 0.0
        for _ in range(n):
            t = self.talon.sample(g, p, rng)
            b = self.best_option(g.hands[p] + t, g.min_bid_index(), True)
            total += b[0] if b else floor
        return stay, total / n

    def wants_pickup(self, g: Game, p: int, rng: random.Random) -> bool:
        """Rablás: a (valószínű) talonnal javuló kéz várható értéke vs. védőként maradni."""
        P = self.params
        if int(P["n_talon"]) <= 0:
            b = self.best_option(g.hands[p], g.min_bid_index(), False)
            return bool(b and b[0] > P["pickup_thr"])
        stay, go = self.pickup_values(g, p, rng)
        return go > stay + P["pickup_thr"]

    def ranked_discards(self, cards: List[int], ci: int, trump: int, k: int = 3) -> List[Tuple[float, List[int]]]:
        """A k legjobb talonletétel (2 lap) az adott vállalásra és adura a 12 lapos kézből, érték szerint csökkenően."""
        cards, pairs, F, has7 = self._rows(cards, True)
        ti = trump + 1 if trump >= 0 else 0
        ev = self._ev_contract(ci, F[ti], has7[ti])
        order = np.argsort(-ev)[:k]
        return [(float(ev[j]), [cards[pairs[j][0]], cards[pairs[j][1]]]) for j in order if ev[j] > -1e8]

    def kontra(self, g: Game, p: int, rng: random.Random) -> List[str]:
        P = self.params
        trump = g.trump if g.trump_game else -1
        out = []
        for k in g.kontra_options(p):
            if p == g.decl:
                ps = self.p_success(g.hands[p], g.ci, trump, k, False, tuple(g.talon))
                if ps > P["rt"]:
                    out.append(k)
            else:
                ps = self.p_success(g.hands[p], g.ci, trump, k, True)
                if ps < P["kt"]:
                    out.append(k)
        return out

    def card(self, g: Game, p: int, rng: random.Random) -> int:
        return bot_choose(g, p)


_CACHE: Dict[str, LearnedPolicy] = {}


def make_learned(path: str = str(DEFAULT_PATH)) -> LearnedPolicy:
    """Folyamatonként egyszer betölti a mentett politikát (az arena párhuzamos futtatásához)."""
    if path not in _CACHE:
        _CACHE[path] = LearnedPolicy.load(Path(path))
    return _CACHE[path]

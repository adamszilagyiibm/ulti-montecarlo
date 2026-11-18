"""tune.py - A tanult politika döntési küszöbeinek hangolása, megszakítás-állóan.

Koordinátánkénti keresés a `learn.TUNE_GRID` rácson. Minden javulás után azonnal menti a politikát,
és az időkeret lejártakor tisztán leáll, így rövid, előtérben futó adagokban is használható.
A hangolás a `learn.TUNE_SEED0` leosztásain fut, ezek függetlenek a végső méréstől (`final_eval.py`).

Használat: python tune.py [--deals 150] [--max-seconds 240] [--path learned_policy.json]
"""
from ulti.paths import POLICY
import argparse
import time
from pathlib import Path

import learn
from policy import LearnedPolicy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deals", type=int, default=150)
    ap.add_argument("--max-seconds", type=int, default=240)
    ap.add_argument("--path", default=str(POLICY / "learned_policy.json"))
    a = ap.parse_args()

    path = Path(a.path)
    tmp = path.with_suffix(".tune.json")
    pol = LearnedPolicy.load(path)
    t0 = time.time()
    best = learn.evaluate_policy(pol, learn.TUNE_SEED0, a.deals, tmp)["paired_diff"].mean
    print(f"kiinduló érték: {best:+.3f}  küszöbök: {pol.params}", flush=True)

    for name, vals in learn.TUNE_GRID.items():
        for v in vals:
            if time.time() - t0 > a.max_seconds:
                print("időkeret lejárt, a mentett állapot érvényes.", flush=True)
                tmp.unlink(missing_ok=True)
                return
            if v == pol.params[name]:
                continue
            cand = LearnedPolicy(pol.w, {**pol.params, name: v}, pol.talon)
            m = learn.evaluate_policy(cand, learn.TUNE_SEED0, a.deals, tmp)["paired_diff"].mean
            if m > best + 1e-9:
                print(f"  {name}: {pol.params[name]} -> {v}  ({m:+.3f})", flush=True)
                best, pol = m, cand
                pol.save(path)
    tmp.unlink(missing_ok=True)
    print(f"kész. érték: {best:+.3f}  küszöbök: {pol.params}", flush=True)


if __name__ == "__main__":
    main()

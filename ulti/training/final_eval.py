"""final_eval.py - A mentett tanult politika mérése friss, a tanítástól és hangolástól független leosztásokon.
Használat: python final_eval.py <seed0> <leosztások>"""
from ulti.paths import POLICY
import functools
import sys
import time

import arena
from policy import make_learned


def main() -> None:
    seed0, deals = int(sys.argv[1]), int(sys.argv[2])
    t = time.time()
    r = arena.evaluate(functools.partial(make_learned, str(POLICY / "learned_policy.json")), deals=deals, seed0=seed0, workers=8)
    print(f"friss leosztások seed {seed0}+{deals}: n={r['n']} ülés")
    print("  tanult pénz/ülés :", r["test_money"])
    print("  JS bot pénz/ülés :", r["base_money"])
    print("  páros különbség  :", r["paired_diff"])
    print("  felvevő arány    : tanult %.3f  JS %.3f   bedobás %.3f" % (r["declares_test"], r["declares_base"], r["concede_rate_test"]))
    print(round(time.time() - t), "s")


if __name__ == "__main__":
    main()

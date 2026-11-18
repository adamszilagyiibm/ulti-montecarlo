"""match.py - Mérkőzés-mátrix: a vizsgált politika egy ülésen, a másik két ülésen az alap-politika.

Páros mérés (ugyanaz a leosztás és ülés), pénz/ülés. Megmutatja, hogy a tanult licit ereje
általános-e, vagy csak a JS-bot gyengeségére épít.

Használat: python match.py <teszt> <alap> [leosztás] [seed0] [minta]
Politikák: js, learned, js+mc, learned+mc, learned:<fájl.json>
"""
import functools
import sys
import time

import arena
from mcplay import MCPlay
from policy import make_learned


def make_js_mc(samples: int = 8) -> MCPlay:
    return MCPlay(arena.JsPolicy(), samples=samples, name="js+mc")


def make_learned_mc(samples: int = 8) -> MCPlay:
    return MCPlay(make_learned(), samples=samples, name="learned+mc")


def maker(name: str, samples: int):
    if name.startswith("learned:"):          # learned:<útvonal> - tetszőleges mentett politika
        return functools.partial(make_learned, name.split(":", 1)[1])
    if name == "js":
        return arena.make_js
    if name == "learned":
        return make_learned
    if name == "js+mc":
        return functools.partial(make_js_mc, samples)
    if name == "learned+mc":
        return functools.partial(make_learned_mc, samples)
    raise SystemExit(f"ismeretlen politika: {name}")


def main() -> None:
    test, base = sys.argv[1], sys.argv[2]
    deals = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    seed0 = int(sys.argv[4]) if len(sys.argv) > 4 else 4_000_000
    samples = int(sys.argv[5]) if len(sys.argv) > 5 else 8
    t = time.time()
    r = arena.evaluate(maker(test, samples), deals=deals, seed0=seed0, workers=8, base_maker=maker(base, samples))
    print(f"{test} VS {base}  (seed {seed0}+{deals}, n={r['n']}, minta={samples})")
    print(f"  {test:11s} pénz/ülés : {r['test_money']}")
    print(f"  {base:11s} pénz/ülés : {r['base_money']}")
    print(f"  páros különbség   : {r['paired_diff']}")
    print(f"  felvevő arány     : {test} {r['declares_test']:.3f}  {base} {r['declares_base']:.3f}   bedobás {r['concede_rate_test']:.3f}")
    print(f"  {time.time() - t:.0f} s", flush=True)


if __name__ == "__main__":
    main()

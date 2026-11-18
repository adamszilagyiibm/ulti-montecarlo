"""Két e2e-futás (E2E_DUMP fájlok) leosztásonkénti pénzének páros különbsége és 95%-os CI-je (1,96·stdev/√n).

Használat:
  E2E_DUMP=/tmp/py.json node tools/e2e.js <port> 240 py 7
  E2E_DUMP=/tmp/js.json node tools/e2e.js <port> 240 js 7
  python tools/e2e_diff.py /tmp/py.json /tmp/js.json
Az azonos seed azonos leosztásokat ad, ezért a különbség páros; a +0,87 ± 0,45 így jött ki."""
from __future__ import annotations

import json
import statistics as st
import sys


def paired(a: list, b: list) -> tuple:
    n = min(len(a), len(b))
    d = [x - y for x, y in zip(a[:n], b[:n])]
    return n, st.mean(d), 1.96 * st.stdev(d) / n ** 0.5


if __name__ == "__main__":
    pa, pb = (json.load(open(p)) for p in sys.argv[1:3])
    n, m, ci = paired(pa, pb)
    print(f"{n} leosztás: különbség {m:+.2f} ± {ci:.2f} pénz/leosztás-ülés")

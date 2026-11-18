"""
extract_book.py - A Kovács-könyv szabály- és tanácsrészeinek kinyerése watsonx.ai-val, több prompttal és szavazással.

Oldalak: 79-132 (a rablóulti menete, figurák, kontrázás, bemondások, talonletétel, indulás/ellenjáték, renonsz,
számítási táblázat) és 210-223 (gyakorlati tanácsok, Ultilexikon). A 20-78. oldal a négyes/talonmária, nem rablóulti.

Minden tételhez SZÓ SZERINTI idézet kell; a kód az idézetet megkeresi az OCR-szövegben (OCR-hibát tűrő összevetéssel),
és ami nem található meg, azt eldobja. A modell több, eltérő megfogalmazású prompttal fut (az eredmény tételenként
szavaz): a tétel 'votes' mezője megmondja, hány futás találta meg ugyanazt az idézett helyet.

Használat: python extract_book.py [--pages 79-132] [--runs 3] [--out book_knowledge.json]
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE, RAW
import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import wx
from extract_kodex import PAGE_RE, _norm, quote_in_source, split_pages

ROOT = Path(__file__).parent
SRC = RAW / "kodex_ocr.txt"
OUT = KNOWLEDGE / "book_knowledge.json"
TOPICS = "szabaly|licit|talon|rablas|kontra|rekontra|bedobas|kijatszas|vedekezes|pontozas|renonsz|definicio|egyeb"

COMMON = (
    "Az alábbi szöveg a Kovács Endre-féle Ultikodex (rablóulti) OCR-ból származó részlete; lehetnek benne OCR-hibák, "
    "kártyás ábrák és táblázatok összekeveredett sorai.\n"
    "Válaszolj EGYETLEN JSON tömbként, más szöveg nélkül. Egy elem:\n"
    '{{"topic": "<' + TOPICS + '>", "contract": "<vállalás: ulti|betli|durchmars|40-100|20-100|4 ász|parti|általános>", '
    '"rule": "<a szabály/tanács egy tömör magyar mondatban>", "condition": "<mikor érvényes, vagy üres>", '
    '"direction": "<tedd|ne_told|szabaly|informacio>", "quote": "<SZÓ SZERINTI idézet a szövegből, 1-2 mondat>"}}\n'
    "Szigorú szabályok:\n"
    "- KIZÁRÓLAG azt add vissza, ami a szövegben áll; semmit ne egészíts ki a saját tudásodból.\n"
    "- A 'quote' változtatás nélkül másolt, összefüggő szövegrész legyen (legalább 8 szó).\n"
    "- Ha a részletben nincs ilyen tétel, az eredmény []\n"
)

PROMPTS = [
    COMMON + "Feladat: gyűjtsd ki a SZABÁLYOKAT (mit szabad / kell / tilos, hogyan működik a licit, a rablás, a kontra, "
             "a bedobás, a renonsz, a pontozás) és az általános érvényű TANÁCSOKAT.\n\n--- SZÖVEG ---\n{text}\n--- VÉGE ---",
    COMMON + "Feladat: kifejezetten a DÖNTÉSI TANÁCSOKAT keresd: mikor érdemes bemondani, rabolni, kontrázni, rekontrázni, "
             "bedobni; mit tegyünk a talonba; hogyan indulj; hogyan védekezz. Példajáték egyedi leírását ne vedd fel.\n\n"
             "--- SZÖVEG ---\n{text}\n--- VÉGE ---",
    COMMON + "Feladat: bármilyen állítást, definíciót, díjat, feltételt vagy kivételt gyűjts ki, ami a játék SZABÁLYÁRA vagy "
             "a helyes DÖNTÉSRE vonatkozik (a lexikon-szócikkek is ide tartoznak: a topic ilyenkor 'definicio'). "
             "Légy kimerítő, de csak idézhető tételt adj.\n\n--- SZÖVEG ---\n{text}\n--- VÉGE ---",
]


def parse_array(txt: str) -> List[dict]:
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    return [x for x in arr if isinstance(x, dict) and x.get("quote") and x.get("rule")]


def locate(quote: str, source: str) -> Optional[int]:
    """Az idézet kezdőpozíciója a normalizált forrásban (azonosításra: ugyanaz a hely = ugyanaz a tétel)."""
    q, s = _norm(quote), _norm(source)
    head = q[:40]
    return s.find(head) if head and head in s else None


def one_run(args: Tuple[int, int, str, int]) -> List[dict]:
    a, b, src, pi = args
    out: List[dict] = []
    try:
        raw = wx.text(PROMPTS[pi % len(PROMPTS)].format(text=src[:9000]), max_tokens=3500,
                      temperature=0.0 if pi == 0 else 0.3, seed=pi)
    except Exception as e:
        print(f"[!] {a}-{b}. oldal, prompt {pi}: {str(e)[:80]}", flush=True)
        return out
    for it in parse_array(raw):
        if quote_in_source(str(it["quote"]), src):
            pos = locate(str(it["quote"]), src)
            out.append({**{k: str(it.get(k, ""))[:400] for k in ("topic", "contract", "rule", "condition", "direction", "quote")},
                        "pos": pos, "prompt": pi, "pages": f"{a}-{b}"})
    return out


def merge(items: List[dict], runs: int) -> List[dict]:
    """Ugyanarra a helyre (idézet-kezdet) mutató tételek összevonása; a 'votes' = ahány különböző prompt találta."""
    groups: List[List[dict]] = []
    for it in sorted(items, key=lambda x: (x["pages"], x["pos"] if x["pos"] is not None else -1)):
        for g in groups:
            h = g[0]
            if h["pages"] == it["pages"] and h["pos"] is not None and it["pos"] is not None and abs(h["pos"] - it["pos"]) < 60:
                g.append(it)
                break
        else:
            groups.append([it])
    out = []
    for g in groups:
        votes = len({x["prompt"] for x in g})
        best = max(g, key=lambda x: len(x["quote"]))
        topics = [x["topic"] for x in g]
        out.append({"pages": best["pages"], "topic": max(set(topics), key=topics.count), "contract": best["contract"],
                    "rule": best["rule"], "condition": best["condition"], "direction": best["direction"],
                    "quote": best["quote"], "votes": votes, "runs": runs})
    return out


def run(lo: int, hi: int, runs: int, per_chunk: int = 3) -> dict:
    pages = [p for p in split_pages(SRC.read_text(encoding="utf-8")) if lo <= p[0] <= hi]
    chunks = []
    for i in range(0, len(pages), per_chunk):
        grp = pages[i:i + per_chunk]
        chunks.append((grp[0][0], grp[-1][0], "\n\n".join(t for _, t in grp)))
    jobs = [(a, b, s, pi) for (a, b, s) in chunks for pi in range(runs)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(one_run, jobs))
    flat = [x for r in res for x in r]
    merged = merge(flat, runs)
    return {"source": "Kovács Endre: Ultikodex (OCR)", "pages": f"{lo}-{hi}", "runs": runs,
            "raw_verified": len(flat), "items": merged}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="79-132")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--per-chunk", type=int, default=3)
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.pages.split("-"))
    d = run(lo, hi, a.runs, a.per_chunk)
    path = Path(a.out)
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"parts": {}}
    prev.setdefault("parts", {})[a.pages] = d
    path.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
    items = d["items"]
    by = {v: sum(1 for x in items if x["votes"] == v) for v in range(1, a.runs + 1)}
    print(f"{a.pages}. oldal: {d['raw_verified']} ellenőrzött idézet -> {len(items)} egyedi tétel; szavazat-eloszlás {by}")


if __name__ == "__main__":
    main()

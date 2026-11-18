"""
extract_facts.py - A példatár szöveges tényei (vállalás, adu, felvevő, kontrák, a könyv pontszáma) az OCR-ból.

A szöveges modell JSON-t ad; minden példát többször, eltérő megfogalmazással kérdezünk, és szavazunk.
A vállalást a rules.CONTRACTS 42 neve közül kell választania (sorszámmal), így nincs szabad szöveg.
A 'Magyarázat' és a 'Tanulság' szakaszt a kód vágja ki szó szerint, nem a modell.

Használat: python extract_facts.py [--only 1,2] [--runs 3] [--out facts.json]
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE
import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

import wx
from extract_examples import STARTS
from parse_tricks import example_text, pages_text
from rules import CONTRACTS, find_contract, set_ruleset

ROOT = Path(__file__).parent
OUT = KNOWLEDGE / "facts.json"


PROMPT = (
    "Alább egy rablóulti-példa szövege (OCR, lehetnek benne hibák). A játékosok: Albert, Béla, Csaba, Dénes (három játszik, egy az osztó).\n"
    "Feladat: add vissza a példa tényeit EGYETLEN JSON objektumként, más szöveg nélkül:\n"
    '{{"final_declarer": "<aki a végső vállalást játssza>", "first_declarer": "<aki először felvette a talont>", '
    '"components": ["<a végső vállalás figurái: parti|40-100|20-100|ulti|4asz|durchmars|betli>"], "open": true|false, "trump": "P|T|Z|M|null", '
    '"kontra": [{{"player": "<név>", "what": "kontra|rekontra", "part": "<jatek|ulti|betli|durchmars|4asz|mind>"}}], '
    '"book_abs_per_opponent": <előjel nélküli szám|null>, "declarer_won_overall": true|false|null, "note": "<mit nem tudtál biztosan>"}}\n'
    "Szabályok:\n"
    "- A 'components' a VÉGSŐ, ténylegesen bemondott vállalás figurái (a rabló is bemondhat újat). Ha a színre felvett játékra semmilyen figurát nem mondtak, ['parti']. "
    "Az ultimó bemondás ['ulti'], a 40-100 ['40-100'], együtt pl. ['40-100','ulti']; a durchmars ['durchmars'], betli ['betli']. A 'piros' NEM külön figura: a piros színt a 'trump' jelzi (P). "
    "Az 'open' true, ha a vállalás TERÍTETT (terített durchmars, terített betli), egyébként false. Színtelen durchmarsnál trump null.\n"
    "- A 'book_abs_per_opponent' a Végeredmény sorból az ÖSSZESÍTETT pont egy ellenfélre, ELŐJEL NÉLKÜL, szó szerint a szövegből "
    "(pl. 'Összesen: 24-24 pont' -> 24, 'nyeresége összesen: 36-36' -> 36, '10-10 pontba került' -> 10). Ha egyik sem fizet a másiknak, "
    "'kutyakefe' vagy kiegyenlítették egymást, 0. Ha a két ellenfél eltérő összeget fizet/kap, vagy nincs ilyen sor, null.\n"
    "- A 'declarer_won_overall' true, ha a végső felvevő a végeredményben nyert (a szöveg szerint pénzt kapott), false, ha fizetett; null, ha egyik sem.\n"
    "- A trump a vállalás színe (P piros, T tök, Z zöld, M makk); betlinél és színtelen durchmarsnál null.\n"
    "- A kontra 'part' mezője: 'jatek', ha a szöveg a JÁTÉKOT / játszmát / pártit / a 40-100-at / a 20-100-at kontrázza (pl. „kontrázta a játékot”, „kontra játék”, „kontra piros játszma”); 'ulti', ha az ULTIMÓT kontrázza; 'betli', ha a betlit; 'durchmars', ha a durchmarsot; '4asz', ha a négy ászt; 'mind', ha mindent kontráz. Ha valaki a játékot ÉS az ultimót is kontrázza, két külön elemet adj. Ha a szöveg szerint valaki NEM kontrázta az ultimót, ne vedd fel.\n"
    "- A kontra 'what' mezője 'rekontra', ha a felvevő (vagy rabló) visszakontráz.\n"
    "- A CSENDES figurák (pl. \"csendes ultimó\", \"csendes 100\", \"csendes durchmars\") NEM bemondások, ezek nem lehetnek a components elemei. Ha csak színre vette fel és figurát nem mondott, a components ['parti']. A 'kontra' lista csak azt tartalmazza, amit a szöveg valóban kontrázásként ír.\n"
    "- Csak azt írd, ami a szövegben áll.\n\n"
    "--- PÉLDA ---\n{text}\n--- VÉGE ---"
)
NAMES = {"Albert", "Béla", "Csaba", "Dénes"}


def parse_json(txt: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def sections(t: str) -> Dict[str, str]:
    """A magyarázat és a tanulság szó szerint (a modell nélkül)."""
    out = {"explanation": "", "lesson": "", "result_text": ""}
    m = re.search(r"Magyarázat\.?(.*?)(?=Tanulság|Végeredmény|$)", t, re.S)
    if m:
        out["explanation"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"Tanulság:?(.*?)(?=Végeredmény|$)", t, re.S)
    if m:
        out["lesson"] = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"Végeredmény:?(.*)$", t, re.S)
    if m:
        out["result_text"] = re.sub(r"\s+", " ", m.group(1)).strip()
    return out


def key_of(f: dict) -> str:
    ks = sorted((k.get("player"), k.get("what"), k.get("part")) for k in f.get("kontra", []) if isinstance(k, dict))
    return json.dumps([f.get("final_declarer"), f.get("contract_index"), f.get("trump"), ks], ensure_ascii=False)


COMP_MAP = {"parti": "parti", "40-100": "c40", "20-100": "c20", "ulti": "ulti", "ultimó": "ulti", "4asz": "asz4", "4ász": "asz4",
            "durchmars": "durch", "betli": "betli"}


def contract_index(f: dict) -> Optional[int]:
    """A modell által megnevezett figurákból a (könyv-díjas) CONTRACTS sorszáma; None, ha nincs ilyen vállalás."""
    comps = [COMP_MAP.get(str(c).lower().replace(" ", "")) for c in (f.get("components") or [])]
    if not comps or None in comps:
        return None
    tr = f.get("trump")
    red = tr == "P"
    opn = bool(f.get("open"))
    if "betli" in comps:
        name = "Terített betli" if opn else ("Piros betli" if red else "Betli")
        return next((i for i, c in enumerate(CONTRACTS) if c["name"] == name), None)
    if tr in (None, "null"):
        return find_contract(comps, False, opn, kind="durch") if "durch" in comps else None
    return find_contract(comps, red, opn)


def valid(f: Optional[dict]) -> bool:
    if not f:
        return False
    ci = contract_index(f)
    if f.get("final_declarer") not in NAMES or ci is None or f.get("trump") not in ("P", "T", "Z", "M", None, "null"):
        return False
    f["contract_index"] = ci
    return True


ZERO_PAT = re.compile(r"kutyakefe|egyik sem fizet|kiegyenl[ií]tették|nem fizet", re.I)


def text_magnitude(result_text: str) -> Optional[int]:
    """A Végeredmény sorból az ellenfelenkénti ÖSSZESÍTETT pont (előjel nélkül), a modell nélkül: 'X-X pont' pár vagy
    'összesen/nyeresége/vesztesége : X pont'. None, ha nem egyértelmű. 0, ha a szöveg szerint senki sem fizet."""
    t = re.sub(r"\s+", " ", result_text)
    t = re.sub(r"(\w)- (\w)", r"\1\2", t)                       # elválasztás: 'pon- tot' -> 'pontot'
    if ZERO_PAT.search(t):
        return 0
    for d in re.findall(r"\d{3,}", t):                           # '48-48' OCR-ja '48148' (a kötőjelet 1-nek olvasta)
        if len(d) % 2 == 1 and d[len(d) // 2] == "1" and d[:len(d) // 2] == d[len(d) // 2 + 1:]:
            return int(d[:len(d) // 2])
    pairs = [int(a) for a, b in re.findall(r"(\d+)\s*[-–—]+\s*(\d+)\s*pon", t) if a == b]
    if pairs:
        return pairs[-1]
    m = re.findall(r"(?:összesen|összegen|nyeresége|nyereség|vesztesége)\s*:?\s*(\d+)\s*pont", t)
    return int(m[-1]) if m else None


# amit az OCR szétzúzott, de a szövegből kézzel egyértelmű (példa -> ellenfelenkénti összpont):
#  14: "kapott Dénestől és Bélától 20 —20 — 40 pontot" (20-20, összesen 40);  30: "16-4-4—20 pontot" (ellenfelenként 20)
TEXT_FIX = {14: 20, 30: 20}


def run_example(n: int, runs: int, P: Dict[int, str]) -> dict:
    set_ruleset("book")                  # a vállalás-sorszámok a könyv díjtáblázatának listájára vonatkoznak (4 ász is szerepel)
    t = example_text(n, P)
    votes: Counter = Counter()
    store: Dict[str, List[dict]] = {}
    for i in range(runs):
        txt = wx.text(PROMPT.format(text=t[:9000]), max_tokens=500,
                      temperature=0.0 if i == 0 else 0.4, seed=i)
        f = parse_json(txt)
        if valid(f):
            k = key_of(f)
            votes[k] += 1
            store.setdefault(k, []).append(f)
    r: dict = {"example": n, "runs": runs, "valid": sum(votes.values()), **sections(t)}
    if votes:
        k, v = votes.most_common(1)[0]
        f = store[k][0]
        mags = [x.get("book_abs_per_opponent") for x in store[k] if isinstance(x.get("book_abs_per_opponent"), (int, float))]
        model_mag = Counter(mags).most_common(1)[0][0] if mags else None
        tm = TEXT_FIX.get(n, text_magnitude(r["result_text"]))
        # a számot a SZÖVEGBŐL olvassuk; a modell becslése csak akkor számít, ha a szövegben tényleg szerepel
        mag = tm if tm is not None else (model_mag if (model_mag and str(int(model_mag)) in re.sub(r"\s+", "", r["result_text"])) else None)
        won = Counter(x.get("declarer_won_overall") for x in store[k]).most_common(1)[0][0]
        f["declarer_won_overall"] = won
        # az ellenfelek nyeresége egyenként: pozitív, ha a felvevő veszített (a build_examples összevetése ezt várja)
        book_net = None if (mag is None or (won is None and mag != 0)) else (0 if mag == 0 else (-abs(mag) if won else abs(mag)))
        r.update(status="ok", votes=v, distinct=len(votes), facts=f, book_net=book_net, book_abs=mag, book_abs_model=model_mag)
    else:
        r["status"] = "manual"
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    P = pages_text()
    nums = [int(x) for x in a.only.split(",")] if a.only else sorted(STARTS)
    path = Path(a.out)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for n in nums:
        r = run_example(n, a.runs, P)
        data[str(n)] = r
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if r["status"] == "ok":
            f = r["facts"]
            print(f"[{n:2d}] {r['votes']}/{r['valid']} egyezik | {f['final_declarer']:6s} {CONTRACTS[f['contract_index']]['name']:30s} adu={f['trump']} "
                  f"kontra={len(f.get('kontra', []))} könyv={r['book_net']}", flush=True)
        else:
            print(f"[{n:2d}] KÉZI", flush=True)


if __name__ == "__main__":
    main()

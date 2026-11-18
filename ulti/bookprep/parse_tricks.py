"""
parse_tricks.py - A példatár ütéstábláinak értelmezése az OCR-szövegből, a diagramból kapott kezek segítségével.

Az OCR a lapjelöléseket elrontja ('Z 28', 'P1oO', 'T7?7', 'Za'), ezért minden játszott lapot a játékos MARADÉK
kezéből választunk ki (legjobb hasonlóság a nyomtatott szöveghez), és ellenőrizzük:
  * a játékos kezében volt a lap, a játékos kezében minden lap pontosan egyszer kerül ki,
  * a hívó színt a játékos szabályosan követi (rules.Game.legal_cards),
  * az ütés nyertese (a szabályok szerint) hívja a következő ütést.
Ami nem megy át, az "inkonzisztens" és nem kerül a tanuló / ellenőrző adatba.
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE, RAW
import difflib
import json
import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from extract_examples import STARTS
from kodex_cards import card_str

ROOT = Path(__file__).parent
NAME_PATS = {
    "Albert": r"A[l1I]b[eé]rt|Alb\w{1,3}t",
    "Béla": r"B[ée]{1}[l1Ii]a|B[ée]la|Bela",
    "Csaba": r"[CGcO(]\s?[sS5]aba|Csaba|saba",
    "Dénes": r"[DB][éeóo]n[ea][sg]|D[ée]nes|Déneg",
}
NAME_RE = re.compile("|".join(f"(?P<{k}>{v})" for k, v in NAME_PATS.items()))
RANK_FORMS = ["7", "8", "9", "10", "a", "f", "k", "asz"]
SUIT_L = "PTZM"


def pages_text() -> Dict[int, str]:
    parts = re.split(r"=== OLDAL (\d+) ===\n", (RAW / "kodex_ocr.txt").read_text(encoding="utf-8"))
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def example_text(n: int, P: Optional[Dict[int, str]] = None) -> str:
    P = P or pages_text()
    a = STARTS[n]
    b = STARTS[n + 1] if n + 1 in STARTS else 212
    t = "\n".join(P[p] for p in range(a, b + 1))
    if n + 1 in STARTS:
        m = re.search(rf"\n[^\n]{{0,4}}{n + 1}\.\s*példa", t)
        if m:
            t = t[: m.start()]
    return t


def trick_section(t: str) -> str:
    m = re.search(r"A 10 ütés|Az ütések|ütés[ae]k? (?:így|következ|a követk)|A \d+ ütés", t)
    start = m.start() if m else 0
    e = re.search(r"Magyarázat|\(.{0,40}(?:ultim|betli|durch|ász|játsz).{0,30}\)", t[start:])
    return t[start: start + e.start()] if e else t[start:]


def split_plays(sec: str, players: List[str]) -> List[Tuple[str, str]]:
    marks = []
    for m in NAME_RE.finditer(sec):
        nm = next(k for k in NAME_PATS if m.group(k))
        if nm in players:
            marks.append((nm, m.start(), m.end()))
    out: List[Tuple[str, str]] = []
    for i, (nm, s, e) in enumerate(marks):
        nxt = marks[i + 1][1] if i + 1 < len(marks) else len(sec)
        chunk = sec[e:nxt]
        chunk = re.sub(r"\(.*?\)", " ", chunk, flags=re.S)
        chunk = re.sub(r"\b\d{3}\b", " ", chunk)          # oldalszám
        chunk = re.sub(r"\n\s*\d+\.\s*$", " ", chunk)     # a következő sor sorszáma
        chunk = re.sub(r"[\s?!.,;:|]+", "", chunk)
        out.append((nm, chunk[:6]))
    return out


def forms(card: int) -> List[str]:
    s = SUIT_L[card // 8].lower()
    r = RANK_FORMS[card % 8]
    out = [s + r]
    if r == "asz":
        out += [s + "ász", s + "as", s + "a5z"]
    if r == "10":
        out += [s + "io", s + "i0", s + "1o", s + "l0", s + "I0"]
    return out


def sim(chunk: str, card: int) -> float:
    c = chunk.lower().replace("á", "a").replace("ö", "o").replace("é", "e")
    return max(difflib.SequenceMatcher(None, c, f.replace("á", "a")).ratio() for f in forms(card))


def pick_card(chunk: str, options: List[int]) -> Tuple[Optional[int], bool]:
    """A chunk-hoz leginkább illő lap az options közül. (lap, egyértelmű?)"""
    if not options:
        return None, False
    sc = sorted(((sim(chunk, c), c) for c in options), reverse=True)
    if len(sc) == 1:
        return sc[0][1], sc[0][0] >= 0.3
    return sc[0][1], sc[0][0] > sc[1][0] + 1e-9 and sc[0][0] >= 0.4


def parse_tricks(n: int, hands: Dict[str, FrozenSet[int]], P: Optional[Dict[int, str]] = None) -> dict:
    players = list(hands)
    t = example_text(n, P)
    sec = trick_section(t)
    plays = split_plays(sec, players)
    return {"plays": plays, "n_plays": len(plays), "section_len": len(sec)}


if __name__ == "__main__":
    d = json.load(open(KNOWLEDGE / "diagrams.json", encoding="utf-8"))
    P = pages_text()
    for k in sorted(d, key=int):
        if d[k]["status"] != "ok":
            continue
        hands = {nm: frozenset(c) for nm, c in d[k]["reading"]["hands"].items()}
        r = parse_tricks(int(k), hands, P)
        print(f"{k:>2}. példa: {r['n_plays']:2d} kijátszott lap felismerve ({r['n_plays'] // 3} ütés)")

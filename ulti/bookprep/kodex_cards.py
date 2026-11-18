"""
kodex_cards.py - A Kovács-féle Ultikodex lapjelöléseinek értelmezése.

A könyv jelölése: színek P (piros), T (tök), Z (zöld), M (makk); rangok: ász, 10, k (király), f (felső),
a (alsó), 9, 8, 7. A kártyaazonosító megegyezik a rules.py-éval: id = szín*8 + rang
(szín: 0 piros, 1 tök, 2 zöld, 3 makk; rang: 0 VII, 1 VIII, 2 IX, 3 X, 4 Alsó, 5 Felső, 6 Király, 7 Ász).
A modell csak a nyomtatott jelölést másolja, az értelmezés itt, determinisztikusan történik.
"""
from __future__ import annotations

import re
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

SUIT_ID: Dict[str, int] = {"P": 0, "T": 1, "Z": 2, "M": 3}
SUIT_NAME = ["piros", "tök", "zöld", "makk"]
RANK_NAME = ["VII", "VIII", "IX", "X", "alsó", "felső", "király", "ász"]

_RANK_TOKENS: Dict[str, int] = {
    "7": 0, "8": 1, "9": 2, "10": 3, "a": 4, "f": 5, "k": 6, "asz": 7,
}
_OCR_FIX = str.maketrans({"I": "1", "l": "1", "O": "0", "o": "0", "ö": "0", "g": "9", "B": "8", "S": "5", "|": "1"})


def norm_rank_token(tok: str) -> Optional[int]:
    """Egy rangjelölés (pl. 'ász', '10', 'f', 'a', 'k', '9') -> 0..7, vagy None, ha nem értelmezhető."""
    t = tok.strip().strip(".,;:()[]").strip()
    if not t:
        return None
    low = t.lower().replace("á", "a")
    if low in ("asz", "as", "ász"):
        return 7
    if low in ("t£t", "tit", "fe", "fel", "felso"):
        return 5
    if low in _RANK_TOKENS:
        return _RANK_TOKENS[low]
    fixed = t.translate(_OCR_FIX)
    if fixed in ("10", "1"):
        return 3
    if fixed in ("7", "8", "9"):
        return int(fixed) - 7
    return None


def parse_rank_list(s: str) -> Optional[List[int]]:
    """'ász, 10, f, a, 9' -> rangok listája. Az üres jelölés ('–', '-', '—', 'nincs') üres lista. Hibára None."""
    s = s.strip()
    if s in ("", "-", "–", "—", "−", "nincs", "nincs lap", "üres"):
        return []
    parts = [p for p in re.split(r"[,;]|\s+", s) if p.strip()]
    out: List[int] = []
    for p in parts:
        if p in ("-", "–", "—", "−"):
            continue
        r = norm_rank_token(p)
        if r is None:
            return None
        out.append(r)
    return out


def card_id(suit_letter: str, rank: int) -> int:
    return SUIT_ID[suit_letter.upper()] * 8 + rank


def parse_cell(suit_letter: str, s: str) -> Optional[FrozenSet[int]]:
    """Egy szín-cella ('ász, 10, f') kártyaazonosítói. Ismétlődő rang vagy értelmezhetetlen jelölés: None."""
    rs = parse_rank_list(s)
    if rs is None or len(set(rs)) != len(rs):
        return None
    return frozenset(card_id(suit_letter, r) for r in rs)


def parse_card_text(s: str) -> Optional[int]:
    """'P a' / 'Z k' / 'T ász' / 'M 10' -> kártyaazonosító (a talon-doboz és az ütéstábla jelölése)."""
    m = re.match(r"^\s*([PTZMptzm])\s*[.:]?\s*(\S+)\s*$", s)
    if not m:
        return None
    r = norm_rank_token(m.group(2))
    return None if r is None else card_id(m.group(1), r)


def card_str(c: int) -> str:
    return f"{SUIT_NAME[c // 8]} {RANK_NAME[c % 8]}"


def deck_problems(hands: Sequence[FrozenSet[int]], last_talon: Sequence[int]) -> List[str]:
    """Ellenőrzés: 3 db 10 lapos kéz + az utolsó talonsor (2 lap) = pontosan a 32 lapos pakli."""
    probs: List[str] = []
    for i, h in enumerate(hands):
        if len(h) != 10:
            probs.append(f"{i + 1}. kéz {len(h)} lapos (10 kellene)")
    allc: List[int] = [c for h in hands for c in h] + list(last_talon)
    seen: Dict[int, int] = {}
    for c in allc:
        seen[c] = seen.get(c, 0) + 1
    dup = sorted(c for c, n in seen.items() if n > 1)
    miss = sorted(set(range(32)) - set(seen))
    if dup:
        probs.append("ismétlődő: " + ", ".join(card_str(c) for c in dup))
    if miss:
        probs.append("hiányzik: " + ", ".join(card_str(c) for c in miss))
    return probs

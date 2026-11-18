"""
book_features.py - A könyvi tanácsok MÉRHETŐ jellemzőkké alakítva.

Minden jellemző egy konkrét, idézettel igazolt könyvi tanácsból jön (BOOK[i].quote szó szerint megtalálható a
forrásfájlban: kodex_advice.json / book_knowledge.json = Kovács Endre Ultikodex, rabloulti_biblia.txt). A jellemző
CSAK mérhető mennyiség (pl. "hány piros lapot tettünk a talonba"); hogy a tanács igaz-e, és mennyit ér, azt
a szimuláció dönti el (book_policy.py): a súlyt a modell tanulja, a könyv nem ad küszöböt.

A jellemzők csak a kézből (10 lap), az aduból és a lerakott két lapból (disc) számolódnak, mint a features.py-ban.
A védői nézetben disc = () (a védő nem látja a talont), ott a lerakásos jellemzők 0-k.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from rules import TP

KODEX = "kodex_advice.json"
KNOW = "book_knowledge.json"
BIBLIA = "rabloulti_biblia.txt"


@dataclass(frozen=True)
class BookFeature:
    name: str
    group: str      # a csoportos A/B méréshez: talon | szerkezet | ulti40_20 | betli | durch
    source: str     # a fájl, amelyben az idézet szó szerint szerepel
    quote: str      # szó szerinti részlet a forrásból
    meaning: str    # mit mér a jellemző


BOOK: List[BookFeature] = [
    BookFeature("disc_red", "talon", KODEX, "Piros színű lapot általában ne tegyünk a talonba",
                "a talonba tett piros színű lapok száma (ha a piros nem adu)"),
    BookFeature("disc_ax7", "talon", KODEX, "Ászt, 10-est, hetest lehetőleg ne tegyünk a talonba",
                "a talonba tett ász, X, VII lapok száma"),
    BookFeature("disc_low", "talon", KODEX, "az alsó, a 9-es és a 8-as lapokat a talonba",
                "a talonba tett alsó, IX, VIII lapok száma"),
    BookFeature("disc_thin", "talon", KODEX, "csak egy-két lapunk van, lehetőleg ne helyezzük",
                "a talonba tett lapok közül hány olyan színű, amelyből a 12 lapos kézben legfeljebb 2 volt"),
    BookFeature("disc_high", "talon", KNOW, "legmagasabb értékű nélkülözhető lapokat tegyük a talonba",
                "a talonba tett felső, király, ász lapok száma (durchmarsnál és betlinél ellentétes előjel várható)"),
    BookFeature("weak_tens", "talon", BIBLIA, "ásszal fedezett tízes",
                "gyengén fedezett X: mellette csak VII/VIII van a színből (a könyv szerint a tízest ász/király-felső fedezi)"),
    BookFeature("n_suits", "szerkezet", KODEX, "minél kevesebb színű lap maradjon",
                "hány színű lap marad a kézben"),
    BookFeature("two_suiter", "szerkezet", KNOW, "kétszínű lapunk van",
                "pontosan kétszínű a kéz"),
    BookFeature("tens_kf", "szerkezet", KODEX, "legnagyobb kísérő lappal, lehetőleg királlyal vagy felsővel",
                "hány X van király vagy felső kísérővel (nem adu színben)"),
    BookFeature("ulti4", "ulti40_20", KODEX, "legalább négy adu van az adu 7-essel",
                "legalább 4 adu és ott az adu VII"),
    BookFeature("ulti5", "ulti40_20", KODEX, "Ultimót legalább 5 aduval",
                "legalább 5 adu és ott az adu VII"),
    BookFeature("c40_core", "ulti40_20", BIBLIA, "köztük ásszal és tízessel",
                "legalább 4 adu, benne az ász és az X"),
    BookFeature("c20_core", "ulti40_20", BIBLIA, "ásszal fedezett tízes",
                "van nem adu szín, amelyben király, felső, ász és X is megvan (20 + fedezett tízes)"),
    BookFeature("betli_low_run", "betli", KODEX, "alacsony értékű lapjaink vannak, többnyire sorozatban",
                "betlinél: a VII-től összefüggő alacsony sorozatok hossza (színenként, összegezve)"),
    BookFeature("betli_high_excess", "betli", BIBLIA, "megtarthatunk egy felsőt vagy királyt",
                "betlinél: a felső/király/ász lapok száma az egy fölött"),
    BookFeature("sure_tops", "durch", KODEX, "Durchmarsra csak akkor szabad vállalkozni, ha 10 biztos ütőlappal",
                "a színenként az ásztól összefüggő (biztos) ütőlapok száma"),
    BookFeature("durch_sure8", "durch", KODEX, "ha 10 biztos ütőlappal",
                "legalább 8 biztos ütőlap"),
]

NB = len(BOOK)
BOOK_NAMES = [b.name for b in BOOK]
GROUPS = ["talon", "szerkezet", "ulti40_20", "betli", "durch"]


def group_mask(groups: Sequence[str]) -> List[float]:
    """Az adott csoportok jellemzőinek maszkja (1 = aktív)."""
    gs = set(groups)
    return [1.0 if b.group in gs else 0.0 for b in BOOK]


def book_vector(h: Sequence[int], trump: int, disc: Sequence[int] = ()) -> List[float]:
    """NB hosszú vektor. h: a 10 lap lerakás után, trump: -1 ha nincs adu, disc: a lerakott két lap."""
    f = [0.0] * NB
    hs = set(h)
    cs: List[List[int]] = [[], [], [], []]
    for x in h:
        cs[x >> 3].append(x & 7)
    full = [0, 0, 0, 0]
    for s in range(4):
        full[s] = len(cs[s])
    for d in disc:
        full[d >> 3] += 1
    # --- lerakásos jellemzők
    for d in disc:
        r = d & 7
        if (d >> 3) == 0 and trump != 0:
            f[0] += 1
        if r in (7, 3, 0):
            f[1] += 1
        if r in (4, 2, 1):
            f[2] += 1
        if full[d >> 3] <= 2:
            f[3] += 1
        if r >= 5:
            f[4] += 1
    # --- szerkezet
    n_suits = 0
    tens_kf = 0
    weak = 0
    for s in range(4):
        rs = cs[s]
        if rs:
            n_suits += 1
        if s == trump:
            continue
        if 3 in rs:
            if 6 in rs or 5 in rs:
                tens_kf += 1
            if len(rs) >= 2 and all(r <= 1 or r == 3 for r in rs) and any(r <= 1 for r in rs):
                weak += 1
    f[5] = weak
    f[6] = n_suits
    f[7] = 1.0 if n_suits == 2 else 0.0
    f[8] = tens_kf
    # --- ulti / 40-100 / 20-100
    if trump >= 0:
        tr = cs[trump]
        n = len(tr)
        has7 = 0 in tr
        f[9] = 1.0 if (n >= 4 and has7) else 0.0
        f[10] = 1.0 if (n >= 5 and has7) else 0.0
        f[11] = 1.0 if (n >= 4 and 7 in tr and 3 in tr) else 0.0
        for s in range(4):
            if s != trump and all(r in cs[s] for r in (7, 3, 6, 5)):
                f[12] = 1.0
    # --- betli
    if trump < 0:
        run_tot = 0
        n_high = 0
        for s in range(4):
            rs = set(cs[s])
            run = 0
            while run in rs:
                run += 1
            run_tot += run
            n_high += sum(1 for r in rs if r >= 5)
        f[13] = run_tot
        f[14] = max(0, n_high - 1)
    # --- durchmars: biztos ütőlapok
    tops = 0
    for s in range(4):
        pw = sorted((TP[r] for r in cs[s]) if trump >= 0 else cs[s], reverse=True)
        run = 0
        while run < len(pw) and pw[run] == 7 - run:
            run += 1
        tops += run
    f[15] = tops
    f[16] = 1.0 if tops >= 8 else 0.0
    return f

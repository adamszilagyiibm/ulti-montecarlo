"""
uibridge.py - A böngészős UI (ulti.html) állapotából rules.Game-et épít a licit-, a kontra- és az elemző végpontokhoz.

Információs szabály: a motor csak azt kapja, amit a döntő (bot) játékos valódi játékban látna: a saját kezét, a saját
talonját (ha ő rakta le), a licit menetét és a kontrákat. A többi kéz és a mások által letett talon helyén
véletlen, DE lapokkal kitöltött (a motor ezeket sosem olvassa: a talon-modell a licitnaplóból következtet).

Kártyaazonosító: szín*8 + rang (a UI-ban és a Pythonban azonos); vállalás: a CONTRACTS indexe (azonos a UI-val).
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence

from bridge import card_from_id
from mcplay import mc_choose
from policy import LearnedPolicy
from rules import CONTRACTS, DECK, Game, find_contract, is_trump_contract, nxt, pts, rank, suit


def _ids(xs: Any) -> List[int]:
    return [int(x) for x in xs]


def _parse_blog(raw: Sequence[Sequence[Any]], p: int) -> List[tuple]:
    """A licitnapló a UI-ból. Más játékosok talonletétele a motor számára ismeretlen: csak a sajátunkat tartjuk meg."""
    out: List[tuple] = []
    for e in raw:
        if e[0] == "bid":
            who = int(e[1])
            ci = int(e[2])
            if not 0 <= ci < len(CONTRACTS):
                raise ValueError("ismeretlen vállalás a naplóban")
            out.append(("bid", who, ci, int(e[3]), int(e[4]), tuple(_ids(e[5])) if who == p else ()))
        elif e[0] in ("pickup", "pass", "concede"):
            out.append((e[0], int(e[1])))
        else:
            raise ValueError("ismeretlen licit-esemény")
    return out


def game_from_snapshot(s: Dict[str, Any], rng: random.Random) -> Game:
    """Licit- vagy kontra-állapot a döntő játékos nézetéből. s: lásd a make_ui.py horgát."""
    p = int(s["p"])
    if p not in (0, 1, 2):
        raise ValueError("érvénytelen játékos")
    hand = _ids(s["hand"])
    sizes = _ids(s["sizes"])
    if len(sizes) != 3 or sizes[p] != len(hand) or len(set(hand)) != len(hand) or any(not 0 <= c < 32 for c in hand):
        raise ValueError("a kéz nem egyezik a méretekkel")
    talon = _ids(s.get("talon") or [])
    n_talon = int(s.get("talon_n", len(talon)))
    if n_talon not in (0, 2) or (talon and len(talon) != 2):
        raise ValueError("a talon 0 vagy 2 lap")
    known = set(hand) | set(talon)
    if len(known) != len(hand) + len(talon):
        raise ValueError("ismétlődő lap")
    pool = [c for c in DECK if c not in known]
    rng.shuffle(pool)
    if len(pool) < sum(sizes) - len(hand) + (n_talon - len(talon)):
        raise ValueError("nincs elég lap a kitöltéshez")
    hands: List[List[int]] = [[], [], []]
    hands[p] = list(hand)
    k = 0
    for q in range(3):
        if q != p:
            hands[q] = pool[k:k + sizes[q]]
            k += sizes[q]
    g = Game.from_hands(int(s["dealer"]), hands)
    g.hands = hands
    g.talon = list(talon) if talon else pool[k:k + n_talon]
    g.blog = _parse_blog(s.get("blog", []), p)
    b = s.get("bid")
    if b:
        ci = int(b["ci"])
        if not 0 <= ci < len(CONTRACTS):
            raise ValueError("ismeretlen vállalás")
        g.bid = (ci, int(b["suit"]) if is_trump_contract(ci) else -1, int(b["seat"]))
    g.passes = int(s.get("passes", 0))
    g.turn = p
    return g


def bid_action(s: Dict[str, Any], pol: LearnedPolicy, rng: random.Random) -> Dict[str, Any]:
    """A bot licit-döntése: {"type": bid|pickup|pass|concede, "ci", "suit", "disc": [id]}."""
    g = game_from_snapshot(s, random.Random(0))        # a kitöltés külön véletlen: a politika rng-je ne tolódjon el
    a = pol.bid_action(g, g.turn, rng)
    out: Dict[str, Any] = {"type": a["type"]}
    if a["type"] == "bid":
        out.update(ci=int(a["ci"]), suit=int(a["suit"]), disc=[int(c) for c in a["disc"]])
    return out


def kontra_choice(s: Dict[str, Any], pol: LearnedPolicy, rng: random.Random) -> List[str]:
    """A bot kontra/rekontra döntése: a kontrázott komponensek kulcsai."""
    s = dict(s)
    s["talon_n"] = 2
    g = game_from_snapshot(s, random.Random(0))
    p = g.turn
    if g.bid is None:
        raise ValueError("nincs licit")
    g.end_bidding()
    km = s.get("kmult") or [{}, {}, {}]
    g.kmult = [{str(k): int(v) for k, v in km[q].items()} for q in range(3)]
    g.kidx = g.kseq.index(p)
    g.turn = p
    return list(pol.kontra(g, p, rng))


# ---------------------------------------------------------------- kijátszás (Monte Carlo)
def _tricks(raw: Sequence[Sequence[Dict[str, Any]]]) -> List[List[tuple]]:
    return [[(int(x["p"]), int(x["c"])) for x in tr] for tr in raw]


def game_from_play_snapshot(s: Dict[str, Any], rng: random.Random) -> Game:
    """Kijátszási állapot a döntő játékos nézetéből (ütések, aktuális ütés, bemondások, kontrák). A látatlan lapokat
    véletlenül osztja ki (a mc_choose úgyis újraosztja); a nyitott játék felfedett kezét a s["reveal"] adja.
    s: lásd a make_ui.py snapshot() függvényét (ci, decl, trump, hand, sizes, talon, trick, hist, legal, kmult, forced, marr, reveal)."""
    p = int(s["p"])
    decl = int(s["decl"])
    if p not in (0, 1, 2) or decl not in (0, 1, 2):
        raise ValueError("érvénytelen játékos")
    comps = set(s.get("comps", []))
    if s.get("ci") is not None:
        ci = int(s["ci"])
    else:
        found = find_contract(list(comps), bool(s.get("red")), bool(s.get("open")), str(s.get("kind", "trump")))
        ci = -1 if found is None else found
    if not 0 <= ci < len(CONTRACTS) or (comps and set(CONTRACTS[ci]["comps"]) != comps):
        raise ValueError("ismeretlen vállalás")
    c = CONTRACTS[ci]
    trump = int(s["trump"])
    if c["kind"] == "trump" and not 0 <= trump <= 3:
        raise ValueError("érvénytelen adu")
    hand = _ids(s["hand"])
    sizes = _ids(s["sizes"])
    hist, trick = _tricks(s.get("hist", [])), [(int(x["p"]), int(x["c"])) for x in s.get("trick", [])]
    played = [cd for tr in hist + [trick] for _, cd in tr]
    if len(sizes) != 3 or sizes[p] != len(hand) or len(set(hand)) != len(hand) or any(not 0 <= x < 32 for x in hand + played):
        raise ValueError("a kéz nem egyezik a méretekkel")
    if any(len(tr) != 3 for tr in hist) or len(trick) > 2:
        raise ValueError("érvénytelen ütésnapló")
    talon = _ids(s.get("talon") or []) if p == decl else []
    if p == decl and len(talon) != 2:
        raise ValueError("a felvevő talonja 2 lap")
    reveal = {int(q): _ids(h) for q, h in (s.get("reveal") or {}).items()}
    if any(q == p or q not in (0, 1, 2) or len(h) != sizes[q] for q, h in reveal.items()):
        raise ValueError("érvénytelen felfedett kéz")
    known = hand + talon + played + [x for h in reveal.values() for x in h]
    if len(set(known)) != len(known):
        raise ValueError("ismétlődő lap")
    pool = [x for x in DECK if x not in set(known)]
    rng.shuffle(pool)
    need = sum(sizes[q] for q in range(3) if q != p and q not in reveal) + (0 if p == decl else 2)
    if len(pool) != need:
        raise ValueError("a lapszámok nem egyeznek a látatlan lapokkal")
    hands: List[List[int]] = [[], [], []]
    hands[p] = list(hand)
    k = 0
    for q in range(3):
        if q in reveal:
            hands[q] = list(reveal[q])
        elif q != p:
            hands[q] = pool[k:k + sizes[q]]
            k += sizes[q]
    g = Game.from_hands(int(s.get("dealer", 0)), hands)
    g.hands = hands
    g.talon = talon if p == decl else pool[k:k + 2]
    g.decl, g.ci, g.trump = decl, ci, trump if c["kind"] == "trump" else -1
    g.bid = (ci, g.trump, decl)
    g.trump_game = c["kind"] == "trump"
    g.talon_pts = sum(pts(x) for x in g.talon)
    g.ulti_lock = "ulti" in c["comps"]
    km = s.get("kmult") or [{}, {}, {}]
    g.kmult = [{str(a): int(b) for a, b in km[q].items()} for q in range(3)]
    g.forced = {str(a): True for a in (s.get("forced") or {}) if (s["forced"][a] if isinstance(s["forced"], dict) else True)}
    g.marr = [[(int(m["s"]), int(m["v"])) for m in (s.get("marr") or [[], [], []])[q]] for q in range(3)]
    g.phase = "play"
    g.hist, g.trick, g.played = hist, trick, played
    g.trick_no = len(hist)
    for tr in hist:
        w = tr[g.trick_winner_idx(tr)][0]
        g.won[w].extend(cd for _, cd in tr)
        g.tcount[w] += 1
        g.leader = w
    if not hist:
        g.leader = decl
    g.last_trick = list(hist[-1]) if hist else None
    g.ulti_broken = "ulti" in c["comps"] and any(who == decl and suit(cd) == g.trump and rank(cd) == 0
                        for i, tr in enumerate(hist + ([trick] if trick else [])) for who, cd in tr if i < 9)
    g.turn = p
    expect = nxt(trick[-1][0]) if trick else g.leader
    if expect != p:
        raise ValueError(f"a snapshot nem a(z) P{p} lépése (következő: P{expect})")
    legal = sorted(_ids(s.get("legal") or []))
    if legal and legal != sorted(g.legal_cards(p)):
        raise ValueError("a szabályos lapok nem egyeznek a UI-éval")
    return g


def choose_card(s: Dict[str, Any], rng: random.Random, samples: int = 32, eps: float = 0.0,
                budget: Optional[float] = 1.5) -> Dict[str, Any]:
    """A bot lapja Monte Carlo kereséssel: {"id", "card", "why"}. Mérés: 32 minta, eps=0 -> ~40-110 ms/lépés."""
    g = game_from_play_snapshot(s, random.Random(0))
    p = int(s["p"])
    fixed = {q for q in (s.get("reveal") or {}) if int(q) != p}
    cid = mc_choose(g, p, rng, samples, eps, {int(q) for q in fixed}, budget)
    if cid not in g.legal_cards(p):
        raise ValueError("a motor nem szabályos lapot adott")
    return {"id": cid, "card": str(card_from_id(cid)), "why": f"Python motor (Monte Carlo, {samples} minta): {card_from_id(cid)}"}

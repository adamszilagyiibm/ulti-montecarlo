"""
bridge.py - Adapter a barátod JS-játékából (ulti.html) a Python motorhoz.

A JS-oldal egy "snapshot"-ot küld egy bot döntési pillanatáról (lásd ulti.html `snapshot()`),
innen `GameState` készül, a motor választ, és a lap azonosítója megy vissza.

Kártyaazonosító (JS): id = szín*8 + rang
  szín: 0 piros, 1 tök, 2 zöld, 3 makk
  rang: 0 VII, 1 VIII, 2 IX, 3 X, 4 Alsó, 5 Felső, 6 Király, 7 Ász

Információs szabály: a snapshot csak a döntő bot lapjait, a lapszámokat, a kijátszott lapokat
(és a felvevőnek a talont) tartalmazza, ugyanazt, amit a JS `botChoose` is lát.
A többi játékos kezét a látatlan lapokból töltjük fel (a motor azt nem használja, a
Monte Carlo pedig úgyis újraosztja).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from engine import Card, ContractType, GameState, HeuristicEngine, Rank, Suit, Trick

SUITS = [Suit.PIROS, Suit.TOK, Suit.ZOLD, Suit.MAKK]
RANKS = [Rank.VII, Rank.VIII, Rank.IX, Rank.X, Rank.ALSO, Rank.FELSO, Rank.KIRALY, Rank.ASZ]


def card_from_id(cid: int) -> Card:
    if not 0 <= cid < 32:
        raise ValueError(f"Érvénytelen kártyaazonosító: {cid}")
    return Card(SUITS[cid // 8], RANKS[cid % 8])


def card_to_id(card: Card) -> int:
    return SUITS.index(card.suit) * 8 + RANKS.index(card.rank)


def map_contract(kind: str, comps: List[str], red: bool, open_: bool) -> ContractType:
    """A 42 JS-vállalás leképezése a motor vállalástípusaira (a motor ennél kevesebbet ismer)."""
    if kind == "durch":
        return ContractType.SZIN_NELKULI_DURCHMARS
    if kind == "betli":
        return ContractType.TERITETT_BETLI if open_ else ContractType.BETLI
    if "ulti" in comps:
        return ContractType.ULTI
    if "c40" in comps:
        return ContractType.NEGYVEN_SZAZ
    if "c20" in comps:
        return ContractType.HUSZ_SZAZ
    if "durch" in comps:
        return ContractType.TERITETT_DURCHMARS if open_ else ContractType.DURCHMARS
    return ContractType.PIROS_PASSZ if red else ContractType.PASSZ


@dataclass
class BridgedState(GameState):
    """GameState, ahol a döntő játékos szabályos lapjait a JS adja (pl. ulti-zár: az adu VII nem adható ki)."""
    acting: int = -1
    allowed: Optional[Set[Card]] = None

    def get_legal_moves(self, player_id: int) -> List[Card]:
        if self.allowed and player_id == self.acting:
            return [c for c in self.player_hands[player_id] if c in self.allowed]
        return super().get_legal_moves(player_id)


def _trick(raw: List[Dict[str, int]], trump: Optional[Suit], is_trump_game: bool, default_lead: int) -> Trick:
    cards = [(int(t["p"]), card_from_id(int(t["c"]))) for t in raw]
    tr = Trick(lead_player_id=cards[0][0] if cards else default_lead, cards=cards)
    if len(cards) == 3:
        tr.won_by = tr.winner(trump, is_trump_game)
    return tr


def snapshot_to_state(snap: Dict[str, Any]) -> BridgedState:
    p = int(snap["p"])
    kind = snap["kind"]
    contract = map_contract(kind, list(snap.get("comps", [])), bool(snap.get("red")), bool(snap.get("open")))
    trump = SUITS[int(snap["trump"])] if int(snap["trump"]) >= 0 and kind == "trump" else None

    probe = GameState(player_hands={}, declarer_id=int(snap["decl"]), contract=contract, trump_suit=trump)
    is_tg = probe.is_trump_game

    hist = [_trick(tr, trump, is_tg, 0) for tr in snap.get("hist", [])]
    # üres ütésnél a hívó az előző ütés nyertese, az elsőnél a felvevő
    next_lead = hist[-1].won_by if hist else int(snap["decl"])
    cur = _trick(snap.get("trick", []), trump, is_tg, next_lead)

    my_hand = [card_from_id(i) for i in snap["hand"]]
    talon = [card_from_id(i) for i in snap.get("talon", [])]
    sizes = [int(n) for n in snap["sizes"]]
    if len(sizes) != 3 or sizes[p] != len(my_hand):
        raise ValueError("Hibás lapszámok a snapshotban")

    known = set(my_hand) | set(talon)
    for tr in hist + [cur]:
        known.update(c for _, c in tr.cards)
    if len(known) != len(my_hand) + len(talon) + sum(len(t.cards) for t in hist + [cur]):
        raise ValueError("Ismétlődő lap a snapshotban")
    pool = [Card(s, r) for s in SUITS for r in RANKS if Card(s, r) not in known]
    others = sum(n for q, n in enumerate(sizes) if q != p)
    if len(pool) - others != 2 - len(talon):  # a talon 2 lapja csak a felvevőnek ismert
        raise ValueError("A lapszámok nem egyeznek a látatlan lapokkal")

    hands: Dict[int, List[Card]] = {p: my_hand}
    for q in range(3):
        if q != p:
            hands[q], pool = pool[:sizes[q]], pool[sizes[q]:]

    state = BridgedState(
        player_hands=hands, declarer_id=int(snap["decl"]), contract=contract, trump_suit=trump,
        tricks_history=hist, current_trick=cur, talon=talon,
        acting=p, allowed={card_from_id(i) for i in snap.get("legal", [])} or None,
    )
    if state.next_player() != p:
        raise ValueError(f"A snapshot nem a(z) P{p} lépése (következő: P{state.next_player()})")
    return state


class EngineBridge:
    """Egy kérés: snapshot -> kártyaazonosító. A motor cserélhető (HeuristicEngine, MonteCarloPlayer, saját)."""

    def __init__(self, engine: Optional[Any] = None):
        self.engine = engine or HeuristicEngine()

    def choose(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        state = snapshot_to_state(snap)
        legal = state.get_legal_moves(state.acting)
        if not legal:
            raise ValueError("Nincs szabályos lap")
        card = legal[0] if len(legal) == 1 else self.engine.choose_move(state, state.acting)
        if card not in legal:  # a motor hibájából se kerüljön szabálytalan lap vissza
            card = legal[0]
        return {"id": card_to_id(card), "card": str(card), "why": f"Python motor: {card}"}

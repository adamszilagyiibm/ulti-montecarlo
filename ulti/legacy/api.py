"""
api.py - Átadási felület a UI számára. Nincs benne UI- vagy hálózati kód, csak JSON-barát adatok.

Az ember a 3 hely egyike (human_id), a másik kettőt a `MoveEngine` játssza.
A motor cseréje: egy `choose_move(state, player_id) -> Card` metódusú objektumot kell átadni
(lásd `MoveEngine`), a többi kód változatlan marad.

Tipikus UI-ciklus:
    s = GameSession.new(human_id=0, seed=42)
    view = s.view()                    # a UI ebből rajzol; "legal_moves" a játszható lapok
    s.play("makk Ász")                 # az ember lépése (ValueError, ha szabálytalan)
    view = s.view()                    # a gépek a következő emberi lépésig már lejátszottak
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Protocol

from engine import Card, GameState, HeuristicEngine, Rank, Suit, UltiTutor
from refinement_loop import UltiScenarioGenerator


class MoveEngine(Protocol):
    """A cserélhető játékmotor interfésze (HeuristicEngine és MonteCarloPlayer is ezt valósítja meg)."""

    def choose_move(self, state: GameState, player_id: int) -> Card: ...


# ---------------------------------------------------------------------------
# Lap <-> JSON
# ---------------------------------------------------------------------------

def card_to_dict(card: Card) -> Dict[str, Any]:
    return {"suit": card.suit.value, "rank": card.rank.value, "points": card.point_value, "id": str(card)}


def parse_card(value: Any) -> Card:
    """Elfogadja a "makk Ász" szöveget és a {"suit": "makk", "rank": "Ász"} objektumot is."""
    if isinstance(value, Card):
        return value
    if isinstance(value, dict):
        suit, rank = value["suit"], value["rank"]
    else:
        suit, _, rank = str(value).partition(" ")
    try:
        return Card(Suit(suit), Rank(rank))
    except ValueError as exc:
        raise ValueError(f"Ismeretlen lap: {value!r}") from exc


# ---------------------------------------------------------------------------
# Játékmenet
# ---------------------------------------------------------------------------

class GameSession:
    def __init__(self, state: GameState, human_id: int, engine: MoveEngine):
        if human_id not in state.player_hands:
            raise ValueError("human_id csak 0, 1 vagy 2 lehet")
        self.state = state
        self.human_id = human_id
        self.engine = engine
        self.log: List[Dict[str, Any]] = []   # minden lejátszott lap, sorrendben
        self._tutor: Optional[UltiTutor] = None
        self.advance_bots()

    @classmethod
    def new(cls, human_id: int = 0, seed: Optional[int] = None, engine: Optional[MoveEngine] = None) -> "GameSession":
        state = UltiScenarioGenerator.generate_deal(random.Random(seed))
        return cls(state, human_id, engine or HeuristicEngine())

    # -- állapot ----------------------------------------------------------

    def is_over(self) -> bool:
        return self.state.is_over()

    def is_human_turn(self) -> bool:
        return not self.is_over() and self.state.next_player() == self.human_id

    def legal_moves(self) -> List[Card]:
        return self.state.get_legal_moves(self.human_id) if self.is_human_turn() else []

    def view(self) -> Dict[str, Any]:
        """Az ember szemszögéből látható állapot (az ellenfelek lapjai rejtettek)."""
        s = self.state
        out: Dict[str, Any] = {
            "contract": s.contract.value,
            "trump_suit": s.trump_suit.value if s.trump_suit else None,
            "declarer_id": s.declarer_id,
            "human_id": self.human_id,
            "next_player": None if self.is_over() else s.next_player(),
            "is_human_turn": self.is_human_turn(),
            "my_hand": [card_to_dict(c) for c in s.player_hands[self.human_id]],
            "legal_moves": [card_to_dict(c) for c in self.legal_moves()],
            "hand_sizes": {pid: len(h) for pid, h in s.player_hands.items()},
            "current_trick": [{"player": pid, "card": card_to_dict(c)} for pid, c in s.current_trick.cards],
            "tricks_won": {pid: sum(1 for t in s.tricks_history if t.won_by == pid) for pid in s.player_hands},
            "tricks_played": len(s.tricks_history),
            "game_over": self.is_over(),
        }
        # a felvevő a talont ismeri, a védők nem
        if self.human_id == s.declarer_id:
            out["talon"] = [card_to_dict(c) for c in s.talon]
        if self.is_over():
            out["result"] = {
                "declarer_points": s.declarer_points(),
                "declarer_tricks": s.declarer_tricks(),
                "declarer_succeeded": s.declarer_succeeded(),
            }
        return out

    # -- lépések ----------------------------------------------------------

    def _record(self, pid: int, card: Card, by_human: bool) -> None:
        self.log.append({"player": pid, "card": card_to_dict(card), "by_human": by_human,
                         "trick_no": len(self.state.tricks_history) + 1})

    def advance_bots(self) -> List[Dict[str, Any]]:
        """A gépek lépnek az ember soráig (vagy a játszma végéig). Visszaadja az újonnan lejátszott lapokat."""
        start = len(self.log)
        while not self.is_over() and self.state.next_player() != self.human_id:
            pid = self.state.next_player()
            card = self.engine.choose_move(self.state, pid)
            self._record(pid, card, by_human=False)
            self.state.play_card(pid, card)
        return self.log[start:]

    def play(self, card: Any) -> Dict[str, Any]:
        """Az ember lépése, utána a gépek az ember következő soráig. Szabálytalan lapra ValueError."""
        if not self.is_human_turn():
            raise ValueError("Nem az ember van soron")
        c = parse_card(card)
        if c not in self.legal_moves():
            raise ValueError(f"Szabálytalan lépés (színadási/felülütési kényszer): {c}")
        self._record(self.human_id, c, by_human=True)
        self.state.play_card(self.human_id, c)
        bots = self.advance_bots()
        return {"bot_moves": bots, "view": self.view()}

    # -- Tutor (LLM, GOOGLE_API_KEY kell) -----------------------------------

    def explain(self, card: Any) -> str:
        """A Tutor megmagyarázza, hogy a megadott lap a mostani állásban miért jó vagy rossz."""
        if not self.is_human_turn():
            raise ValueError("Magyarázat csak az ember lépése előtt kérhető")
        if self._tutor is None:
            from llm import load_book
            self._tutor = UltiTutor(load_book())
        return self._tutor.explain_move(self.state, self.human_id, parse_card(card))

    def suggest(self) -> Dict[str, Any]:
        """A motor javaslata az ember számára (nem hajtja végre)."""
        if not self.is_human_turn():
            raise ValueError("Nem az ember van soron")
        return card_to_dict(self.engine.choose_move(self.state, self.human_id))

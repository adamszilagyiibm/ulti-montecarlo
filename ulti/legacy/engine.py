"""
engine.py - Rablóulti Core Domain Models, Game Rules & Tutor Engine
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path

from ulti.paths import KNOWLEDGE
from typing import List, Dict, Optional, Tuple, Any




# ============================================================================
# 1. DOMAIN ENUMS & VALUE OBJECTS
# ============================================================================

class Suit(str, Enum):
    PIROS = "piros"   # Hearts / Főszín
    TOK = "tök"       # Bells
    ZOLD = "zöld"     # Leaves
    MAKK = "makk"     # Acorns


class Rank(str, Enum):
    VII = "VII"
    VIII = "VIII"
    IX = "IX"
    ALSO = "Alsó"
    FELSO = "Felső"
    KIRALY = "Király"
    X = "X"
    ASZ = "Ász"


class ContractType(str, Enum):
    PASSZ = "Passz"
    PIROS_PASSZ = "Piros passz"
    NEGYVEN_SZAZ = "40-100"
    HUSZ_SZAZ = "20-100"
    ULTI = "Ulti"
    BETLI = "Betli"
    TERITETT_BETLI = "Terített betli"
    DURCHMARS = "Durchmars"
    SZIN_NELKULI_DURCHMARS = "Szín nélküli durchmars"
    TERITETT_DURCHMARS = "Terített durchmars"


@dataclass(frozen=True)
class Card:
    suit: Suit
    rank: Rank

    @property
    def point_value(self) -> int:
        """Ász és X 10 pontot érnek, a többiek 0-t."""
        return 10 if self.rank in (Rank.ASZ, Rank.X) else 0

    def get_rank_power(self, is_trump_game: bool) -> int:
        """
        Visszaadja a lap erejét a játéktípus függvényében:
        Adus játékban: VII(1) < VIII(2) < IX(3) < Alsó(4) < Felső(5) < Király(6) < X(7) < Ász(8)
        Adu nélküli (Betli, Szín nélküli): VII(1) < VIII(2) < IX(3) < X(4) < Alsó(5) < Felső(6) < Király(7) < Ász(8)
        """
        if is_trump_game:
            order = [Rank.VII, Rank.VIII, Rank.IX, Rank.ALSO, Rank.FELSO, Rank.KIRALY, Rank.X, Rank.ASZ]
        else:
            order = [Rank.VII, Rank.VIII, Rank.IX, Rank.X, Rank.ALSO, Rank.FELSO, Rank.KIRALY, Rank.ASZ]
        return order.index(self.rank) + 1

    def __repr__(self) -> str:
        return f"{self.suit.value} {self.rank.value}"


# ============================================================================
# 2. GAME STATE & TRICK VALIDATION
# ============================================================================

@dataclass
class Trick:
    lead_player_id: int
    cards: List[Tuple[int, Card]] = field(default_factory=list)  # (player_id, Card)
    won_by: Optional[int] = None

    def is_complete(self) -> bool:
        return len(self.cards) == 3

    def winner(self, trump_suit: Optional[Suit], is_trump_game: bool) -> int:
        """Kiszámolja az ütést megnyerő játékos ID-jét a hivatalos szabályok szerint."""
        if not self.cards:
            raise ValueError("Üres ütésnek nincs nyertese!")

        lead_suit = self.cards[0][1].suit
        best_player, best_card = self.cards[0]
        best_is_trump = (best_card.suit == trump_suit) if is_trump_game else False

        for player_id, card in self.cards[1:]:
            card_is_trump = (card.suit == trump_suit) if is_trump_game else False

            if card_is_trump and not best_is_trump:
                best_player, best_card, best_is_trump = player_id, card, True
            elif card_is_trump and best_is_trump:
                if card.get_rank_power(is_trump_game) > best_card.get_rank_power(is_trump_game):
                    best_player, best_card = player_id, card
            elif not card_is_trump and not best_is_trump:
                if card.suit == lead_suit and card.get_rank_power(is_trump_game) > best_card.get_rank_power(is_trump_game):
                    best_player, best_card = player_id, card

        return best_player


@dataclass
class GameState:
    player_hands: Dict[int, List[Card]]
    declarer_id: int
    contract: ContractType
    trump_suit: Optional[Suit]
    tricks_history: List[Trick] = field(default_factory=list)
    current_trick: Trick = field(default_factory=lambda: Trick(lead_player_id=0))
    talon: List[Card] = field(default_factory=list)
    announcements: Dict[int, List[int]] = field(default_factory=dict)  # pl. 20, 40 bemondások

    @property
    def is_trump_game(self) -> bool:
        return self.contract not in (ContractType.BETLI, ContractType.TERITETT_BETLI, ContractType.SZIN_NELKULI_DURCHMARS)

    def get_legal_moves(self, player_id: int) -> List[Card]:
        """
        Kiszámítja a szabályos lépéseket a szigorú Ulti-szabályok szerint:
        1. Színadási kényszer
        2. Felülütési kényszer azonos színben
        3. Színhiány esetén aduzási (lopási) kényszer felülütéssel
        4. Aduhiány esetén tetszőleges dobás
        """
        hand = self.player_hands[player_id]
        if not self.current_trick.cards:
            return hand.copy()

        lead_card = self.current_trick.cards[0][1]
        lead_suit = lead_card.suit
        same_suit_cards = [c for c in hand if c.suit == lead_suit]

        current_winner_id = self.current_trick.winner(self.trump_suit, self.is_trump_game)
        winning_card = next(c for pid, c in self.current_trick.cards if pid == current_winner_id)

        # 1. Van hívott szín
        if same_suit_cards:
            if winning_card.suit == lead_suit:
                overtaking_cards = [
                    c for c in same_suit_cards 
                    if c.get_rank_power(self.is_trump_game) > winning_card.get_rank_power(self.is_trump_game)
                ]
                if overtaking_cards:
                    return overtaking_cards
            return same_suit_cards

        # 2. Nincs hívott szín -> Aduzási kényszer (ha van adu a játékban)
        if self.is_trump_game and self.trump_suit:
            trump_cards = [c for c in hand if c.suit == self.trump_suit]
            if trump_cards:
                if winning_card.suit == self.trump_suit:
                    overtaking_trumps = [
                        c for c in trump_cards 
                        if c.get_rank_power(self.is_trump_game) > winning_card.get_rank_power(self.is_trump_game)
                    ]
                    if overtaking_trumps:
                        return overtaking_trumps
                return trump_cards

        # 3. Nincs sem hívott szín, sem adu -> Tetszőleges lap dobható
        return hand.copy()

    # ------------------------------------------------------------------
    # Játékmenet
    # ------------------------------------------------------------------

    def next_player(self) -> int:
        """Következő lépő (jobbra tartás, 3 játékos)."""
        if not self.current_trick.cards:
            return self.current_trick.lead_player_id
        return (self.current_trick.cards[-1][0] + 1) % 3

    def is_over(self) -> bool:
        return len(self.tricks_history) == 10

    def clone(self) -> "GameState":
        return GameState(
            player_hands={pid: list(h) for pid, h in self.player_hands.items()},
            declarer_id=self.declarer_id,
            contract=self.contract,
            trump_suit=self.trump_suit,
            tricks_history=[Trick(t.lead_player_id, list(t.cards), t.won_by) for t in self.tricks_history],
            current_trick=Trick(self.current_trick.lead_player_id, list(self.current_trick.cards)),
            talon=list(self.talon),
            announcements={k: list(v) for k, v in self.announcements.items()},
        )

    def play_card(self, player_id: int, card: Card) -> None:
        """Kijátssza a lapot; teljes ütésnél lezárja azt, a nyertes hív legközelebb."""
        if card not in self.get_legal_moves(player_id):
            raise ValueError(f"Szabálytalan lépés (renonsz): P{player_id} {card}")
        self.player_hands[player_id].remove(card)
        self.current_trick.cards.append((player_id, card))
        if self.current_trick.is_complete():
            winner = self.current_trick.winner(self.trump_suit, self.is_trump_game)
            self.current_trick.won_by = winner
            self.tricks_history.append(self.current_trick)
            self.current_trick = Trick(lead_player_id=winner)

    # ------------------------------------------------------------------
    # Kimenetel
    # ------------------------------------------------------------------

    def declarer_points(self) -> int:
        """Felvevő ütéspontjai (ász/X = 10, utolsó ütés +10). A talon a védőké."""
        pts = 0
        for i, t in enumerate(self.tricks_history):
            if t.won_by == self.declarer_id:
                pts += sum(c.point_value for _, c in t.cards)
                if i == 9:
                    pts += 10
        return pts

    def declarer_tricks(self) -> int:
        return sum(1 for t in self.tricks_history if t.won_by == self.declarer_id)

    def declarer_succeeded(self) -> bool:
        """A vállalás teljesült-e (befejezett játszmára). A 20/40 bemondások nincsenek modellezve."""
        c = self.contract
        if c in (ContractType.BETLI, ContractType.TERITETT_BETLI):
            return self.declarer_tricks() == 0
        if c in (ContractType.DURCHMARS, ContractType.SZIN_NELKULI_DURCHMARS, ContractType.TERITETT_DURCHMARS):
            return self.declarer_tricks() == 10
        pts = self.declarer_points()
        last = self.tricks_history[-1]
        if c == ContractType.ULTI:
            last_card = next(cd for pid, cd in last.cards if pid == last.won_by)
            ulti_ok = (last.won_by == self.declarer_id and last_card.rank == Rank.VII
                       and last_card.suit == self.trump_suit)
            return ulti_ok and pts > 45
        if c in (ContractType.NEGYVEN_SZAZ, ContractType.HUSZ_SZAZ):
            return pts >= 100 - (40 if c == ContractType.NEGYVEN_SZAZ else 20)
        return pts > 45  # Passz / Piros passz: több pont, mint a védőknek (90 összesen)


# ============================================================================
# 3. HEURISZTIKUS ALGORITMIKUS JÁTÉKMOTOR (FAST EVALUATION)
# ============================================================================

class HeuristicEngine:
    def __init__(self, heuristics_path: str = str(KNOWLEDGE / "heuristics_weights.json")):
        self.heuristics_path = heuristics_path
        self.weights = self._load_weights()

    def _load_weights(self) -> Dict[str, Any]:
        p = Path(self.heuristics_path)
        if not p.exists():
            return self._default_weights()
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def _default_weights(self) -> Dict[str, Any]:
        return {
            "lead_preferences": {
                "lead_trump_when_declarer": 25.0,
                "lead_singleton_for_defense": 15.0,
                "lead_high_card_first": 10.0,
                "lead_safe_ten": 5.0
            },
            "defense_weights": {
                "feed_ten_or_ace_to_partner": 20.0,
                "save_trump_seven_for_last": 30.0,
                "keep_stoppers_in_betli": 25.0
            },
            "card_eval_bias": {
                "Rank.ASZ": 10.0,
                "Rank.X": 8.0,
                "Rank.KIRALY": 4.0,
                "Rank.FELSO": 3.0,
                "Rank.ALSO": 2.0,
                "Rank.IX": 1.0,
                "Rank.VIII": 0.5,
                "Rank.VII": 0.0
            }
        }

    def choose_move(self, state: GameState, player_id: int) -> Card:
        """Kiválasztja a legoptimálisabb szabályos lapot a heurisztikus pontszámok alapján."""
        legal_moves = state.get_legal_moves(player_id)
        if len(legal_moves) == 1:
            return legal_moves[0]

        best_card = legal_moves[0]
        max_score = float("-inf")

        is_declarer = (player_id == state.declarer_id)
        is_lead = len(state.current_trick.cards) == 0

        for card in legal_moves:
            score = 0.0
            bias = self.weights.get("card_eval_bias", {}).get(f"Rank.{card.rank.name}", 1.0)
            score += bias

            # 1. Hívási szituáció
            if is_lead:
                if is_declarer and state.is_trump_game and card.suit == state.trump_suit:
                    score += self.weights.get("lead_preferences", {}).get("lead_trump_when_declarer", 20.0)
                if not is_declarer and card.rank == Rank.ASZ:
                    score += self.weights.get("lead_preferences", {}).get("lead_high_card_first", 10.0)
            # 2. Ütésbe adás szituáció
            else:
                lead_player = state.current_trick.lead_player_id
                if not is_declarer and lead_player != state.declarer_id and card.point_value == 10:
                    score += self.weights.get("defense_weights", {}).get("feed_ten_or_ace_to_partner", 20.0)

            # Ulti VII védelme
            if state.contract == ContractType.ULTI and card.rank == Rank.VII and card.suit == state.trump_suit:
                if len(state.tricks_history) < 9:
                    score -= self.weights.get("defense_weights", {}).get("save_trump_seven_for_last", 50.0)

            if score > max_score:
                max_score = score
                best_card = card

        return best_card


# ============================================================================
# 4. GEMINI OKTATÓ MOTOR (TUTOR ENGINE)
# ============================================================================

class UltiTutor:
    def __init__(self, book_text: str, api_key: Optional[str] = None):
        # az elavult Gemini-s oktató: a szerver nem használja, ezért az importjai csak itt (lustán) töltődnek be
        from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
        from llm import make_llm
        self.llm = make_llm(temperature=0.2, api_key=api_key)
        self.book_text = book_text

        system_template = (
            "Te egy mester Rablóulti oktató és versenybíró vagy. Az alábbi szakkönyv alapján elemzed a játékállásokat:\n\n"
            "--- KÖNYVSZÖVEG KEZDETE ---\n"
            "{book_text}\n"
            "--- KÖNYVSZÖVEG VÉGE ---\n\n"
            "Elemezd a megadott játékhelyzetet pontos szabály- és stratégiai hivatkozásokkal! Válaszolj professzionális, érthető stílusban."
        )
        human_template = (
            "Játékállás:\n"
            "- Vállalás: {contract}\n"
            "- Adu: {trump_suit}\n"
            "- Felvevő ID: {declarer_id}\n"
            "- Játékos ID: {player_id} (Kéz: {hand})\n"
            "- Aktuális ütés: {current_trick}\n"
            "- Választott lépés: {chosen_card}\n\n"
            "Kérdés: Miért jó vagy nem jó ez a lépés elméletileg és taktikailag a könyv alapján?"
        )

        self.prompt = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(system_template),
            HumanMessagePromptTemplate.from_template(human_template)
        ])
        self.chain = self.prompt | self.llm

    def explain_move(self, state: GameState, player_id: int, chosen_card: Card) -> str:
        from llm import content_text
        res = self.chain.invoke({
            "book_text": self.book_text,
            "contract": state.contract.value,
            "trump_suit": state.trump_suit.value if state.trump_suit else "Nincs",
            "declarer_id": state.declarer_id,
            "player_id": player_id,
            "hand": str(state.player_hands[player_id]),
            "current_trick": str([f"P{pid}:{c}" for pid, c in state.current_trick.cards]),
            "chosen_card": str(chosen_card)
        })
        return content_text(res.content)

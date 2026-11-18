"""
refinement_loop.py - Automatizált teszt- és tanuló hurok (LLM-as-a-Judge + súlyfinomhangolás).

Folyamat egy iterációban:
  1. véletlen, szabályos játékállás generálása (akár játszma közepén),
  2. a heurisztikus motor lépést választ,
  3. a Judge (Gemini, teljes könyv a System Promptban) értékeli a lépést,
  4. eltérés esetén a JSON-ban tárolt súly módosul, a motor újratölti.
"""
from ulti.paths import KNOWLEDGE
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Tuple

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from engine import Card, ContractType, GameState, HeuristicEngine, Rank, Suit, Trick
from llm import load_book, make_llm

HEURISTICS_PATH = KNOWLEDGE / "heuristics_weights.json"
ADJUSTABLE_SECTIONS = ("lead_preferences", "defense_weights", "card_eval_bias")
MAX_ABS_DELTA = 3.0     # egy iterációban ennyivel módosulhat egy súly
WEIGHT_RANGE = (0.0, 60.0)


# ============================================================================
# 1. JUDGE OUTPUT SCHEMA + INTERFACE
# ============================================================================

class JudgeEvaluation(BaseModel):
    is_optimal: bool = Field(description="A motor lépése optimális volt-e a könyv elmélete szerint")
    suggested_card: str = Field(description="A javasolt lap 'szín rang' formában (pl. 'makk Ász'), ha a motor hibázott; különben üres")
    reasoning: str = Field(description="A döntés elméleti magyarázata a könyv alapján")
    weight_key_to_adjust: str = Field(description="A módosítandó súlykulcs a megadott listából; üres, ha nincs módosítás")
    suggested_delta: float = Field(description="Súlymódosítás (pozitív: a motor preferálja jobban; negatív: kevésbé)")


class Judge(Protocol):
    def evaluate(self, state: GameState, player_id: int, chosen: Card, weight_keys: List[str]) -> JudgeEvaluation: ...


# ============================================================================
# 2. SCENARIO GENERATOR
# ============================================================================

class UltiScenarioGenerator:
    """Véletlenszerű, szabályos leosztás és licithelyzet; a játszma a motorral előre is játszható."""

    CONTRACTS = [
        ContractType.PASSZ, ContractType.PIROS_PASSZ, ContractType.NEGYVEN_SZAZ,
        ContractType.HUSZ_SZAZ, ContractType.ULTI, ContractType.BETLI, ContractType.DURCHMARS,
    ]

    @staticmethod
    def get_full_deck() -> List[Card]:
        return [Card(suit=s, rank=r) for s in Suit for r in Rank]

    @classmethod
    def generate_deal(cls, rng: random.Random) -> GameState:
        deck = cls.get_full_deck()
        rng.shuffle(deck)
        hands = {i: sorted(deck[i * 10:(i + 1) * 10], key=lambda c: (c.suit.value, c.rank.value)) for i in range(3)}
        contract = rng.choice(cls.CONTRACTS)
        if contract == ContractType.PIROS_PASSZ:
            trump: Optional[Suit] = Suit.PIROS
        elif contract == ContractType.BETLI:
            trump = None
        else:
            trump = rng.choice(list(Suit))
        declarer = rng.randrange(3)
        return GameState(
            player_hands=hands, declarer_id=declarer, contract=contract, trump_suit=trump,
            current_trick=Trick(lead_player_id=declarer), talon=deck[30:32],
        )

    @classmethod
    def generate_random_situation(
        cls, engine: Optional[HeuristicEngine] = None, rng: Optional[random.Random] = None,
        max_cards_played: int = 27,
    ) -> Tuple[GameState, int]:
        """Visszaad egy állást és a soron lévő játékos azonosítóját (0-27 lap már lejátszva)."""
        rng = rng or random.Random()
        engine = engine or HeuristicEngine()
        state = cls.generate_deal(rng)
        for _ in range(rng.randint(0, max_cards_played)):
            pid = state.next_player()
            state.play_card(pid, engine.choose_move(state, pid))
        return state, state.next_player()


# ============================================================================
# 3. LLM JUDGE (Gemini, Zero-RAG)
# ============================================================================

class GeminiJudge:
    def __init__(self, book_text: str):
        self.book_text = book_text
        llm = make_llm(temperature=0.0).with_structured_output(JudgeEvaluation)
        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "Te egy szigorú Rablóulti-bíró vagy. A szakkönyv alapján ellenőrzöd egy játékmotor lépéseit.\n\n"
             "--- KÖNYVSZÖVEG KEZDETE ---\n{book_text}\n--- KÖNYVSZÖVEG VÉGE ---\n\n"
             "A motor súlyai (a módosítható kulcsok): {weight_keys}\n"
             "Ha a lépés nem optimális, adj meg EGY kulcsot a listából és egy előjeles módosítást "
             "(-3 és +3 között). Ha optimális, a kulcs legyen üres, a módosítás 0."),
            ("human",
             "Játékállás:\n- Vállalás: {contract}\n- Adu: {trump_suit}\n- Felvevő: P{declarer_id}\n"
             "- Soron lévő játékos: P{player_id} (kéz: {hand})\n- Szabályos lépések: {legal_moves}\n"
             "- Az asztalon lévő ütés: {trick}\n- Lejátszott ütések száma: {tricks_done}\n"
             "- A motor választása: {chosen_card}\n\nOptimális volt a lépés?"),
        ])
        self.chain = prompt | llm

    def evaluate(self, state: GameState, player_id: int, chosen: Card, weight_keys: List[str]) -> JudgeEvaluation:
        return self.chain.invoke({
            "book_text": self.book_text,
            "weight_keys": ", ".join(weight_keys),
            "contract": state.contract.value,
            "trump_suit": state.trump_suit.value if state.trump_suit else "nincs",
            "declarer_id": state.declarer_id,
            "player_id": player_id,
            "hand": str(state.player_hands[player_id]),
            "legal_moves": str(state.get_legal_moves(player_id)),
            "trick": str([f"P{pid}:{c}" for pid, c in state.current_trick.cards]) or "üres (te hívsz)",
            "tricks_done": len(state.tricks_history),
            "chosen_card": str(chosen),
        })


# ============================================================================
# 4. SÚLYFRISSÍTÉS
# ============================================================================

def list_weight_keys(weights: dict) -> List[str]:
    return [f"{sec}.{k}" for sec in ADJUSTABLE_SECTIONS for k in weights.get(sec, {})]


def apply_weight_delta(weights: dict, key: str, delta: float) -> Optional[Tuple[float, float]]:
    """Validált, korlátozott módosítás. Kulcs: 'szekció.név'. Visszatér (régi, új) értékkel, vagy None-nal."""
    section, _, name = key.partition(".")
    if section not in ADJUSTABLE_SECTIONS or name not in weights.get(section, {}) or delta == 0:
        return None
    delta = max(-MAX_ABS_DELTA, min(MAX_ABS_DELTA, delta))
    old = weights[section][name]
    new = round(max(WEIGHT_RANGE[0], min(WEIGHT_RANGE[1], old + delta)), 3)
    weights[section][name] = new
    return old, new


@dataclass
class IterationResult:
    player_id: int
    chosen: str
    is_optimal: bool
    adjusted: Optional[str]
    reasoning: str


class WeightRefiner:
    def __init__(self, judge: Judge, heuristics_path: Path = HEURISTICS_PATH):
        self.judge = judge
        self.path = Path(heuristics_path)

    def step(self, engine: HeuristicEngine, state: GameState, player_id: int) -> IterationResult:
        chosen = engine.choose_move(state, player_id)
        ev = self.judge.evaluate(state, player_id, chosen, list_weight_keys(engine.weights))
        adjusted = None
        if not ev.is_optimal and ev.weight_key_to_adjust:
            change = apply_weight_delta(engine.weights, ev.weight_key_to_adjust, ev.suggested_delta)
            if change:
                adjusted = f"{ev.weight_key_to_adjust}: {change[0]} -> {change[1]}"
                self.path.write_text(json.dumps(engine.weights, ensure_ascii=False, indent=2), encoding="utf-8")
        return IterationResult(player_id, str(chosen), ev.is_optimal, adjusted, ev.reasoning)


# ============================================================================
# 5. HURKOK
# ============================================================================

def run_refinement_loop(
    num_iterations: int = 10, judge: Optional[Judge] = None,
    heuristics_path: Path = HEURISTICS_PATH, seed: Optional[int] = None,
) -> List[IterationResult]:
    judge = judge or GeminiJudge(load_book())
    engine = HeuristicEngine(str(heuristics_path))
    refiner = WeightRefiner(judge, heuristics_path)
    rng = random.Random(seed)
    results: List[IterationResult] = []

    print(f"[*] Finomhangolási hurok indul ({num_iterations} iteráció)...")
    for i in range(1, num_iterations + 1):
        state, pid = UltiScenarioGenerator.generate_random_situation(engine, rng)
        res = refiner.step(engine, state, pid)
        results.append(res)
        print(f"[{i}/{num_iterations}] P{pid} {res.chosen} | optimális: {res.is_optimal}"
              + (f" | módosítva {res.adjusted}" if res.adjusted else ""))

    ok = sum(r.is_optimal for r in results)
    print(f"[+] Kész: {ok}/{len(results)} lépés optimális.")
    return results


if __name__ == "__main__":
    run_refinement_loop()

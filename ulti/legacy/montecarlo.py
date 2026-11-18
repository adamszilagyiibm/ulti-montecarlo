"""
montecarlo.py - Determinizált Monte Carlo játékos (a HeuristicEngine a rollout-policy).
"""
import random
from typing import Dict, Optional

from engine import Card, GameState, HeuristicEngine, Rank, Suit


class MonteCarloPlayer:
    """
    Információhalmaz-determinizálás: a játékos által nem látott lapokat véletlenül
    szétosztja az ellenfelek között, majd a HeuristicEngine-nel végigjátssza a játszmát.
    Az a lap a legjobb, amelyik a legtöbb mintában teljesíti a játékos céljait.
    """

    def __init__(self, engine: HeuristicEngine, n_samples: int = 30,
                 epsilon: float = 0.15, seed: Optional[int] = None):
        self.engine = engine
        self.n_samples = n_samples
        self.epsilon = epsilon  # rollout-ban ennyi eséllyel véletlen szabályos lap
        self.rng = random.Random(seed)

    def _determinize(self, state: GameState, player_id: int) -> GameState:
        s = state.clone()
        seen = set(s.player_hands[player_id])
        for t in s.tricks_history:
            seen.update(c for _, c in t.cards)
        seen.update(c for _, c in s.current_trick.cards)
        if player_id == s.declarer_id:
            seen.update(s.talon)  # a felvevő ismeri a talont
        pool = [Card(su, ra) for su in Suit for ra in Rank if Card(su, ra) not in seen]
        self.rng.shuffle(pool)
        for pid in s.player_hands:
            if pid != player_id:
                n = len(s.player_hands[pid])
                s.player_hands[pid], pool = pool[:n], pool[n:]
        return s

    def _rollout(self, state: GameState) -> GameState:
        while not state.is_over():
            pid = state.next_player()
            if self.rng.random() < self.epsilon:
                card = self.rng.choice(state.get_legal_moves(pid))
            else:
                card = self.engine.choose_move(state, pid)
            state.play_card(pid, card)
        return state

    def choose_move(self, state: GameState, player_id: int) -> Card:
        legal = state.get_legal_moves(player_id)
        if len(legal) == 1:
            return legal[0]
        wants_success = player_id == state.declarer_id
        wins: Dict[Card, int] = {c: 0 for c in legal}
        for _ in range(self.n_samples):
            det = self._determinize(state, player_id)
            for card in legal:
                sim = det.clone()
                sim.play_card(player_id, card)
                if self._rollout(sim).declarer_succeeded() == wants_success:
                    wins[card] += 1
        return max(legal, key=lambda c: wins[c])

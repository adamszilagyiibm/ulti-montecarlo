"""
test_engine.py - Egységtesztek (API kulcs nélkül futnak). Futtatás: python -m unittest -v
"""
import json
import random
import tempfile
import unittest
from pathlib import Path

from engine import Card, ContractType, GameState, HeuristicEngine, Rank, Suit, Trick
from montecarlo import MonteCarloPlayer
from refinement_loop import (
    JudgeEvaluation, UltiScenarioGenerator, WeightRefiner, apply_weight_delta,
    list_weight_keys, run_refinement_loop,
)

C = Card


def play_full_game(seed: int, player=None) -> GameState:
    rng = random.Random(seed)
    state = UltiScenarioGenerator.generate_deal(rng)
    player = player or HeuristicEngine()
    while not state.is_over():
        pid = state.next_player()
        state.play_card(pid, player.choose_move(state, pid))
    return state


class RulesTest(unittest.TestCase):
    def make(self, hands, trump=Suit.ZOLD, contract=ContractType.PASSZ, lead=0):
        return GameState(player_hands=hands, declarer_id=0, contract=contract, trump_suit=trump,
                         current_trick=Trick(lead_player_id=lead))

    def test_must_follow_suit_and_overtake(self):
        s = self.make({0: [C(Suit.MAKK, Rank.IX)], 1: [C(Suit.MAKK, Rank.VII), C(Suit.MAKK, Rank.ASZ), C(Suit.ZOLD, Rank.VII)],
                       2: []})
        s.play_card(0, C(Suit.MAKK, Rank.IX))
        self.assertEqual(s.get_legal_moves(1), [C(Suit.MAKK, Rank.ASZ)])

    def test_must_trump_without_suit(self):
        s = self.make({0: [C(Suit.MAKK, Rank.IX)], 1: [C(Suit.ZOLD, Rank.VII), C(Suit.TOK, Rank.ASZ)], 2: []})
        s.play_card(0, C(Suit.MAKK, Rank.IX))
        self.assertEqual(s.get_legal_moves(1), [C(Suit.ZOLD, Rank.VII)])

    def test_free_discard_without_suit_and_trump(self):
        s = self.make({0: [C(Suit.MAKK, Rank.IX)], 1: [C(Suit.TOK, Rank.VII), C(Suit.PIROS, Rank.ASZ)], 2: []})
        s.play_card(0, C(Suit.MAKK, Rank.IX))
        self.assertEqual(len(s.get_legal_moves(1)), 2)

    def test_trick_winner_and_next_lead(self):
        s = self.make({0: [C(Suit.MAKK, Rank.ASZ)], 1: [C(Suit.ZOLD, Rank.VII)], 2: [C(Suit.MAKK, Rank.X)]})
        for pid, c in [(0, C(Suit.MAKK, Rank.ASZ)), (1, C(Suit.ZOLD, Rank.VII)), (2, C(Suit.MAKK, Rank.X))]:
            s.play_card(pid, c)
        self.assertEqual(s.tricks_history[0].won_by, 1)  # az adu VII üt
        self.assertEqual(s.next_player(), 1)

    def test_illegal_move_raises(self):
        s = self.make({0: [C(Suit.MAKK, Rank.IX)], 1: [C(Suit.MAKK, Rank.ASZ), C(Suit.TOK, Rank.VII)], 2: []})
        s.play_card(0, C(Suit.MAKK, Rank.IX))
        with self.assertRaises(ValueError):
            s.play_card(1, C(Suit.TOK, Rank.VII))

    def test_betli_rank_order(self):
        self.assertLess(C(Suit.MAKK, Rank.ALSO).get_rank_power(False), C(Suit.MAKK, Rank.X).get_rank_power(False) + 10)
        self.assertGreater(C(Suit.MAKK, Rank.ALSO).get_rank_power(False), C(Suit.MAKK, Rank.X).get_rank_power(False))
        self.assertLess(C(Suit.MAKK, Rank.ALSO).get_rank_power(True), C(Suit.MAKK, Rank.X).get_rank_power(True))


class GameFlowTest(unittest.TestCase):
    def test_full_games_complete_with_consistent_scoring(self):
        for seed in range(100):
            s = play_full_game(seed)
            self.assertEqual(len(s.tricks_history), 10)
            self.assertTrue(all(len(h) == 0 for h in s.player_hands.values()))
            talon_pts = sum(c.point_value for c in s.talon)  # a talon a védőké
            total = sum(c.point_value for t in s.tricks_history for _, c in t.cards)
            self.assertEqual(total + talon_pts, 80)
            declarer = s.declarer_points()
            defenders = sum(sum(c.point_value for _, c in t.cards) + (10 if i == 9 else 0)
                            for i, t in enumerate(s.tricks_history) if t.won_by != s.declarer_id)
            self.assertEqual(declarer + defenders + talon_pts, 90)

    def test_clone_is_independent(self):
        s = UltiScenarioGenerator.generate_deal(random.Random(1))
        c = s.clone()
        c.play_card(c.next_player(), c.get_legal_moves(c.next_player())[0])
        self.assertEqual(len(s.current_trick.cards), 0)
        self.assertEqual(sum(len(h) for h in s.player_hands.values()), 30)

    def test_monte_carlo_plays_legal_game(self):
        mc = MonteCarloPlayer(HeuristicEngine(), n_samples=2, seed=3)
        s = play_full_game(5, mc)
        self.assertTrue(s.is_over())

    def test_random_situation_has_legal_moves(self):
        rng = random.Random(7)
        for _ in range(50):
            s, pid = UltiScenarioGenerator.generate_random_situation(rng=rng)
            self.assertEqual(s.next_player(), pid)
            self.assertTrue(s.get_legal_moves(pid))


class RefinementTest(unittest.TestCase):
    def test_apply_weight_delta_clamps_and_validates(self):
        w = {"lead_preferences": {"a": 10.0}, "defense_weights": {}, "card_eval_bias": {}}
        self.assertEqual(apply_weight_delta(w, "lead_preferences.a", 100), (10.0, 13.0))
        self.assertEqual(apply_weight_delta(w, "lead_preferences.a", -100), (13.0, 10.0))
        self.assertIsNone(apply_weight_delta(w, "lead_preferences.nincs", 1))
        self.assertIsNone(apply_weight_delta(w, "ismeretlen.a", 1))
        self.assertIsNone(apply_weight_delta(w, "lead_preferences.a", 0))

    def test_loop_updates_json_with_fake_judge(self):
        class FakeJudge:
            def evaluate(self, state, player_id, chosen, weight_keys):
                return JudgeEvaluation(is_optimal=False, suggested_card="", reasoning="teszt",
                                       weight_key_to_adjust="lead_preferences.lead_trump_when_declarer",
                                       suggested_delta=-2.0)

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.json"
            p.write_text(json.dumps(HeuristicEngine(str(Path(d) / "nincs.json")).weights), encoding="utf-8")
            before = json.loads(p.read_text())["lead_preferences"]["lead_trump_when_declarer"]
            res = run_refinement_loop(3, judge=FakeJudge(), heuristics_path=p, seed=1)
            after = json.loads(p.read_text())["lead_preferences"]["lead_trump_when_declarer"]
            self.assertEqual(len(res), 3)
            self.assertAlmostEqual(after, before - 6.0)

    def test_list_weight_keys(self):
        keys = list_weight_keys(HeuristicEngine("nincs.json").weights)
        self.assertIn("defense_weights.save_trump_seven_for_last", keys)


if __name__ == "__main__":
    unittest.main()

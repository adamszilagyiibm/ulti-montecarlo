"""
test_api.py - A UI-nak átadott GameSession felület tesztjei (API kulcs nélkül futnak).
"""
import json
import unittest

from api import GameSession, card_to_dict, parse_card
from engine import Card, HeuristicEngine, Rank, Suit


class ParseCardTest(unittest.TestCase):
    def test_roundtrip_text_and_dict(self):
        c = Card(Suit.MAKK, Rank.ASZ)
        self.assertEqual(parse_card("makk Ász"), c)
        self.assertEqual(parse_card({"suit": "makk", "rank": "Ász"}), c)
        self.assertEqual(parse_card(card_to_dict(c)), c)
        self.assertEqual(parse_card(c), c)

    def test_unknown_card(self):
        with self.assertRaises(ValueError):
            parse_card("kékszín Ász")


class SessionTest(unittest.TestCase):
    def test_human_seat_gets_turn_and_view_is_json(self):
        for human in (0, 1, 2):
            s = GameSession.new(human_id=human, seed=11)
            v = s.view()
            self.assertTrue(v["is_human_turn"] or v["game_over"])
            json.dumps(v)  # JSON-szerializálható

    def test_hidden_information(self):
        s = GameSession.new(human_id=0, seed=3)
        v = s.view()
        self.assertEqual(len(v["my_hand"]), v["hand_sizes"][0])
        self.assertNotIn("opponent_hands", v)
        self.assertEqual("talon" in v, v["declarer_id"] == 0)

    def test_full_game_with_human_playing_first_legal_card(self):
        for seed in range(30):
            for human in (0, 1, 2):
                s = GameSession.new(human_id=human, seed=seed)
                guard = 0
                while not s.is_over():
                    legal = s.legal_moves()
                    self.assertTrue(legal)
                    s.play(legal[0])
                    guard += 1
                    self.assertLess(guard, 11)
                v = s.view()
                self.assertTrue(v["game_over"])
                self.assertIn("result", v)
                self.assertEqual(len(s.log), 30)
                self.assertEqual(sum(v["tricks_won"].values()), 10)

    def test_illegal_move_rejected_and_state_unchanged(self):
        s = GameSession.new(human_id=0, seed=2)
        # kényszerített helyzetet keresünk: van szabálytalan lap a kézben
        for seed in range(60):
            s = GameSession.new(human_id=0, seed=seed)
            while not s.is_over():
                legal = s.legal_moves()
                illegal = [c for c in s.state.player_hands[0] if c not in legal]
                if illegal:
                    before = json.dumps(s.view(), sort_keys=True)
                    with self.assertRaises(ValueError):
                        s.play(illegal[0])
                    self.assertEqual(before, json.dumps(s.view(), sort_keys=True))
                    return
                s.play(legal[0])
        self.fail("nem találtam szabálytalan lapot a mintában")

    def test_play_out_of_turn_rejected(self):
        s = GameSession.new(human_id=0, seed=1)
        while not s.is_over():
            s.play(s.legal_moves()[0])
        with self.assertRaises(ValueError):
            s.play("makk Ász")

    def test_pluggable_engine(self):
        class FirstLegal:
            calls = 0

            def choose_move(self, state, player_id):
                FirstLegal.calls += 1
                return state.get_legal_moves(player_id)[0]

        s = GameSession.new(human_id=1, seed=4, engine=FirstLegal())
        while not s.is_over():
            s.play(s.legal_moves()[0])
        self.assertEqual(FirstLegal.calls, 20)  # 2 gép x 10 lap

    def test_suggest_is_legal(self):
        s = GameSession.new(human_id=2, seed=9, engine=HeuristicEngine())
        self.assertIn(parse_card(s.suggest()), s.legal_moves())


if __name__ == "__main__":
    unittest.main()

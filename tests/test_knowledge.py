"""A talon-modell, a determinizált Monte Carlo, a licitnapló, a könyv lapjelölései és az idézet-ellenőrzés tesztjei."""
from ulti.paths import POLICY
import random
import unittest

import numpy as np

import rules
from extract_book import merge
from extract_kodex import quote_in_source
from extract_facts import contract_index, text_magnitude
from kodex_cards import card_id, deck_problems, parse_card_text, parse_cell
from mcplay import _deal_unseen, mc_choose
from policy import LearnedPolicy, ctx_of
from rules import Game
from talon import N_GROUPS, N_STAGES, TalonModel


def _play_bids(g: Game, rng: random.Random) -> None:
    """Egy rövid licit: az első felvevő a talon megtartásával parti-t mond, a többiek passzolnak."""
    p = g.fh
    disc = g.hands[p][:2]
    ci = 0
    su = next(s for s in rules.suits_for(ci))
    while g.validate_bid(p, disc, ci, su):
        disc = rng.sample(g.hands[p], 2)
    g.do_bid(p, disc, ci, su)


class BidLog(unittest.TestCase):
    def test_bid_is_logged_and_cloned(self) -> None:
        g = Game.new_deal(0, random.Random(5))
        _play_bids(g, random.Random(1))
        self.assertTrue(g.blog and g.blog[-1][0] == "bid")
        self.assertEqual(g.clone().blog, g.blog)
        self.assertIsNot(g.clone().blog, g.blog)


class TalonModelTests(unittest.TestCase):
    def test_json_roundtrip_and_shape(self) -> None:
        m = TalonModel()
        self.assertEqual(m.rates.shape, (N_STAGES, N_GROUPS, 8))
        self.assertTrue(np.allclose(TalonModel.from_json(m.to_json()).rates, m.rates))
        self.assertEqual(TalonModel.from_json([[1.0]]).rates.shape, m.rates.shape)     # hibás alak: alapérték

    def test_sample_gives_two_unseen_cards(self) -> None:
        for sd in range(20):
            g = Game.new_deal(sd % 3, random.Random(sd))
            _play_bids(g, random.Random(sd))
            for p in range(3):
                if p == g.bid[2]:
                    continue
                t = TalonModel().sample(g, p, random.Random(sd))
                self.assertEqual(len(set(t)), 2)
                self.assertFalse(set(t) & set(g.hands[p]))


class MonteCarlo(unittest.TestCase):
    def test_determinization_keeps_my_hand_and_sizes(self) -> None:
        g = Game.new_deal(0, random.Random(3))
        _play_bids(g, random.Random(3))
        while g.phase == "bid":
            g.do_pass(g.turn)
        while g.phase == "kontra":
            g.do_kontra(g.turn, [])
        p = g.turn
        mine, sizes = sorted(g.hands[p]), {q: len(g.hands[q]) for q in range(3)}
        c = g.clone()
        _deal_unseen(c, p, random.Random(9))
        self.assertEqual(sorted(c.hands[p]), mine)
        self.assertEqual({q: len(c.hands[q]) for q in range(3)}, sizes)
        every = [x for h in c.hands for x in h] + list(c.talon)
        self.assertEqual(len(set(every)), len(every))

    def test_mc_choose_returns_legal_card(self) -> None:
        g = Game.new_deal(0, random.Random(4))
        _play_bids(g, random.Random(4))
        while g.phase == "bid":
            g.do_pass(g.turn)
        while g.phase == "kontra":
            g.do_kontra(g.turn, [])
        p = g.turn
        self.assertIn(mc_choose(g, p, random.Random(1), samples=3), g.legal_cards(p))


class BookCards(unittest.TestCase):
    def test_card_notation(self) -> None:
        self.assertEqual(parse_card_text("P a"), card_id("P", 4))
        self.assertEqual(parse_card_text("T ász"), card_id("T", 7))
        self.assertEqual(parse_card_text("M 10"), card_id("M", 3))
        self.assertIsNone(parse_card_text("xx"))
        self.assertEqual(parse_cell("Z", "ász, 10, f"), frozenset({card_id("Z", 7), card_id("Z", 3), card_id("Z", 5)}))
        self.assertIsNone(parse_cell("Z", "ász, ász"))            # ismétlődő rang
        self.assertEqual(parse_cell("Z", "—"), frozenset())

    def test_full_deck_check(self) -> None:
        deck = list(range(32))
        hands = [frozenset(deck[0:10]), frozenset(deck[10:20]), frozenset(deck[20:30])]
        self.assertEqual(deck_problems(hands, deck[30:32]), [])
        self.assertTrue(deck_problems(hands, [0, 31]))             # a 0. lap kétszer: hibát kell jeleznie


class BookFacts(unittest.TestCase):
    def tearDown(self) -> None:
        rules.set_ruleset("friend")

    def test_contract_index_uses_book_ruleset(self) -> None:
        rules.set_ruleset("book")
        i = contract_index({"components": ["40-100", "ulti"], "trump": "P"})
        self.assertEqual(rules.CONTRACTS[i]["name"], "Piros 40-100 ulti")
        j = contract_index({"components": ["4asz"], "trump": "T"})
        self.assertEqual(rules.CONTRACTS[j]["comps"], {"asz4": 4})
        self.assertIsNone(contract_index({"components": ["nincs-ilyen"], "trump": "P"}))

    def test_result_text_magnitude(self) -> None:
        self.assertEqual(text_magnitude("nyertek összesen : 24-24 pontot"), 24)
        self.assertEqual(text_magnitude("Egyik sem fizet a másiknak: kutyakefe"), 0)
        self.assertEqual(text_magnitude("Csaba nyeresége összesen : 48148 pont"), 48)       # '48-48' OCR-ja
        self.assertIsNone(text_magnitude("nincs itt eredmény"))


class QuoteCheck(unittest.TestCase):
    SRC = "A rabló magasabb egységű játékot köteles vállalni, mint az előző játékos. A felvevő a talonlapokat köteles a lapjai közé tenni."

    def test_verbatim_and_ocr_noise_pass_invented_fails(self) -> None:
        self.assertTrue(quote_in_source("A rabló magasabb egységű játékot köteles vállalni, mint az előző játékos.", self.SRC))
        self.assertTrue(quote_in_source("A rabló magasabb egységu játékot köteles vállalni mint az elózó játékos.", self.SRC))
        self.assertFalse(quote_in_source("A rabló bármikor passzolhat és nem kell semmit vállalnia a licit során.", self.SRC))
        self.assertFalse(quote_in_source("A rabló", self.SRC))            # túl rövid idézet nem fogadható el

    def test_merge_counts_distinct_prompts_as_votes(self) -> None:
        a = {"pages": "1-2", "pos": 100, "prompt": 0, "topic": "licit", "contract": "ulti", "rule": "r", "condition": "", "direction": "tedd", "quote": "q" * 20}
        b = {**a, "pos": 120, "prompt": 1}
        c = {**a, "pos": 900, "prompt": 0}
        out = merge([a, b, c], 3)
        self.assertEqual(sorted(x["votes"] for x in out), [1, 2])


class PolicyCompat(unittest.TestCase):
    def tearDown(self) -> None:
        rules.set_ruleset("friend")

    def test_ctx_follows_active_ruleset(self) -> None:
        n_friend = len(rules.CONTRACTS)
        rules.set_ruleset("book")
        self.assertGreater(len(rules.CONTRACTS), n_friend)
        ci = next(i for i, c in enumerate(rules.CONTRACTS) if "asz4" in c["comps"])
        self.assertEqual(ctx_of(ci)[-1], 1.0)
        rules.set_ruleset("friend")
        self.assertEqual(ctx_of(0)[-1], 0.0)

    def test_old_saved_weights_still_load(self) -> None:
        pol = LearnedPolicy.load(POLICY / "learned_policy.json")
        self.assertEqual(len(pol.w["ulti"]), len(LearnedPolicy().w["ulti"]))


if __name__ == "__main__":
    unittest.main()

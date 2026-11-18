"""Gyors tesztek a kontra_rule.ExpectedMoneyKontra-hoz (<10 s)."""
import random
import unittest

import arena
from kontra_rule import ExpectedMoneyKontra, make_kontra_policy
from policy import make_learned
from rules import CONTRACTS, Game

SEEDS = range(16900000, 16900060)


def _kontra_states(pol):
    """A kontra-fázisok állapotai (a régi, küszöbös politikával játszott licit után)."""
    out = []

    class Spy:
        name = "spy"

        def bid_action(self, g, p, rng):
            return pol.bid_action(g, p, rng)

        def kontra(self, g, p, rng):
            out.append((g.clone(), p))
            return pol.kontra(g, p, rng)

        def card(self, g, p, rng):
            return pol.card(g, p, rng)

    s = Spy()
    for sd in SEEDS:
        arena.run_deal(Game.new_deal(sd % 3, random.Random(sd)), [s, s, s], random.Random(sd))
    return out


class KontraRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pol = make_kontra_policy()
        cls.states = _kontra_states(make_learned())

    def test_factory_type_and_cache(self):
        self.assertIsInstance(self.pol, ExpectedMoneyKontra)
        self.assertIs(self.pol, make_kontra_policy())

    def test_states_exist(self):
        self.assertGreater(len(self.states), 20)

    def test_valid_options_and_determinism(self):
        for g, p in self.states:
            opts = set(g.kontra_options(p))
            a = self.pol.kontra(g, p, random.Random(1))
            b = self.pol.kontra(g, p, random.Random(2))
            self.assertEqual(a, b)
            self.assertTrue(set(a) <= opts, (a, opts))
            self.assertEqual(len(a), len(set(a)))

    def test_must_kontra_not_violated(self):
        for g, p in self.states:
            if p == g.decl:
                continue
            a = self.pol.kontra(g, p, random.Random(0))
            for k in g.kontra_options(p):
                if g.must_kontra(p, k):
                    self.assertIn(k, a)
            h = g.clone()
            h.do_kontra(p, a)                     # a Game.do_kontra sem dob hibát
            for k in g.kontra_options(p):
                if g.must_kontra(p, k):
                    self.assertEqual(h.kmult[p].get(k), 2)

    def test_other_behaviour_unchanged(self):
        base = make_learned()
        self.assertEqual(self.pol.params, base.params)
        for sd in range(16900000, 16900010):
            g = Game.new_deal(sd % 3, random.Random(sd))
            r = random.Random(sd)
            self.assertEqual(self.pol.bid_action(g, g.turn, r), base.bid_action(g, g.turn, random.Random(sd)))

    def test_gain_signs(self):
        # nagyon gyenge felvevő-esély (p kicsi) -> pozitív, nagyon erős -> negatív várható kontra-nyereség (analitikus ág)
        pol = ExpectedMoneyKontra(self.pol, self.pol.model, mode="analytic")
        for g, p in self.states:
            if p != g.decl and "betli" in g.kontra_options(p):
                v = CONTRACTS[g.ci]["comps"]["betli"]
                self.assertAlmostEqual(
                    pol.defender_gain(g, p, "betli", -1) / v,
                    (2 * (1 - pol._p_def(g.hands[p], g.ci, -1, "betli")) - 1) * 1.0, places=6)
                break


if __name__ == "__main__":
    unittest.main()

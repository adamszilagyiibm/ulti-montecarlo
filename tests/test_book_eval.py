"""test_book_eval.py - gyors teszt a book_eval.py-hoz: egy eset kevés világgal, szerkezet és determinizmus."""
from ulti.paths import KNOWLEDGE, POLICY
import json
import unittest
from pathlib import Path

import book_eval as be
import rules
from policy import LearnedPolicy

rules.set_ruleset("friend")      # a book_eval importja a könyv díjaira állít: a többi teszt miatt visszaállítjuk

ROOT = Path(__file__).parent


class BookEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rules.set_ruleset("book")
        cls.pol = LearnedPolicy.load(POLICY / "learned_book.json")
        cases = be.load_cases(json.loads((KNOWLEDGE / "replay.json").read_text(encoding="utf-8")))
        cls.case = next(c for c in cases if be.case_key(c) == "2.0")      # a motor és a könyv itt eltér

    @classmethod
    def tearDownClass(cls) -> None:
        rules.set_ruleset("friend")

    def test_structure_and_determinism(self) -> None:
        a = be.evaluate_case(self.pol, self.case, n=6, seed=3, workers=0)
        b = be.evaluate_case(self.pol, self.case, n=6, seed=3, workers=0)
        self.assertEqual(a, b)
        self.assertEqual(a["status"], "ok")
        for k in ("book", "engine", "book_mean", "engine_mean", "diff", "ci_half", "verdict", "kind"):
            self.assertIn(k, a)
        self.assertIn(a["verdict"], ("motor", "könyv", "nem eldönthető"))
        self.assertAlmostEqual(a["diff"], a["engine_mean"] - a["book_mean"], delta=0.02)

    def test_world_is_valid_bid_state(self) -> None:
        import random
        w = be.make_world(self.case, random.Random(1))
        self.assertEqual(len(w.hands[self.case["seat"]]), 12)
        self.assertEqual(len({c for h in w.hands for c in h}), 32)
        self.assertIsNone(w.validate_bid(self.case["seat"], self.case["book_disc"], self.case["book_ci"], self.case["book_trump"]))

    def test_summary(self) -> None:
        r = be.evaluate_case(self.pol, self.case, n=4, seed=1, workers=0)
        s = be.summarize([r])
        self.assertEqual(s["evaluated"], 1)
        self.assertEqual(sum(s["verdicts"].values()), 1)


if __name__ == "__main__":
    unittest.main()

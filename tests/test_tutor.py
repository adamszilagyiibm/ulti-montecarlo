"""A tanító bot (tutor.py) tesztjei: napló-visszajátszás, könyvi tanácskeresés, elemzés, JSON-kimenet."""
from ulti.paths import POLICY
import json
import random
import unittest

import arena
import rules
import tutor
from policy import make_learned


def _deal(seed: int):
    pol = make_learned(str(POLICY / "learned_policy.json"))
    return arena.play_deal(seed % 3, seed, [pol, pol, pol])


class TutorTests(unittest.TestCase):
    def setUp(self) -> None:
        rules.set_ruleset("friend")

    def test_record_json_roundtrip_and_replay_matches_result(self) -> None:
        for seed in (0, 3, 5):
            g = _deal(seed)
            rec = tutor.DealRecord.from_game(g)
            rec2 = tutor.DealRecord.from_json(json.loads(json.dumps(rec.to_json())))
            last = None
            for _kind, st, _ev in tutor.replay(rec2):
                last = st
            self.assertEqual(last.result["change"], g.result["change"])

    def test_book_advice_is_quoted_and_contract_specific_first(self) -> None:
        adv = tutor.book_advice("kontra", ["ulti"], 2)
        self.assertTrue(adv)
        for a in adv:
            self.assertTrue(a["quote"] and a["pages"])
        self.assertIn("ultim", adv[0]["text"].lower())
        self.assertEqual(tutor.book_advice("nincs_ilyen_tema"), [])

    def test_paired_difference(self) -> None:
        m, se = tutor.paired([0.0, 0.0, 0.0, 0.0], [2.0, 2.0, 3.0, 1.0])
        self.assertAlmostEqual(m, 2.0)
        self.assertGreater(se, 0)

    def test_discard_why_names_the_split_marriage(self) -> None:
        # zöld király (22) és felső (21) együtt; a tutor a szétvágott párt jelzi
        cards = [22, 21, 0, 1, 2, 3, 8, 9, 10, 11, 16, 17]
        out = tutor._discard_why(cards, [22, 0], [0, 1], trump=1)
        self.assertTrue(any("király–felső" in s for s in out), out)

    def test_analyze_returns_serializable_review(self) -> None:
        g = _deal(5)
        rv = tutor.analyze(tutor.DealRecord.from_game(g), you=None, n_bid=4, n_play=4, workers=1, max_findings=3)
        self.assertGreater(rv.checked, 0)
        self.assertLessEqual(len(rv.findings), 3)
        json.dumps(rv.to_json())
        self.assertIn("Pénz", rv.text())
        for f in rv.findings:
            self.assertGreaterEqual(f.loss, tutor.MIN_DIFF)

    def test_only_selected_player_is_reviewed(self) -> None:
        g = _deal(5)
        rv = tutor.analyze(tutor.DealRecord.from_game(g), you=1, n_bid=4, n_play=4, workers=1)
        self.assertTrue(all(f.player == 1 for f in rv.findings))

    def test_defender_worlds_are_conditioned_on_the_bid(self) -> None:
        """Védői nézetben a felvevő keze a bemondáshoz illik (margó <= COND_MARGIN), nem egyenletes újraosztás."""
        pol = make_learned(str(POLICY / "learned_policy.json"))
        g = _deal(100)
        rec = tutor.DealRecord.from_game(g)
        for kind, st, ev in tutor.replay(rec):
            if kind == "kontra" and ev[0] != st.decl:
                ws, seeds = tutor._worlds_for(st, ev[0], 6, random.Random(1), pol)
                self.assertEqual(len(ws), len(seeds))
                for w in ws:
                    self.assertEqual(len(w.hands[st.decl]), 10)
                    self.assertEqual(w.hands[ev[0]], st.hands[ev[0]])         # a döntő saját keze nem változik
                    self.assertLessEqual(tutor._bid_margin_after(pol, st, w), tutor.COND_MARGIN + 1e-9)
                return
        self.fail("nincs védői kontra-döntés")

    def test_polish_keeps_facts_or_falls_back(self) -> None:
        f = tutor.Finding("bid", 0, "Licit", "Parti (tök adu)", "Ulti (zöld adu)", 4.2,
                          ["A teljesülési esély 40%-ról 70%-ra nő."], [])
        facts = tutor._facts_of(f)
        good = "Jobb lett volna az Ulti (zöld adu): az esély 40%-ról 70%-ra nő, a veszteség 4.2 pont."
        self.assertTrue(tutor.faithful(facts, good))
        self.assertEqual(tutor.polish(f, ask=lambda p: good), good)
        invented = good.replace("70%", "90%")
        self.assertFalse(tutor.faithful(facts, invented))
        self.assertEqual(tutor.polish(f, ask=lambda p: invented), f.text())          # kitalált szám: a sablon marad
        self.assertEqual(tutor.polish(f, ask=lambda p: "x piros ász 7"), f.text())   # kitalált lap + szám
        def boom(p):
            raise RuntimeError("nincs token")
        self.assertEqual(tutor.polish(f, ask=boom), f.text())                         # LLM nélkül is működik


if __name__ == "__main__":
    unittest.main()

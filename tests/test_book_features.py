"""Gyors tesztek a book_features / book_policy modulokhoz (<15 s, kulcs és hálózat nélkül)."""
from ulti.paths import KNOWLEDGE, RESEARCH
import json
import random
import tempfile
import unittest
from pathlib import Path

import numpy as np

import book_features as bf
import book_policy as bp
from features import N_FEATURES
from policy import DEFAULT_PATH, LearnedPolicy
from rules import Game

ROOT = Path(__file__).parent


class FeatureTest(unittest.TestCase):
    def test_vector_shape_and_names(self):
        self.assertEqual(bf.NB, len(bf.BOOK))
        self.assertEqual(len(set(bf.BOOK_NAMES)), bf.NB)
        for g in bf.GROUPS:
            self.assertTrue(any(b.group == g for b in bf.BOOK))
        self.assertEqual(len(bf.book_vector([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 0)), bf.NB)

    def test_quotes_exist_in_sources(self):
        """Minden jellemző idézete szó szerint megtalálható a megadott forrásfájlban."""
        for b in bf.BOOK:
            txt = (KNOWLEDGE / b.source).read_text(encoding="utf-8")
            self.assertIn(b.quote, txt, b.name)

    def test_known_values(self):
        i = bf.BOOK_NAMES.index
        # adu = piros(0): ász, X, VII, király + 6 más; talonba: piros ász (7) és tök VII (8)
        hand = [0, 3, 6, 7, 16, 17, 18, 22, 24, 25]
        f = bf.book_vector(hand, 0, (1, 9))
        self.assertEqual(f[i("ulti4")], 1.0)       # 4 adu (0,3,6,7) és VII
        self.assertEqual(f[i("ulti5")], 0.0)
        self.assertEqual(f[i("c40_core")], 1.0)    # ász + X az aduban, 4 adu
        self.assertEqual(f[i("disc_red")], 0.0)    # adu a piros: nem "piros a talonban"
        f2 = bf.book_vector(hand, 1, (1, 9))       # most a tök az adu, a piros nem
        self.assertEqual(f2[i("disc_red")], 1.0)   # a tök (9) nem piros; az 1 (piros VIII) igen
        self.assertEqual(f2[i("disc_low")], 2.0)   # 1: VIII, 9: VIII
        self.assertEqual(f2[i("ulti4")], 0.0)
        # betlinél a VII,VIII,IX sorozat
        f3 = bf.book_vector([0, 1, 2, 8, 9, 16, 17, 24, 25, 26], -1)
        self.assertEqual(f3[i("betli_low_run")], 3 + 2 + 2 + 3)
        # nincs lerakás -> a lerakásos jellemzők 0 (védői nézet)
        for n in ("disc_red", "disc_ax7", "disc_low", "disc_thin", "disc_high"):
            self.assertEqual(bf.book_vector(hand, 0)[i(n)], 0.0)

    def test_mask(self):
        m = bf.group_mask(["talon"])
        self.assertEqual(sum(m), sum(1 for b in bf.BOOK if b.group == "talon"))
        self.assertEqual(sum(bf.group_mask([])), 0)
        self.assertEqual(sum(bf.group_mask(bf.GROUPS)), bf.NB)


class PolicyTest(unittest.TestCase):
    def test_zero_book_weights_equal_base_policy(self):
        """Nulla könyvi súllyal a BookPolicy pontosan a LearnedPolicy-t adja (a könyvi réteg additív)."""
        base = LearnedPolicy.load(DEFAULT_PATH)
        bpol = bp.BookPolicy(base.w, base.params, base.talon, None, bf.GROUPS)
        rng = random.Random(1)
        for sd in range(12):
            g = Game.new_deal(sd % 3, random.Random(sd))
            hand = g.hands[g.fh]
            a = base.best_option(hand, 0, True)
            b = bpol.best_option(hand, 0, True)
            self.assertEqual(a[1:3], b[1:3])
            self.assertAlmostEqual(a[0], b[0], places=9)
            self.assertEqual(sorted(a[3]), sorted(b[3]))
            self.assertAlmostEqual(base.p_success(hand[:10], 3, 0, "ulti", False), bpol.p_success(hand[:10], 3, 0, "ulti", False), places=9)

    def test_save_load_roundtrip_and_legal_play(self):
        rng = np.random.default_rng(0)
        w = bp._bw_zero()
        for k in w:
            w[k] = rng.normal(size=bf.NB) * 0.3
        base = LearnedPolicy.load(DEFAULT_PATH)
        pol = bp.BookPolicy(base.w, base.params, base.talon, w, ["talon", "durch"])
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p.json"
            pol.save(f)
            back = bp.BookPolicy.load(f)
        self.assertEqual(back.groups, ["talon", "durch"])
        self.assertTrue(np.allclose(back.wb["parti"], pol.wb["parti"]))
        import arena
        for sd in range(6):                               # szabálytalan licit/lap esetén az arena kivételt dob
            g = arena.play_deal(sd % 3, 4242 + sd, [back, back, back])
            self.assertIsNotNone(g.result)
            self.assertEqual(sum(g.result["change"]), 0)

    def test_mask_off_ignores_book_weights(self):
        base = LearnedPolicy.load(DEFAULT_PATH)
        w = bp._bw_zero()
        for k in w:
            w[k][:] = 5.0
        hand = Game.new_deal(0, random.Random(3)).hands[0][:10]
        plain = bp.BookPolicy(base.w, base.params, base.talon, w, [])        # nincs aktív csoport
        self.assertAlmostEqual(plain.p_success(hand, 3, 0, "ulti", False), base.p_success(hand, 3, 0, "ulti", False), places=9)

    def test_fit_recovers_signal(self):
        """Szintetikus adaton: ha a kimenet egy könyvi jellemzőtől függ, a súlya pozitív lesz; maszkolva 0."""
        rng = np.random.default_rng(1)
        n = 4000
        X = np.zeros((n, N_FEATURES + bf.NB + 8))
        X[:, 0] = 1.0
        j = bf.BOOK_NAMES.index("ulti4")
        X[:, N_FEATURES + j] = rng.integers(0, 2, n)
        y = (rng.random(n) < np.where(X[:, N_FEATURES + j] > 0, 0.8, 0.2)).astype(float)
        base = LearnedPolicy.load(DEFAULT_PATH)
        on = bp.fit_book_policy({"ulti": (X, y)}, ["ulti40_20"], base)
        off = bp.fit_book_policy({"ulti": (X, y)}, [], base)
        self.assertGreater(on.wb["ulti"][j], 1.0)
        self.assertEqual(off.wb["ulti"][j], 0.0)


class ArtifactTest(unittest.TestCase):
    def test_results_file_and_policy_untouched(self):
        r = json.loads((RESEARCH / "book_features_results.json").read_text(encoding="utf-8"))
        for k in ("summary", "ab_vs_base", "all_pooled_3_sets", "heldout_logloss", "sign_check", "conclusion"):
            self.assertIn(k, r)
        self.assertEqual(set(r["ab_vs_base"]), {"all", *bf.GROUPS})
        p = json.loads((RESEARCH / "learned_bookfeat.json").read_text(encoding="utf-8"))
        self.assertIn("book_weights", p)
        self.assertEqual(p["book_names"], bf.BOOK_NAMES)
        self.assertNotIn("book_weights", json.loads(DEFAULT_PATH.read_text()))   # a learned_policy.json változatlan formátumú


if __name__ == "__main__":
    unittest.main()

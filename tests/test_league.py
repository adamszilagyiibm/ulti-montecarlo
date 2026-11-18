"""test_league.py - A liga-tanítás (league.py) gyors tesztjei."""
import json
import unittest
from pathlib import Path

import numpy as np

import league
from policy import DEFAULT_PATH, LearnedPolicy


class LeagueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.champ = LearnedPolicy.load(DEFAULT_PATH)
        spec = league.pool_spec(cls.champ, [("gen0", cls.champ)], 1.0, 1.0)
        cls.data = league.gather(spec, 123_456_000, 24, 0.3, workers=2)

    def test_gather_shapes(self) -> None:
        self.assertTrue(self.data)
        for k, (X, y) in self.data.items():
            self.assertEqual(len(X), len(y), k)
            self.assertIn(k, self.champ.w)

    def test_gather_has_declarer_and_defender_rows(self) -> None:
        self.assertTrue(any(k.startswith("d_") for k in self.data))
        self.assertTrue(any(not k.startswith("d_") and k != "silent" for k in self.data))

    def test_refit_blend_zero_keeps_champion(self) -> None:
        big = {k: (np.vstack([X] * 4), np.concatenate([y] * 4)) for k, (X, y) in self.data.items()}
        cand = league.refit(self.champ, big, blend=0.0)
        for k, w in self.champ.w.items():
            self.assertTrue(np.allclose(cand.w[k], w), k)
        self.assertEqual(cand.params, self.champ.params)

    def test_refit_keeps_params_and_talon(self) -> None:
        big = {k: (np.vstack([X] * 4), np.concatenate([y] * 4)) for k, (X, y) in self.data.items()}
        cand = league.refit(self.champ, big, lam=30.0, blend=0.5)
        self.assertEqual(cand.params, self.champ.params)
        self.assertIs(cand.talon, self.champ.talon)

    def test_gate_logic(self) -> None:
        self.assertTrue(league.significantly_worse({"mean": -2.0, "ci95": 1.0}))
        self.assertFalse(league.significantly_worse({"mean": -0.5, "ci95": 1.0}))

    def test_saved_candidate_loads(self) -> None:
        f = Path(league.OUT_POLICY)
        if f.exists():
            self.assertIsInstance(LearnedPolicy.load(f), LearnedPolicy)

    def test_does_not_overwrite_learned_policy(self) -> None:
        self.assertNotEqual(Path(league.OUT_POLICY).resolve(), Path(DEFAULT_PATH).resolve())

    def test_data_roundtrip(self) -> None:
        league.GEN_DIR.mkdir(parents=True, exist_ok=True)
        keep = league.GEN_DIR / "data.npz"
        backup = keep.read_bytes() if keep.exists() else None
        try:
            league.save_data(self.data)
            back = league.load_data()
            self.assertEqual(set(back), set(self.data))
            for k in self.data:
                self.assertTrue(np.allclose(back[k][0], self.data[k][0]))
        finally:
            if backup is not None:
                keep.write_bytes(backup)
            else:
                keep.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

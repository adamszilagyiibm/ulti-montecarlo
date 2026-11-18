"""A Kovács-könyv díjtáblázata és a rules.set_ruleset('book') ellenőrzése, valamint a visszajátszott példák."""
from ulti.paths import KNOWLEDGE
import json
import unittest
from pathlib import Path

import rules
from check_book_prices import ROWS, check

ROOT = Path(__file__).parent
KEY = {"parti": "parti", "c40": "c40", "c20": "c20", "ulti": "ulti", "asz4": "asz4", "durch": "durch", "tdurch": "durch"}


class BookPrices(unittest.TestCase):
    def tearDown(self) -> None:
        rules.set_ruleset("friend")

    def test_table_is_internally_consistent(self) -> None:
        vals, bad = check()
        self.assertEqual(bad, [])
        self.assertEqual((vals["c20"], vals["durch"], vals["tdurch"], vals["asz4"]), (6, 7, 14, 4))

    def test_every_book_figure_exists_with_book_value(self) -> None:
        rules.set_ruleset("book")
        for unit, figs in ROWS:
            for red, comps in figs:
                cs = {KEY[c] for c in comps}
                opn = "tdurch" in comps
                hit = [c for c in rules.CONTRACTS if c["kind"] == "trump" and c["red"] == red and c["open"] == opn
                       and {k for k in c["comps"] if k != "parti"} == (cs - {"parti"})
                       and sum(v for k, v in c["comps"].items() if k != "parti" or "parti" in cs) == unit]
                self.assertTrue(hit, f"hiányzik a könyv {unit} egységes figurája: {red} {comps}")

    def test_friend_ruleset_is_restored(self) -> None:
        n = len(rules.CONTRACTS)
        rules.set_ruleset("book")
        self.assertGreater(len(rules.CONTRACTS), n)
        rules.set_ruleset("friend")
        self.assertEqual(len(rules.CONTRACTS), n)

    def test_replayed_book_examples_match_book_score(self) -> None:
        path = KNOWLEDGE / "replay.json"
        if not path.exists():
            self.skipTest("replay.json nincs (a könyv szövegét tartalmazza, nem része a repónak)")
        data = json.loads(path.read_text(encoding="utf-8"))
        full = [v for v in data.values() if v.get("match") is not None]
        self.assertTrue(full)
        self.assertEqual([v["example"] for v in full if not v["match"]], [], "a motor eltér a könyv pontszámától")


if __name__ == "__main__":
    unittest.main()

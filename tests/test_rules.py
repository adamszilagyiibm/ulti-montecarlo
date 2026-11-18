"""
test_rules.py - A rules.py szabálymotor keresztellenőrzése a JS motorral.

A xcheck.js véletlen (de szabályos) játszmákat játszik le az igazi JS motorral, és kimenti a lépéseket
és a pontszámot. Itt ugyanazokat a lépéseket a Python motorral játsszuk le: az eredménynek (pont,
sorok) bit-azonosnak kell lennie. Eltérés = a Python vagy a JS szabályok hibája.
"""
from ulti.paths import TOOLS
import json
import random
import shutil
import subprocess
import unittest
from pathlib import Path

import rules
from rules import CONTRACTS, Game

HERE = Path(__file__).parent
NODE = shutil.which("node")


def replay(rec: dict) -> Game:
    g = Game.from_hands(rec["dealer"], rec["hands"])
    for a in rec["acts"]:
        t = a["t"]
        if t == "bid":
            err = g.validate_bid(a["p"], a["disc"], a["ci"], a["su"])
            assert err is None, f"Python szerint érvénytelen licit: {err} {a}"
            g.do_bid(a["p"], a["disc"], a["ci"], a["su"])
        elif t == "pickup":
            assert g.can_pick_up(a["p"]), f"Python szerint nem vehető fel: {a}"
            g.do_pickup(a["p"])
        elif t == "pass":
            g.do_pass(a["p"])
        elif t == "concede":
            g.do_concede(a["p"])
        elif t == "kontra":
            assert g.turn == a["p"], f"kontrasor eltér: {g.turn} != {a['p']}"
            g.do_kontra(a["p"], a["ks"])
        elif t == "card":
            assert g.turn == a["p"], f"lépéssor eltér: {g.turn} != {a['p']}"
            assert a["id"] in g.legal_cards(a["p"]), f"Python szerint szabálytalan lap: {a}"
            g.play_card(a["p"], a["id"])
        elif t == "collect":
            g.collect_trick()
    return g


@unittest.skipUnless(NODE, "node nem elérhető")
class CrossCheckWithJs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(TOOLS / "xcheck.js"), "400", "777"], capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        cls.recs = json.loads(out.stdout)

    def test_every_deal_matches_js(self):
        bad = []
        for i, rec in enumerate(self.recs):
            g = replay(rec)
            if g.result is None or list(g.result["change"]) != list(rec["change"]):
                bad.append((i, rec["change"], g.result and g.result["change"]))
        self.assertEqual(bad, [], f"{len(bad)} eltérés a JS-hez képest, első: {bad[:3]}")

    def test_result_rows_match_js(self):
        for i, rec in enumerate(self.recs):
            g = replay(rec)
            if rec["conceded"]:
                continue
            py = sorted((r["k"] or "", bool(r["win"]), r["delta"]) for r in g.result["rows"])
            js = sorted((r["k"] or "", bool(r["win"]), r["delta"]) for r in rec["rows"])
            self.assertEqual(py, js, f"#{i}")

    def test_sample_covers_all_paths(self):
        kinds = {(r["ci"], bool(r["conceded"])) for r in self.recs}
        self.assertGreater(len({k[0] for k in kinds}), 15, "kevés különböző vállalás a mintában")
        self.assertTrue(any(r["conceded"] for r in self.recs))
        self.assertTrue(any(a["t"] == "kontra" and a["ks"] for r in self.recs for a in r["acts"]))
        self.assertTrue(any(a["t"] == "pickup" for r in self.recs for a in r["acts"]))


class RulesBasics(unittest.TestCase):
    def test_zero_sum_on_random_play(self):
        rng = random.Random(5)
        for n in range(200):
            g = Game.new_deal(n % 3, rng)
            # első licitáló: parti, két véletlen lerakott lappal
            p = g.turn
            disc = g.hands[p][:2]
            g.do_bid(p, disc, 0, 1)
            g.do_pass(g.turn)
            g.do_pass(g.turn)
            g.do_pass(g.turn) if g.phase == "bid" else None
            if g.phase == "kontra":
                while g.phase == "kontra":
                    g.do_kontra(g.turn, [])
            while g.phase == "play":
                if len(g.trick) == 3:
                    g.collect_trick()
                    continue
                q = g.turn
                g.play_card(q, rng.choice(g.legal_cards(q)))
            self.assertEqual(sum(g.result["change"]), 0)

    def test_contract_table_complete(self):
        self.assertEqual(len(CONTRACTS), 42)
        vals = [c["val"] for c in CONTRACTS]
        self.assertEqual(vals, sorted(vals))

    def test_ulti_requires_trump_seven(self):
        ci = next(i for i, c in enumerate(CONTRACTS) if c["name"] == "Ulti")
        hand = [rules.suit_rank for suit_rank in ()] if False else [8 + 1, 8 + 2, 8 + 3]
        self.assertIsNotNone(rules.check_req(hand, ci, 1))      # nincs tök VII
        self.assertIsNone(rules.check_req([8] + hand, ci, 1))   # tök VII = id 8

    def test_forced_parts(self):
        ci = next(i for i, c in enumerate(CONTRACTS) if c["name"] == "40-100")
        self.assertEqual(rules.forced_parts([8, 9], ci, 1), ["c40"])
        self.assertEqual(rules.forced_parts([8 + 6, 8 + 5], ci, 1), [])


@unittest.skipUnless(NODE, "node nem elérhető")
class BotMatchesJs(unittest.TestCase):
    """A playbot.bot_choose minden lépésben ugyanazt a lapot adja, mint a JS botChoose."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(TOOLS / "xcheck.js"), "400", "4242", "bot"],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        cls.recs = json.loads(out.stdout)

    def test_bot_choices_match_js(self):
        from playbot import bot_choose
        total = diff = 0
        examples = []
        for rec in self.recs:
            g = Game.from_hands(rec["dealer"], rec["hands"])
            for a in rec["acts"]:
                t = a["t"]
                if t == "bid":
                    g.do_bid(a["p"], a["disc"], a["ci"], a["su"])
                elif t == "pickup":
                    g.do_pickup(a["p"])
                elif t == "pass":
                    g.do_pass(a["p"])
                elif t == "concede":
                    g.do_concede(a["p"])
                elif t == "kontra":
                    g.do_kontra(a["p"], a["ks"])
                elif t == "collect":
                    g.collect_trick()
                elif t == "card":
                    mine = bot_choose(g, a["p"])
                    total += 1
                    if mine != a["id"]:
                        diff += 1
                        if len(examples) < 5:
                            examples.append((rules.CONTRACTS[g.ci]["name"], g.trick_no, a["p"], a["id"], mine))
                    g.play_card(a["p"], a["id"])
        self.assertGreater(total, 5000)
        self.assertEqual(diff, 0, f"{diff}/{total} lépés eltér a JS-től, példák: {examples}")


@unittest.skipUnless(NODE, "node nem elérhető")
class AuctionMatchesJs(unittest.TestCase):
    """A jsbot licit-értékelése és kontra-döntése megegyezik a JS-sel (zaj nélküli mennyiségeken)."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, str(TOOLS / "xcheck.js"), "300", "99", "auction"],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        cls.recs = json.loads(out.stdout)

    def test_auction_values_and_kontra_match(self):
        import jsbot
        n_bid = n_own = n_kon = 0
        bad = []
        for rec in self.recs:
            g = Game.from_hands(rec["dealer"], rec["hands"])
            by_at = {}
            for ch in rec["checks"]:
                by_at.setdefault(ch["at"], []).append(ch)
            for i, a in enumerate(rec["acts"] + [{"t": "end"}]):
                for ch in by_at.get(i, []):
                    if ch["t"] == "bid":
                        o = jsbot.bid_options(g.hands[ch["p"]], g.min_bid_index())[0]
                        n_bid += 1
                        if o["ci"] != ch["ci"] or abs(o["ev"] - ch["ev"]) > 1e-9:
                            bad.append(("bid", o["ci"], ch["ci"], o["ev"], ch["ev"]))
                    elif ch["t"] == "own":
                        h = g.hands[ch["p"]] + g.talon
                        opts = jsbot.bid_options(h, g.min_bid_index())
                        cur = jsbot.estimate(g.hands[ch["p"]], g.bid[0], g.bid[1], None)
                        n_own += 1
                        ev = opts[0]["ev"] if opts else None
                        if (ev is None) != (ch["ev"] is None) or (ev is not None and abs(ev - ch["ev"]) > 1e-9) \
                                or abs(cur - ch["cur"]) > 1e-9:
                            bad.append(("own", ev, ch["ev"], cur, ch["cur"]))
                    elif ch["t"] == "kontra":
                        ks = jsbot.bot_kontra(g, ch["p"])
                        n_kon += 1
                        if ks != ch["ks"]:
                            bad.append(("kontra", ks, ch["ks"], rules.CONTRACTS[g.ci]["name"]))
                t = a["t"]
                if t == "bid":
                    g.do_bid(a["p"], a["disc"], a["ci"], a["su"])
                elif t == "pickup":
                    g.do_pickup(a["p"])
                elif t == "pass":
                    g.do_pass(a["p"])
                elif t == "concede":
                    g.do_concede(a["p"])
                elif t == "kontra":
                    g.do_kontra(a["p"], a["ks"])
        self.assertGreater(n_bid, 200)
        self.assertGreater(n_own, 200)
        self.assertGreater(n_kon, 400)
        self.assertEqual(bad, [], f"{len(bad)} eltérés, első: {bad[:3]}")


if __name__ == "__main__":
    unittest.main()

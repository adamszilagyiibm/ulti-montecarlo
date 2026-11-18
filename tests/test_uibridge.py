"""A licit-, kontra- és elemző végpontok (uibridge.py, server.py) tesztjei, valódi HTTP-n."""
from ulti.paths import POLICY
import json
import random
import threading
import unittest
import urllib.error
import urllib.request

import rules
import tutor
import uibridge
from mcplay import mc_choose
from policy import make_learned
from rules import Game
import server
from server import serve

POL = POLICY / "learned_policy.json"


def _snap(g: Game, p: int) -> dict:
    return {"p": p, "dealer": g.dealer, "hand": list(g.hands[p]), "sizes": [len(h) for h in g.hands],
            "talon": list(g.talon) if (g.bid and g.bid[2] == p) else [], "talon_n": len(g.talon),
            "bid": ({"ci": g.bid[0], "suit": g.bid[1], "seat": g.bid[2]} if g.bid else None),
            "passes": g.passes, "blog": [list(e) for e in g.blog]}


def _play_snap(g: Game, p: int, reveal: bool = False) -> dict:
    """A UI snapshot() függvényének Python-mása: a döntő játékos nézete a kijátszás közben."""
    c = rules.CONTRACTS[g.ci]
    tr = lambda t: [{"p": a, "c": b} for a, b in t]
    s = {"p": p, "dealer": g.dealer, "ci": g.ci, "decl": g.decl, "kind": c["kind"], "comps": list(c["comps"]), "red": c["red"],
         "open": c["open"], "trump": g.trump, "hand": list(g.hands[p]), "sizes": [len(h) for h in g.hands],
         "talon": list(g.talon) if p == g.decl else [], "trick": tr(g.trick), "hist": [tr(t) for t in g.hist],
         "legal": g.legal_cards(p), "kmult": [dict(k) for k in g.kmult], "forced": dict(g.forced),
         "marr": [[{"s": a, "v": b} for a, b in m] for m in g.marr]}
    if reveal and c["open"] and g.trick_no >= 1 and p != g.decl:
        s["reveal"] = {g.decl: list(g.hands[g.decl])}
    return s


def _play_states(n_deals: int = 12, per_deal: int = 4):
    """(Game, játékos) párok valódi, a tanult politikával lejátszott leosztások közepéről."""
    pol = make_learned(str(POL))
    out = []
    for seed in range(n_deals):
        rng = random.Random(seed)
        g = Game.new_deal(seed % 3, rng)
        while g.phase == "bid":
            p = g.turn
            a = pol.bid_action(g, p, rng)
            {"bid": lambda: g.do_bid(p, a["disc"], a["ci"], a["suit"]), "pickup": lambda: g.do_pickup(p),
             "concede": lambda: g.do_concede(p), "pass": lambda: g.do_pass(p)}[a["type"]]()
        while g.phase == "kontra":
            g.do_kontra(g.turn, pol.kontra(g, g.turn, rng))
        k = 0
        while g.phase == "play":
            if len(g.trick) == 3:
                g.collect_trick()
                continue
            p = g.turn
            if k % 5 == 2 and len(out) < n_deals * per_deal:
                out.append((g.clone(), p))
            g.play_card(p, pol.card(g, p, rng))
            k += 1
    return out


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        rules.set_ruleset("friend")
        self.pol = make_learned(str(POL))

    def test_bid_matches_the_engine_on_full_game_state(self) -> None:
        """A UI-pillanatképből (csak a döntő látása) ugyanaz a licit adódik, mint a teljes játékállapotból."""
        for seed in range(12):
            g = Game.new_deal(seed % 3, random.Random(seed))
            while g.phase == "bid":
                p = g.turn
                a = self.pol.bid_action(g, p, random.Random(5))
                b = uibridge.bid_action(_snap(g, p), self.pol, random.Random(5))
                self.assertEqual((a["type"], a.get("ci"), a.get("suit"), sorted(a.get("disc", []))),
                                 (b["type"], b.get("ci"), b.get("suit"), sorted(b.get("disc", []))))
                if a["type"] == "bid":
                    g.do_bid(p, a["disc"], a["ci"], a["suit"])
                elif a["type"] == "pickup":
                    g.do_pickup(p)
                elif a["type"] == "concede":
                    g.do_concede(p)
                else:
                    g.do_pass(p)

    def test_bad_snapshots_raise_value_error(self) -> None:
        g = Game.new_deal(0, random.Random(1))
        s = _snap(g, g.fh)
        for bad in ({**s, "p": 5}, {**s, "hand": s["hand"][:-1]}, {**s, "hand": s["hand"] + [s["hand"][0]]},
                    {**s, "blog": [["bid", 0, 999, 1, 0, []]]}, {**s, "blog": [["tánc", 0]]}):
            with self.assertRaises((ValueError, KeyError)):
                uibridge.bid_action(bad, self.pol, random.Random(1))

    def test_record_from_ui_validates(self) -> None:
        g = Game.new_deal(0, random.Random(2))
        d = {"dealer": 0, "hands": g.init_hands, "blog": [], "klog": [], "plays": []}
        self.assertEqual(tutor.record_from_ui(d).dealer, 0)
        for bad in ({**d, "hands": [h[:-1] for h in g.init_hands]}, {**d, "dealer": 2}, {**d, "blog": [["x", 0]]}):
            with self.assertRaises(ValueError):
                tutor.record_from_ui(bad)


    def test_play_snapshot_rebuilds_the_game_exactly(self) -> None:
        states = _play_states()
        self.assertGreater(len(states), 20)
        for g, p in states:
            r = uibridge.game_from_play_snapshot(_play_snap(g, p), random.Random(1))
            self.assertEqual((r.decl, r.ci, r.trump, r.trick_no, r.turn), (g.decl, g.ci, g.trump, g.trick_no, p))
            self.assertEqual(r.hands[p], g.hands[p])
            self.assertEqual(r.won, g.won)
            self.assertEqual(r.tcount, g.tcount)
            self.assertEqual(r.kmult, g.kmult)
            self.assertEqual(r.forced, g.forced)
            self.assertEqual(r.marr, g.marr)
            self.assertEqual(r.ulti_broken, g.ulti_broken)
            self.assertEqual(r.legal_cards(p), g.legal_cards(p))
            if p == g.decl:
                self.assertEqual(r.talon, g.talon)
            for q in range(3):                  # a látatlan kezek mérete egyezik, a lapok nem szivároghatnak át
                self.assertEqual(len(r.hands[q]), len(g.hands[q]))

    def test_choose_card_is_legal_and_matches_direct_search(self) -> None:
        for g, p in _play_states(8, 3):
            s = _play_snap(g, p)
            got = uibridge.choose_card(s, random.Random(5), samples=6, budget=None)
            self.assertIn(got["id"], g.legal_cards(p))
            # ugyanaz a döntés, mintha a Game-ből közvetlenül számolnánk (ugyanaz a rng, ugyanazok a minták)
            direct = mc_choose(uibridge.game_from_play_snapshot(s, random.Random(0)), p, random.Random(5), 6, 0.0, set())
            self.assertEqual(got["id"], direct)

    def test_choose_card_never_peeks_at_hidden_hands(self) -> None:
        g, p = next((g, p) for g, p in _play_states() if len(g.legal_cards(p)) > 1 and g.trick_no < 6)
        s = _play_snap(g, p)
        a = uibridge.choose_card(s, random.Random(9), samples=8, budget=None)["id"]
        for q in range(3):            # a UI nem küldi el a másik kezeket: a snapshotban nincs más kéz, csak a méretek
            self.assertNotIn(q, [k for k in s if isinstance(k, int)])
        self.assertNotIn("hands", s)
        self.assertEqual(a, uibridge.choose_card(s, random.Random(9), samples=8, budget=None)["id"])

    def test_play_snapshot_validation(self) -> None:
        g, p = _play_states()[3]
        good = _play_snap(g, p)
        bad = [{**good, "hand": good["hand"][:-1]}, {**good, "decl": 7}, {**good, "legal": [good["hand"][0] ^ 31]},
               {**good, "trump": 9 if good["kind"] == "trump" else good["trump"], "ci": 9999},
               {**good, "hist": good["hist"] + [[{"p": 0, "c": good["hand"][0]}] * 3]}]
        for b in bad:
            with self.assertRaises((ValueError, KeyError)):
                uibridge.game_from_play_snapshot(b, random.Random(0))

    def test_time_budget_stops_the_search(self) -> None:
        import time
        g, p = next((g, p) for g, p in _play_states() if len(g.legal_cards(p)) > 2 and g.trick_no <= 2)
        t0 = time.monotonic()
        uibridge.choose_card(_play_snap(g, p), random.Random(1), samples=100000, budget=0.2)
        self.assertLess(time.monotonic() - t0, 3.0)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.srv = serve(port=0, engine_name="heuristic")
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, path: str, body: dict):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_bid_endpoint(self) -> None:
        g = Game.new_deal(0, random.Random(7))
        code, j = self.post("/api/bid", _snap(g, g.fh))
        self.assertEqual(code, 200)
        self.assertEqual(j["type"], "bid")
        self.assertIsNone(g.validate_bid(g.fh, j["disc"], j["ci"], j["suit"]))

    def test_kontra_endpoint(self) -> None:
        pol = server.kontra_policy()          # az /api/kontra a várható-pénz szabályt használja
        for seed in range(20):
            g = Game.new_deal(seed % 3, random.Random(seed))
            while g.phase == "bid":
                p = g.turn
                a = pol.bid_action(g, p, random.Random(1))
                {"bid": lambda: g.do_bid(p, a["disc"], a["ci"], a["suit"]), "pickup": lambda: g.do_pickup(p),
                 "concede": lambda: g.do_concede(p), "pass": lambda: g.do_pass(p)}[a["type"]]()
            if g.phase != "kontra":
                continue
            p = g.turn
            s = _snap(g, p)
            s.update(talon=list(g.talon) if p == g.decl else [], talon_n=2, bid={"ci": g.ci, "suit": g.trump, "seat": g.decl},
                     kmult=[dict(k) for k in g.kmult])
            code, j = self.post("/api/kontra", s)
            self.assertEqual(code, 200)
            self.assertEqual(sorted(j["ks"]), sorted(pol.kontra(g, p, random.Random(5))))
            self.assertTrue(set(j["ks"]) <= set(g.kontra_options(p)))
            return
        self.fail("nincs kontra-fázis")

    def test_review_endpoint_and_bad_input(self) -> None:
        import arena
        pol = make_learned(str(POL))
        g = arena.play_deal(2, 5, [pol, pol, pol])
        d = tutor.DealRecord.from_game(g).to_json()
        code, j = self.post("/api/review", {**d, "you": 1})
        self.assertEqual(code, 200)
        self.assertIn("summary", j)
        self.assertTrue(all(f["player"] == 1 for f in j["findings"]))
        self.assertEqual(self.post("/api/review", {**d, "you": 7})[0], 400)
        self.assertEqual(self.post("/api/review", {"dealer": 0})[0], 400)
        self.assertEqual(self.post("/api/bid", {"p": 0})[0], 400)


    def test_health_reports_capabilities(self) -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=10) as r:
            j = json.loads(r.read())
        self.assertTrue(j["ok"])
        self.assertIn("/api/choose", j["endpoints"])
        self.assertIsInstance(j["llm"], bool)

    def test_choose_endpoint_mc_engine_and_fallback(self) -> None:
        srv = serve(port=0, engine_name="mc")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port = srv.server_address[1]
            for g, p in _play_states(4, 2)[:4]:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/choose", json.dumps(_play_snap(g, p)).encode(),
                                             {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    j = json.loads(r.read())
                self.assertIn(j["id"], g.legal_cards(p))
                self.assertIn("Monte Carlo", j["why"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_review_polish_flag_falls_back_to_template(self) -> None:
        import arena
        pol = make_learned(str(POL))
        d = tutor.DealRecord.from_game(arena.play_deal(2, 5, [pol, pol, pol])).to_json()
        code, j = self.post("/api/review", {**d, "you": 1, "polish": True})   # token nélkül is 200, a sablon marad
        self.assertEqual(code, 200)
        self.assertIn("text", j)
        self.assertIn("polished", j)


if __name__ == "__main__":
    unittest.main()

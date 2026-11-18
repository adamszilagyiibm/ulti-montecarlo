"""
test_bridge.py - A JS <-> Python adapter és a HTTP szerver tesztjei (API-kulcs nélkül futnak).
"""
import json
import threading
import unittest
import urllib.error
import urllib.request

from bridge import EngineBridge, card_from_id, card_to_id, map_contract, snapshot_to_state
from engine import Card, ContractType, Rank, Suit
from server import serve


def snap(**over):
    """Egy kis, érvényes snapshot: a felvevő (P0) hív először, adu: zöld (2)."""
    base = {
        "p": 0, "decl": 0, "kind": "trump", "comps": ["parti"], "red": False, "open": False, "trump": 2,
        "hand": [0, 1, 2, 8, 9, 16, 17, 24, 25, 7], "sizes": [10, 10, 10], "talon": [], "trick": [], "hist": [],
        "legal": list(range(0)),
    }
    base.update(over)
    return base


class CardIdTest(unittest.TestCase):
    def test_roundtrip_all_32(self):
        seen = set()
        for i in range(32):
            c = card_from_id(i)
            self.assertEqual(card_to_id(c), i)
            seen.add(c)
        self.assertEqual(len(seen), 32)

    def test_known_ids(self):
        self.assertEqual(card_from_id(0), Card(Suit.PIROS, Rank.VII))
        self.assertEqual(card_from_id(31), Card(Suit.MAKK, Rank.ASZ))
        self.assertEqual(card_from_id(11), Card(Suit.TOK, Rank.X))  # 1*8+3

    def test_invalid(self):
        for bad in (-1, 32):
            with self.assertRaises(ValueError):
                card_from_id(bad)


class ContractMapTest(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(map_contract("trump", ["parti", "ulti"], False, False), ContractType.ULTI)
        self.assertEqual(map_contract("trump", ["c40"], False, False), ContractType.NEGYVEN_SZAZ)
        self.assertEqual(map_contract("trump", ["c20", "durch"], False, True), ContractType.HUSZ_SZAZ)
        self.assertEqual(map_contract("betli", ["betli"], False, True), ContractType.TERITETT_BETLI)
        self.assertEqual(map_contract("durch", ["durch"], False, False), ContractType.SZIN_NELKULI_DURCHMARS)
        self.assertEqual(map_contract("trump", ["durch"], False, False), ContractType.DURCHMARS)
        self.assertEqual(map_contract("trump", ["parti"], True, False), ContractType.PIROS_PASSZ)


class SnapshotTest(unittest.TestCase):
    def test_builds_consistent_state(self):
        s = snapshot_to_state(snap())
        self.assertEqual(s.acting, 0)
        self.assertEqual(sum(len(h) for h in s.player_hands.values()), 30)
        all_cards = [c for h in s.player_hands.values() for c in h]
        self.assertEqual(len(set(all_cards)), 30)  # nincs duplikált lap
        self.assertEqual(s.trump_suit, Suit.ZOLD)

    def test_respects_js_legal_list(self):
        s = snapshot_to_state(snap(legal=[0, 1]))
        self.assertEqual({card_to_id(c) for c in s.get_legal_moves(0)}, {0, 1})

    def test_rejects_wrong_turn_and_bad_data(self):
        with self.assertRaises(ValueError):
            snapshot_to_state(snap(p=2))                      # P0 volna soron
        with self.assertRaises(ValueError):
            snapshot_to_state(snap(sizes=[10, 9, 10]))        # lapszám nem egyezik
        with self.assertRaises(ValueError):
            snapshot_to_state(snap(hand=[0, 0, 1, 2, 8, 9, 16, 17, 24, 25]))  # duplikált lap
        with self.assertRaises(KeyError):
            snapshot_to_state({"p": 0})

    def test_betli_has_no_trump(self):
        s = snapshot_to_state(snap(kind="betli", comps=["betli"], trump=-1))
        self.assertIsNone(s.trump_suit)
        self.assertFalse(s.is_trump_game)


class BridgeTest(unittest.TestCase):
    def test_engine_choice_is_always_legal(self):
        b = EngineBridge()
        r = b.choose(snap(legal=[0, 1, 2]))
        self.assertIn(r["id"], (0, 1, 2))

    def test_bad_engine_cannot_return_illegal_card(self):
        class Bad:
            def choose_move(self, state, pid):
                return Card(Suit.MAKK, Rank.ASZ)  # nincs a kezében

        r = EngineBridge(Bad()).choose(snap(legal=[0, 1]))
        self.assertIn(r["id"], (0, 1))


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = serve(port=0, engine_name="heuristic")
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, body, path="/api/choose"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_health(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=10) as r:
            self.assertTrue(json.loads(r.read())["ok"])

    def test_choose_ok(self):
        code, j = self.post(json.dumps(snap(legal=[0, 1])).encode())
        self.assertEqual(code, 200)
        self.assertIn(j["id"], (0, 1))

    def test_bad_requests_return_4xx_not_crash(self):
        self.assertEqual(self.post(b"nem json")[0], 400)
        self.assertEqual(self.post(b"[1,2]")[0], 400)
        self.assertEqual(self.post(json.dumps({"p": 0}).encode())[0], 400)
        self.assertEqual(self.post(b"", )[0], 413)
        self.assertEqual(self.post(b"x" * 70000)[0], 413)
        self.assertEqual(self.post(b"{}", path="/api/nincs")[0], 404)
        # a szerver ezután is él
        self.assertEqual(self.post(json.dumps(snap(legal=[0])).encode())[0], 200)


if __name__ == "__main__":
    unittest.main()

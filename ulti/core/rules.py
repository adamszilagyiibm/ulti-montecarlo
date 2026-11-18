"""
rules.py - A teljes rablóulti-játszma szabálymotorja (licit, talon, kontra, kijátszás, pontozás).

Ez a tanulás "valósága": a pontszám (pénz) itt keletkezik. A szabályok a könyvből és a UI
(ulti.html) motorjából származnak, a JS-sel lépésről lépésre össze van vetve (test_rules.py, xcheck.js).

Lapok: egész szám, id = szín*8 + rang. Szín: 0 piros, 1 tök, 2 zöld, 3 makk.
Rang: 0 VII, 1 VIII, 2 IX, 3 X, 4 Alsó, 5 Felső, 6 Király, 7 Ász.
"""
from __future__ import annotations

from ulti.paths import RULES
import json
import os
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_FRIEND: List[dict] = json.loads((RULES / "contracts.json").read_text("utf-8"))
RULESET = "friend"                                       # az éppen érvényes szabályrendszer neve (set_ruleset állítja)
CONTRACTS: List[dict] = [dict(c) for c in _FRIEND]      # a mindenki által importált, helyben cserélhető lista
COMP_NAMES = {"parti": "Parti", "c40": "40-100", "c20": "20-100", "ulti": "Ulti", "betli": "Betli", "durch": "Durchmars",
              "asz4": "4 ász"}
# a barát UI-jának egységei (contracts.json) -> a Kovács-könyv egységei (85-88. oldal, book_prices.json); a piros és a
# terített szorzót a barát táblázata már a komponens értékébe építi, ezért az arány komponensenként állandó
_FRIEND_UNIT = {"parti": 1, "c40": 4, "c20": 8, "ulti": 4, "durch": 6, "betli": 5}
_BOOK_UNIT = {"parti": 1, "c40": 4, "c20": 6, "ulti": 4, "durch": 7, "betli": 5, "asz4": 4}


def book_contracts() -> List[dict]:
    """A könyv díjtáblázata szerinti vállalás-lista: a 20-100 6, a durchmars 7 egység, és a 4 ász bemondás is szerepel.

    A barát 42 vállalásából indul (név, fajta, piros/terített), az értékeket a könyv egységeire váltja, majd minden
    parti nélküli színjátékhoz hozzáadja a 4 ász változatot (a könyv 55 figurájából ennyi áll elő; ezt a
    test_rules ellenőrzi a check_book_prices.ROWS ellen)."""
    out: List[dict] = []
    for c in _FRIEND:
        nc = dict(c)
        nc["comps"] = {k: (v if k == "betli" else v * _BOOK_UNIT[k] // _FRIEND_UNIT[k]) for k, v in c["comps"].items()}
        nc["val"] = sum(nc["comps"].values())
        out.append(nc)
    extra: List[dict] = []
    for c in out + [{"name": "", "kind": "trump", "red": False, "open": False, "comps": {}},
                    {"name": "Piros", "kind": "trump", "red": True, "open": False, "comps": {}}]:
        if c["kind"] != "trump" or ("parti" in c["comps"] and "ulti" not in c["comps"]):
            continue
        v = _BOOK_UNIT["asz4"] * (2 if c["red"] else 1)
        base = c["name"].replace("Piros ", "", 1) if c["red"] else c["name"]
        name = ("Piros " if c["red"] else "") + "4 ász" + (" " + base if base and base != "Piros" else "")
        # az "ulti" önmagában parti + ulti; a 4 ásszal együtt a könyv 8 egysége (4+4) parti nélkül adódik, mint a 40-100 ulti
        comps = {"asz4": v, **{k: x for k, x in c["comps"].items() if k != "parti"}}
        extra.append({"name": name, "val": sum(comps.values()), "comps": comps, "kind": "trump", "red": c["red"], "open": c["open"]})
    allc = out + extra
    allc.sort(key=lambda x: x["val"])          # stabil: az azonos értékűek sorrendje megmarad
    return allc


def set_ruleset(name: str) -> None:
    """'friend' (contracts.json, a barát UI-ja) vagy 'book' (a Kovács-könyv díjai + 4 ász). Helyben cseréli a CONTRACTS-ot."""
    global RULESET
    if name not in ("friend", "book"):
        raise ValueError(name)
    CONTRACTS[:] = book_contracts() if name == "book" else [dict(c) for c in _FRIEND]
    RULESET = name


if os.environ.get("ULTI_RULES") == "book":      # a folyamat (és a spawnolt worker-ei) a könyv díjaival indul
    set_ruleset("book")
TP = [0, 1, 2, 6, 3, 4, 5, 7]          # adus játékban az ütőerő (X magasabb az alsónál)
DECK = list(range(32))

Trick = List[Tuple[int, int]]            # [(játékos, lap)]


def suit(c: int) -> int:
    return c >> 3


def rank(c: int) -> int:
    return c & 7


def pts(c: int) -> int:
    r = c & 7
    return 10 if r == 3 or r == 7 else 0


def power(c: int, tg: bool) -> int:
    return TP[c & 7] if tg else c & 7


def nxt(p: int) -> int:
    return (p + 1) % 3


def has_card(h: List[int], s: int, r: int) -> bool:
    return (s * 8 + r) in h


def is_trump_contract(ci: int) -> bool:
    return CONTRACTS[ci]["kind"] == "trump"


def suits_for(ci: int) -> List[int]:
    c = CONTRACTS[ci]
    if c["kind"] != "trump":
        return [-1]
    return [0] if c["red"] else [1, 2, 3]


def has40(h: List[int], t: int) -> bool:
    return has_card(h, t, 6) and has_card(h, t, 5)


def has20_for(h: List[int], t: int) -> bool:
    return any(s != t and has_card(h, s, 6) and has_card(h, s, 5) for s in range(4))


def forced_parts(hand: List[int], ci: int, trump: int) -> List[str]:
    c = CONTRACTS[ci]
    f: List[str] = []
    if c["kind"] != "trump":
        return f
    if "c40" in c["comps"] and not has40(hand, trump):
        f.append("c40")
    if "c20" in c["comps"] and not has20_for(hand, trump):
        f.append("c20")
    return f


def check_req(hand: List[int], ci: int, trump: int) -> Optional[str]:
    c = CONTRACTS[ci]
    if c["kind"] != "trump":
        return None
    if c["red"]:
        if trump != 0:
            return "Piros játékban a piros az adu."
    elif not (1 <= trump <= 3):
        return "Válassz adut: tök, zöld vagy makk."
    if "ulti" in c["comps"] and not has_card(hand, trump, 0):
        return "Az ultihoz kell az adu VII-es."
    return None


def concede_loss(ci: int, kmult: Optional[List[Dict[str, int]]], q: int) -> int:
    s = 0
    for k, v in CONTRACTS[ci]["comps"].items():
        m = max((kmult[q].get(k, 1) if kmult else 1), 2)
        s += v * (m + 1) if k == "ulti" else v * m
    return s


class Game:
    __slots__ = ("dealer", "fh", "hands", "talon", "phase", "turn", "bid", "passes", "decl", "ci", "trump",
                 "trump_game", "trick", "won", "tcount", "trick_no", "leader", "marr", "ulti_broken", "ulti_won",
                 "last_winner", "played", "result", "talon_pts", "last_trick", "hist", "kmult", "kseq", "kidx",
                 "ulti_lock", "forced", "init_hands", "init_talon_disc", "blog", "klog")

    def __init__(self) -> None:
        self.hist: List[Trick] = []

    # ---------- létrehozás ----------
    @classmethod
    def from_hands(cls, dealer: int, hands: List[List[int]]) -> "Game":
        g = cls()
        g.dealer = dealer
        g.fh = nxt(dealer)
        g.hands = [list(h) for h in hands]
        g.init_hands = [list(h) for h in hands]
        g.talon = []
        g.phase = "bid"
        g.turn = g.fh
        g.bid = None
        g.passes = 0
        g.decl = -1
        g.ci = -1
        g.trump = -1
        g.trump_game = False
        g.trick = []
        g.won = [[], [], []]
        g.tcount = [0, 0, 0]
        g.trick_no = 0
        g.leader = -1
        g.marr = [[], [], []]
        g.ulti_broken = False
        g.ulti_won = False
        g.last_winner = -1
        g.played = []
        g.result = None
        g.talon_pts = 0
        g.last_trick = None
        g.hist = []
        g.kmult = [{}, {}, {}]
        g.kseq = []
        g.kidx = 0
        g.ulti_lock = False
        g.forced = {}
        g.blog = []
        g.klog = []
        return g

    @classmethod
    def new_deal(cls, dealer: int, rng: random.Random) -> "Game":
        d = DECK[:]
        rng.shuffle(d)
        fh = nxt(dealer)
        hands: List[List[int]] = [[], [], []]
        k = 0
        for p in (fh, nxt(fh), nxt(nxt(fh))):
            n = 12 if p == fh else 10
            hands[p] = d[k:k + n]
            k += n
        return cls.from_hands(dealer, hands)

    def clone(self) -> "Game":
        g = Game()
        for a in Game.__slots__:
            if hasattr(self, a):
                setattr(g, a, getattr(self, a))
        g.hands = [list(h) for h in self.hands]
        g.talon = list(self.talon)
        g.trick = list(self.trick)
        g.won = [list(w) for w in self.won]
        g.tcount = list(self.tcount)
        g.marr = [list(m) for m in self.marr]
        g.played = list(self.played)
        g.hist = list(self.hist)           # az ütések nem módosulnak utólag
        g.kmult = [dict(k) for k in self.kmult]
        g.forced = dict(self.forced)
        g.kseq = list(self.kseq)
        g.blog = list(getattr(self, "blog", []))
        g.klog = list(getattr(self, "klog", []))
        return g

    # ---------- licit ----------
    def holding12(self, p: int) -> bool:
        return len(self.hands[p]) == 12

    def min_bid_index(self) -> int:
        if not self.bid:
            return 0
        v = CONTRACTS[self.bid[0]]["val"]
        for i, c in enumerate(CONTRACTS):
            if c["val"] > v:
                return i
        return len(CONTRACTS)

    def can_pick_up(self, p: int) -> bool:
        return (self.phase == "bid" and self.turn == p and not self.holding12(p) and self.bid is not None
                and self.min_bid_index() < len(CONTRACTS) and len(self.talon) == 2)

    def validate_bid(self, p: int, disc: List[int], ci: int, trump: int) -> Optional[str]:
        if ci < 0:
            return "Válassz játékot."
        if ci < self.min_bid_index():
            return "Csak magasabb licitet mondhatsz."
        if len(disc) != 2 or len(set(disc)) != 2 or any(d not in self.hands[p] for d in disc):
            return "Jelölj ki pontosan 2 lapot a talonba."
        return check_req([c for c in self.hands[p] if c not in disc], ci, trump)

    def do_pass(self, p: int) -> None:
        self.blog.append(("pass", p))
        self.passes += 1
        if self.passes >= 3 or (self.passes >= 2 and self.min_bid_index() >= len(CONTRACTS)):
            self.end_bidding()
        else:
            self.turn = nxt(p)

    def do_pickup(self, p: int) -> None:
        self.blog.append(("pickup", p))
        self.hands[p].extend(self.talon)
        self.talon = []

    def do_bid(self, p: int, disc: List[int], ci: int, trump: int) -> None:
        self.blog.append(("bid", p, ci, trump, self.min_bid_index(), tuple(disc)))
        self.hands[p] = [c for c in self.hands[p] if c not in disc]
        self.talon = list(disc)
        self.bid = (ci, trump if is_trump_contract(ci) else -1, p)
        self.passes = 0
        self.turn = nxt(p)

    def do_concede(self, p: int) -> None:
        self.blog.append(("concede", p))
        if self.phase == "bid":
            ci, su, seat = self.bid
            self.decl, self.ci, self.trump = seat, ci, su
            self.trump_game = is_trump_contract(ci)
            self.kmult = [{}, {}, {}]
            self.talon_pts = sum(pts(c) for c in self.talon)
        d = self.decl
        change = [0, 0, 0]
        for q in (x for x in range(3) if x != d):
            s = concede_loss(self.ci, self.kmult, q)
            change[q] = s
            change[d] -= s
        self.result = {"change": change, "conceded": True, "rows": [], "decl_pts": 0, "def_pts": 0}
        self.phase = "result"
        self.turn = -1

    def end_bidding(self) -> None:
        ci, su, seat = self.bid
        c = CONTRACTS[ci]
        self.decl, self.ci, self.trump = seat, ci, su
        self.trump_game = c["kind"] == "trump"
        self.talon_pts = sum(pts(x) for x in self.talon)
        if "parti" in c["comps"]:
            for p in range(3):
                for s in range(4):
                    if has_card(self.hands[p], s, 6) and has_card(self.hands[p], s, 5):
                        self.marr[p].append((s, 40 if s == self.trump else 20))
        self.kmult = [{}, {}, {}]
        self.kseq = [nxt(seat), nxt(nxt(seat)), seat]
        self.kidx = 0
        self.phase = "kontra"
        self.turn = self.kseq[0]
        self.ulti_lock = "ulti" in c["comps"]
        self.forced = {k: True for k in forced_parts(self.hands[seat], ci, su)}

    # ---------- kontra ----------
    def kontra_common(self) -> bool:
        return is_trump_contract(self.ci)

    def kontra_options(self, p: int) -> List[str]:
        comps = CONTRACTS[self.ci]["comps"]
        if p != self.decl:
            # a könyv szerint a 4 ász nem kontrázható, ha a bemondó durchmarsot is mondott
            return [k for k in comps if not self.kmult[p].get(k) and not (k == "asz4" and "durch" in comps)]
        ks = []
        for q in range(3):
            if q != self.decl:
                for k, m in self.kmult[q].items():
                    if m == 2 and k not in ks:
                        ks.append(k)
        return ks

    def must_kontra(self, p: int, k: str) -> bool:
        if p == self.decl or not self.forced.get(k):
            return False
        h, t = self.hands[p], self.trump
        if k == "c40":
            return has_card(h, t, 6) or has_card(h, t, 5)
        return any(suit(x) != t and rank(x) in (5, 6) for x in h)

    def do_kontra(self, p: int, ks: List[str]) -> None:
        ks = list(ks)
        if p != self.decl:
            for k in self.kontra_options(p):
                if self.must_kontra(p, k) and k not in ks:
                    ks.append(k)
        defs = [q for q in range(3) if q != self.decl]
        valid = set(self.kontra_options(p))
        self.klog.append((p, tuple(k for k in ks if k in valid)))
        for k in ks:
            if k not in valid:
                continue
            if p != self.decl:
                for q in (defs if self.kontra_common() else [p]):
                    self.kmult[q][k] = 2
            else:
                for q in defs:
                    if self.kmult[q].get(k) == 2:
                        self.kmult[q][k] = 4
        self.kidx += 1
        while self.kidx < 3 and not self.kontra_options(self.kseq[self.kidx]):
            self.kidx += 1
        if self.kidx >= 3:
            self.start_play()
        else:
            self.turn = self.kseq[self.kidx]

    def start_play(self) -> None:
        self.phase = "play"
        self.turn = self.decl
        self.leader = self.decl

    # ---------- kijátszás ----------
    def beats(self, a: int, b: int) -> bool:
        tg, t = self.trump_game, self.trump
        if tg:
            if suit(a) == t and suit(b) != t:
                return True
            if suit(a) != t and suit(b) == t:
                return False
        if suit(a) != suit(b):
            return False
        return power(a, tg) > power(b, tg)

    def trick_winner_idx(self, tr: Trick) -> int:
        w = 0
        for i in range(1, len(tr)):
            if self.beats(tr[i][1], tr[w][1]):
                w = i
        return w

    def legal_raw(self, p: int) -> List[int]:
        h, tr, tg = self.hands[p], self.trick, self.trump_game
        if not tr:
            return list(h)
        lead = suit(tr[0][1])
        wc = tr[self.trick_winner_idx(tr)][1]
        same = [x for x in h if suit(x) == lead]
        if same:
            if suit(wc) == lead:
                b = [x for x in same if power(x, tg) > power(wc, tg)]
                if b:
                    return b
            return same
        if tg:
            ts = [x for x in h if suit(x) == self.trump]
            if ts:
                if suit(wc) == self.trump:
                    b = [x for x in ts if power(x, tg) > power(wc, tg)]
                    if b:
                        return b
                return ts
        return list(h)

    def legal_cards(self, p: int) -> List[int]:
        L = self.legal_raw(p)
        if self.ulti_lock and p == self.decl and any(suit(x) == self.trump and rank(x) != 0 for x in self.hands[p]):
            f = [x for x in L if not (suit(x) == self.trump and rank(x) == 0)]
            if f:
                return f
        return L

    def play_card(self, p: int, card: int) -> None:
        self.hands[p].remove(card)
        self.trick.append((p, card))
        self.played.append(card)
        c = CONTRACTS[self.ci]
        if ("ulti" in c["comps"] and p == self.decl and suit(card) == self.trump and rank(card) == 0
                and self.trick_no < 9 and not self.ulti_broken):
            self.ulti_broken = True
        self.turn = nxt(p) if len(self.trick) < 3 else -1

    def collect_trick(self) -> int:
        w = self.trick[self.trick_winner_idx(self.trick)][0]
        c = CONTRACTS[self.ci]
        self.won[w].extend(x[1] for x in self.trick)
        self.tcount[w] += 1
        if self.trick_no == 9:
            self.last_winner = w
            if "ulti" in c["comps"]:
                dc = next(x[1] for x in self.trick if x[0] == self.decl)
                if w == self.decl and suit(dc) == self.trump and rank(dc) == 0 and not self.ulti_broken:
                    self.ulti_won = True
        self.hist.append(list(self.trick))
        self.last_trick = list(self.trick)
        self.trick = []
        self.trick_no += 1
        self.leader = w
        self.turn = w
        end = self.trick_no == 10
        if c["kind"] == "betli" and w == self.decl:
            end = True
        if c["kind"] == "durch" and w != self.decl:
            end = True
        if end:
            self.finish_hand()
        return w

    # ---------- pontozás ----------
    def finish_hand(self) -> None:
        c = CONTRACTS[self.ci]
        d = self.decl
        defs = [p for p in range(3) if p != d]

        def cp(p: int) -> int:
            return sum(pts(x) for x in self.won[p])

        complete = self.trick_no == 10
        decl_pts = cp(d) + (10 if complete and self.last_winner == d else 0)
        def_pts = sum(cp(p) for p in defs) + self.talon_pts + (10 if complete and self.last_winner != d else 0)

        def msum(p: int) -> int:
            return sum(v for _, v in self.marr[p])

        def_trick_pts = sum(cp(p) for p in defs) + (10 if complete and self.last_winner != d else 0)
        A = decl_pts + (msum(d) if decl_pts > 0 else 0)
        B = def_pts + (sum(msum(p) for p in defs) if def_trick_pts > 0 else 0)
        base = 2 if c["red"] else 1
        rows: List[dict] = []
        for k, v in c["comps"].items():
            if k == "parti":
                win = A > B
            elif k == "c40":
                win = decl_pts + (0 if self.forced.get("c40") else 40) >= 100
            elif k == "c20":
                win = decl_pts + (0 if self.forced.get("c20") else 20) >= 100
            elif k == "ulti":
                win = self.ulti_won
            elif k == "asz4":
                win = sum(1 for x in self.won[d] if rank(x) == 7) == 4
            elif k == "betli":
                win = self.tcount[d] == 0
            else:
                win = self.tcount[d] == 10
            rows.append({"k": k, "v": v, "win": win, "delta": v if win else -(2 * v if k == "ulti" else v)})
        if complete and self.trump_game and "ulti" not in c["comps"]:
            for p, x in self.last_trick:
                if suit(x) == self.trump and rank(x) == 0:
                    ok, mine, v = self.last_winner == p, p == d, 2 * base
                    delta = (v if mine else -v) if ok else (-2 * v if mine else 2 * v)
                    rows.append({"k": None, "v": 0, "win": delta > 0, "delta": delta})
        if complete and "parti" in c["comps"]:
            if A >= 100:
                rows.append({"k": None, "v": 0, "win": True, "delta": 2 * base})
            if B >= 100:
                rows.append({"k": None, "v": 0, "win": False, "delta": -2 * base})

            def aces(p: int) -> int:
                return sum(1 for x in self.won[p] if rank(x) == 7)

            if aces(d) == 4:
                rows.append({"k": None, "v": 0, "win": True, "delta": 2 * base})
            elif sum(aces(p) for p in defs) == 4:
                rows.append({"k": None, "v": 0, "win": False, "delta": -2 * base})
        if complete and self.trump_game and "durch" not in c["comps"] and self.tcount[d] == 10:
            rows.append({"k": None, "v": 0, "win": True, "delta": 3 * base})
        change = [0, 0, 0]
        for q in defs:
            s = 0
            for r in rows:
                m = self.kmult[q].get(r["k"], 1) if r["k"] else 1
                gain = r["v"] * (m + 1) if (r["k"] == "ulti" and not r["win"]) else -r["delta"] * m
                s -= gain
            change[q] = -s
            change[d] += s
        self.result = {"change": change, "conceded": False, "rows": rows, "decl_pts": decl_pts, "def_pts": def_pts}
        self.phase = "result"

    # ---------- segédek a botoknak ----------
    def outstanding(self, p: int) -> List[int]:
        seen = set(self.hands[p]) | set(self.played)
        if p == self.decl:
            seen.update(self.talon)
        return [x for x in DECK if x not in seen]

    def history(self) -> List[Trick]:
        return self.hist + ([self.trick] if self.trick else [])

    def known_void(self, q: int, s: int) -> bool:
        for tr in self.history():
            if suit(tr[0][1]) == s and any(pl == q and suit(cd) != s for pl, cd in tr):
                return True
        return False

    def partner_of(self, p: int) -> int:
        return next(q for q in range(3) if q != p and q != self.decl)


def find_contract(comps: List[str], red: bool, open_: bool = False, kind: str = "trump") -> Optional[int]:
    """A vállalás sorszáma a CONTRACTS-ban a komponensek alapján (a 'parti' nélkül nézve: az ulti magában parti + ulti).

    comps: 'parti', 'c40', 'c20', 'ulti', 'asz4', 'durch', 'betli'. kind: 'trump' | 'betli' | 'durch' (színtelen durchmars)."""
    want = frozenset(k for k in comps if k != "parti")
    for i, c in enumerate(CONTRACTS):
        if c["kind"] != kind or c["red"] != red or c["open"] != open_:
            continue
        if frozenset(k for k in c["comps"] if k != "parti") == want:
            return i
    return None

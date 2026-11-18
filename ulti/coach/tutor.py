"""
tutor.py - Tanító bot: egy lejátszott leosztás utólagos elemzése, LLM NÉLKÜL.

A tényeket a motor szimulációval számolja (a döntés pénzben mért értéke), a magyar szöveg sablonokból áll,
az indoklást a könyv (book_knowledge.json, kodex_advice.json) idézett tanácsai adják. Az LLM (wx.py) csak
opcionálisan fogalmazza át a már kiszámolt szöveget, tényt nem találhat ki (lásd polish()).

Egy megállapítás (Finding): kategória, a hibázó játékos, a döntés, a jobb alternatíva, a becsült
pénzkülönbség (a játékos szemszögéből, pont/leosztás), az ok és a hozzá illő könyvi tanács.
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE, POLICY as POLICY_DIR
import json
import random
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import arena
from kodex_cards import RANK_NAME, SUIT_NAME
from mcplay import _deal_unseen
from policy import LearnedPolicy, make_learned
from rules import CONTRACTS, COMP_NAMES, Game, concede_loss, is_trump_contract, pts, rank, suit

HERE = POLICY_DIR   # a tesztek és a régi hivatkozások miatt marad a név
MIN_DIFF = 1.0          # ennél kisebb becsült különbség nem megállapítás (zaj)
SEATS = ["A játékos", "B játékos", "C játékos"]


# ---------------------------------------------------------------- megjelenítés
def card_name(c: int) -> str:
    return f"{SUIT_NAME[suit(c)]} {RANK_NAME[rank(c)]}"


def cards_text(cs: Sequence[int]) -> str:
    return ", ".join(card_name(c) for c in sorted(cs))


def contract_text(ci: int, trump: int) -> str:
    c = CONTRACTS[ci]
    return c["name"] + (f" ({SUIT_NAME[trump]} adu)" if trump >= 0 and c["kind"] == "trump" and not c["red"] else "")


def seat_name(p: int, you: Optional[int]) -> str:
    return "Te" if p == you else SEATS[p]


# ---------------------------------------------------------------- könyvi tanácsok
@lru_cache(maxsize=1)
def _book() -> List[dict]:
    items: List[dict] = []
    p = KNOWLEDGE / "kodex_advice.json"
    if p.exists():
        for a in json.loads(p.read_text("utf-8")).get("advice", []):
            items.append({"topic": a["topic"], "contract": a.get("contract", ""), "text": a["advice"],
                          "quote": a["quote"], "pages": a.get("pages", ""), "direction": a.get("direction", ""), "votes": 3})
    p = KNOWLEDGE / "book_knowledge.json"
    if p.exists():
        for part in json.loads(p.read_text("utf-8")).get("parts", {}).values():
            for a in part["items"]:
                items.append({"topic": a["topic"], "contract": a.get("contract", ""), "text": a["rule"],
                              "quote": a["quote"], "pages": a.get("pages", ""), "direction": a.get("direction", ""),
                              "votes": a.get("votes", 1)})
    return items


_CONTRACT_WORDS = {"ulti": ("ulti", "ultimó"), "c40": ("40-100",), "c20": ("20-100",), "betli": ("betli",),
                   "durch": ("durchmars", "terített durchmars"), "asz4": ("4 ász", "négy ász"), "parti": ("parti",)}


def book_advice(topic: str, comps: Sequence[str] = (), limit: int = 2, direction: Optional[str] = None) -> List[dict]:
    """A téma (licit, talon, kontra, rekontra, rablas, bedobas, kijatszas, vedekezes) legjobb könyvi tanácsai:
    előbb a vállalás-specifikusak, aztán az általánosak; a több futáson megtalált (szavazat) tétel előbb."""
    words = {w for k in comps for w in _CONTRACT_WORDS.get(k, ())}
    scored: List[Tuple[int, dict]] = []
    for a in _book():
        if a["topic"] != topic or (direction and a["direction"] != direction):
            continue
        cs = {x.strip() for x in a["contract"].replace("|", ",").replace(" vagy ", ",").replace(" és ", ",").split(",")}
        spec = 2 if cs & words else (1 if a["contract"] in ("", "általános") else -1)
        if spec < 0:
            continue
        scored.append((spec * 10 + a["votes"], a))
    scored.sort(key=lambda t: -t[0])
    seen, out = set(), []
    for _, a in scored:
        if a["text"] in seen:
            continue
        seen.add(a["text"])
        out.append({"text": a["text"], "quote": a["quote"], "pages": a["pages"]})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- megállapítás
@dataclass
class Finding:
    kind: str                     # bid | discard | pickup | kontra | rekontra | play | concede
    player: int
    title: str                    # egy sor
    chosen: str                   # amit tett
    better: str                   # amit a motor javasol
    loss: float                   # a becsült pénzveszteség (pont/leosztás), pozitív = rossz döntés
    why: List[str] = field(default_factory=list)       # okok (a motor számaiból)
    book: List[dict] = field(default_factory=list)     # idézett könyvi tanács
    trick: Optional[int] = None

    def text(self, you: Optional[int] = None) -> str:
        who = seat_name(self.player, you)
        lines = [f"{self.title} ({who}; becsült veszteség: {self.loss:.1f} pont)",
                 f"  Ezt tetted: {self.chosen}", f"  Jobb: {self.better}"]
        lines += [f"  - {w}" for w in self.why]
        lines += [f"  Könyv ({b['pages']}. o.): \"{b['quote'][:220]}\"" for b in self.book]
        return "\n".join(lines)


# ---------------------------------------------------------------- leosztás-napló és visszajátszás
@dataclass
class DealRecord:
    """Egy lejátszott leosztás teljes naplója: az osztás, a licit, a kontrák és a lapok sorrendje."""
    dealer: int
    hands: List[List[int]]
    blog: List[tuple]                    # ("bid", p, ci, adu, min_ci, (lerakott lapok)), ("pickup"|"pass"|"concede", p)
    klog: List[Tuple[int, tuple]]        # (játékos, kontrázott komponensek)
    plays: List[Tuple[int, int]]         # (játékos, lap) a kijátszás sorrendjében
    rules: str = "friend"

    @classmethod
    def from_game(cls, g: Game) -> "DealRecord":
        import rules
        return cls(g.dealer, [list(h) for h in g.init_hands], list(g.blog), list(g.klog),
                   [(p, c) for tr in g.hist for p, c in tr], rules.RULESET)

    def to_json(self) -> dict:
        return {"dealer": self.dealer, "hands": self.hands, "blog": [list(e) for e in self.blog],
                "klog": [[p, list(ks)] for p, ks in self.klog], "plays": [list(x) for x in self.plays], "rules": self.rules}

    @classmethod
    def from_json(cls, d: dict) -> "DealRecord":
        blog = []
        for e in d["blog"]:
            e = list(e)
            if e[0] == "bid":
                e[5] = tuple(e[5])
            blog.append(tuple(e))
        return cls(int(d["dealer"]), [list(h) for h in d["hands"]], blog, [(int(p), tuple(ks)) for p, ks in d["klog"]],
                   [(int(p), int(c)) for p, c in d["plays"]], d.get("rules", "friend"))


def replay(rec: DealRecord):
    """Végigjátssza a naplót. Minden döntés ELŐTT (kind, élő_állapot, esemény)-t ad; az állapotot a fogyasztó klónozza."""
    g = Game.from_hands(rec.dealer, rec.hands)
    for e in rec.blog:
        yield "bid", g, e
        if e[0] == "bid":
            g.do_bid(e[1], list(e[5]), e[2], e[3])
        elif e[0] == "pickup":
            g.do_pickup(e[1])
        elif e[0] == "concede":
            g.do_concede(e[1])
            return
        else:
            g.do_pass(e[1])
    for p, ks in rec.klog:
        yield "kontra", g, (p, ks)
        g.do_kontra(p, list(ks))
    for p, c in rec.plays:
        if len(g.trick) == 3:
            g.collect_trick()
        yield "play", g, (p, c)
        g.play_card(p, c)
    if g.phase == "play" and len(g.trick) == 3:
        g.collect_trick()
    yield "end", g, None


# ---------------------------------------------------------------- szimuláció
def _policy_path() -> str:
    import rules
    return str(HERE / ("learned_book.json" if rules.RULESET == "book" else "learned_policy.json"))


Outcome = Tuple[float, Tuple[Tuple[str, bool], ...]]       # (a döntő játékos pénze, komponens -> teljesült)


def _sim_job(job: Tuple[str, str, Game, int, int]) -> Outcome:
    """Egy világ végigjátszása a motor politikájával; a p játékos pénze és a komponensek kimenete. (Worker-ben is fut.)"""
    import rules
    ruleset, path, world, p, seed = job
    if rules.RULESET != ruleset:
        rules.set_ruleset(ruleset)
    from kontra_rule import default_policy
    pol = default_policy(path)
    floor = -float(concede_loss(world.bid[0], None, 1)) if world.bid else 0.0
    try:
        arena.run_deal(world, [pol, pol, pol], random.Random(seed))
    except ValueError:        # rablás után nincs érvényes licit: a motor saját padlóértéke (policy.wants_pickup) szerint számolunk
        return floor, ()
    r = world.result
    return float(r["change"][p]), tuple((x["k"], bool(x["win"])) for x in r["rows"] if x["k"])


_POOL: Any = None


def _pool(workers: int):
    global _POOL
    if _POOL is None:
        from concurrent.futures import ProcessPoolExecutor
        _POOL = ProcessPoolExecutor(max_workers=workers)
    return _POOL


def simulate(worlds: Sequence[Game], p: int, seeds: Sequence[int], workers: int = 0) -> List[Outcome]:
    """A p pénze minden világban. workers<=1: ebben a folyamatban; különben közös folyamat-készleten."""
    import rules
    jobs = [(rules.RULESET, _policy_path(), w, p, s) for w, s in zip(worlds, seeds)]
    if workers <= 1 or len(jobs) < 8:
        return [_sim_job(j) for j in jobs]
    return list(_pool(workers).map(_sim_job, jobs, chunksize=max(1, len(jobs) // (workers * 4))))


def money(v: Sequence[Outcome]) -> List[float]:
    return [x[0] for x in v]


def rates(v: Sequence[Outcome]) -> Dict[str, float]:
    """Komponensenként a teljesülés aránya a szimulációkban."""
    acc: Dict[str, List[bool]] = {}
    for _, ws in v:
        for k, w in ws:
            acc.setdefault(k, []).append(w)
    return {k: sum(x) / len(x) for k, x in acc.items()}


def paired(base: Sequence[float], alt: Sequence[float]) -> Tuple[float, float]:
    """(átlagos különbség alt - base, standard hiba) páros mintákon."""
    d = [a - b for a, b in zip(alt, base)]
    n = len(d)
    if n < 2:
        return (d[0] if d else 0.0), 9e9
    m = sum(d) / n
    var = sum((x - m) ** 2 for x in d) / (n - 1)
    return m, (var / n) ** 0.5



# ---------------------------------------------------------------- világok (a döntő játékos információja szerint)
def _deal_world(g: Game, p: int, rng: random.Random, pol: LearnedPolicy) -> Game:
    """Licitfázisban: a p nem látott lapjai újraosztva. A talon (ha p nem a letevő) a talon-modellből jön."""
    from rules import DECK
    w = g.clone()
    own_talon = g.bid is not None and g.bid[2] == p
    seen = set(g.hands[p]) | (set(g.talon) if own_talon else set())
    pool = [c for c in DECK if c not in seen]
    if not own_talon and len(g.talon) == 2:
        t = pol.talon.sample(g, p, rng)
        w.talon = list(t)
        pool = [c for c in pool if c not in t]
    rng.shuffle(pool)
    k = 0
    for q in range(3):
        if q != p:
            n = len(g.hands[q])
            w.hands[q] = pool[k:k + n]
            k += n
    return w


def _twelve(g: Game, w: Game) -> Tuple[List[int], List[int]]:
    """(a felvevő lerakás előtti 12 lapja, a már kijátszott saját lapjai) a w világban."""
    d = w.decl
    played = [c for tr in w.history() for q, c in tr if q == d]
    return w.hands[d] + played + list(w.talon), played


def _bid_margin_after(pol: LearnedPolicy, g: Game, w: Game) -> float:
    """Mennyivel jobb a motor szerint a felvevő legjobb választása a w-beli ténylegesnél (0: a bemondás ésszerű)."""
    twelve, _ = _twelve(g, w)
    trump = g.trump if g.trump_game else -1
    own = pol.ranked_discards(twelve, g.ci, trump, 1)
    e = max(i for i, x in enumerate(g.blog) if x[0] == "bid" and x[1] == w.decl)
    top = pol.ranked_options(twelve, g.blog[e][4], 1)
    return 99.0 if not own or not top else max(0.0, top[0][0] - own[0][0])


def _resplit(pol: LearnedPolicy, g: Game, w: Game) -> Optional[float]:
    """A w világban a felvevő 12 lapját a motor legjobb talonletételével osztja újra (kéz + talon), ahogy a valódi felvevő
    is rakna. Visszaadja a bemondás margóját (_bid_margin_after), vagy None, ha a világ nem összeegyeztethető
    (a letétel már kijátszott lapot érintene)."""
    d = w.decl
    twelve, played = _twelve(g, w)
    if len(twelve) != 12:
        return None
    own = pol.ranked_discards(twelve, g.ci, g.trump if g.trump_game else -1, 1)
    if not own or any(c in played for c in own[0][1]):
        return None
    margin = _bid_margin_after(pol, g, w)
    disc = own[0][1]
    w.hands[d] = [c for c in twelve if c not in disc and c not in played]
    w.talon = list(disc)
    w.talon_pts = sum(pts(c) for c in disc)
    return margin


KONTRA_MIN = 5.0        # a kontra-tanácsot csak ekkora becsült különbségnél adjuk (a rejtett kezek becslése bizonytalanabb)
COND_CAP = 120          # ennyi jelöltvilágból válogatjuk ki a felvevő bemondásával legjobban egyezőket
COND_MARGIN = 0.5       # ennyi pontnál nagyobb margóval a bemondás nem illik a felvevő kezéhez: a világ kimarad


def _worlds_for(g: Game, p: int, n: int, rng: random.Random, pol: LearnedPolicy) -> Tuple[List[Game], List[int]]:
    """n világ a p információjával. Védői nézetben a felvevő keze nem egyenletes: a bemondás szűri. A jelölt világokban
    a felvevő 12 lapját a motor legjobb letételével osztjuk újra, és csak azokat tartjuk meg, ahol a ténylegesen
    bemondott játék a motor szerint is ésszerű (margó <= COND_MARGIN); ha nincs elég ilyen, a legkisebb margójúak
    jönnek. Nyitott játékban az első ütés után a felvevő keze ismert, azt nem osztjuk újra."""
    if g.phase == "bid":
        ws = [_deal_world(g, p, rng, pol) for _ in range(n)]
        return ws, [rng.randrange(1 << 30) for _ in ws]
    fixed = {g.decl} if CONTRACTS[g.ci]["open"] and g.trick_no >= 1 else set()
    cond = p != g.decl and not fixed and g.decl >= 0
    cand: List[Tuple[float, Game]] = []
    for _ in range(COND_CAP if cond else n):
        w = g.clone()
        _deal_unseen(w, p, rng, fixed)
        if cond:
            m = _resplit(pol, g, w)
            if m is None:
                continue
            cand.append((m, w))
            if sum(1 for x, _ in cand if x <= COND_MARGIN) >= n:
                break
        else:
            cand.append((0.0, w))
    cand.sort(key=lambda t: t[0])
    ws = [w for _, w in cand[:n]] or [g.clone() for _ in range(n)]
    return ws, [rng.randrange(1 << 30) for _ in ws]


def _compare(g: Game, p: int, cands: Sequence[Tuple[Any, Any]], n: int, rng: random.Random, pol: LearnedPolicy,
             workers: int) -> List[List[Outcome]]:
    """cands: [(címke, apply(világ))]. Minden jelölt ugyanazokon a világokon és véletlen-magokon fut (páros)."""
    ws, seeds = _worlds_for(g, p, n, rng, pol)
    out: List[List[Outcome]] = []
    jobs_w, jobs_s = [], []
    for _, apply in cands:
        for w, s in zip(ws, seeds):
            x = w.clone()
            apply(x)
            jobs_w.append(x)
            jobs_s.append(s)
    flat = simulate(jobs_w, p, jobs_s, workers)
    for i in range(len(cands)):
        out.append(flat[i * n:(i + 1) * n])
    return out


def _best_alt(vals: List[List[Outcome]], chosen: int, z: float, min_diff: float) -> Optional[Tuple[int, float, float]]:
    """A legjobb nem-választott jelölt, ha érdemben és szignifikánsan jobb a választottnál: (index, különbség, se)."""
    best: Optional[Tuple[int, float, float]] = None
    for i, v in enumerate(vals):
        if i == chosen:
            continue
        m, se = paired(money(vals[chosen]), money(v))
        if m >= min_diff and m > z * se and (best is None or m > best[1]):
            best = (i, m, se)
    return best


# ---------------------------------------------------------------- okok (a motor számaiból, nem kitalálva)
def _discard_why(cards: Sequence[int], chosen: Sequence[int], best: Sequence[int], trump: int) -> List[str]:
    out: List[str] = []
    only_c = [c for c in chosen if c not in best]
    only_b = [c for c in best if c not in chosen]
    for c in only_c:
        s, r = suit(c), rank(c)
        if trump >= 0 and s == trump and r != 0:
            out.append(f"Adut raktál le ({card_name(c)}); a talonnál az adu hosszát és erejét érdemes megtartani.")
        elif r == 7:
            out.append(f"Ászt raktál le ({card_name(c)}): ez biztos ütőlap, a talonba rakva elvész.")
        elif r == 3:
            out.append(f"Tízest raktál le ({card_name(c)}): a tízes csak ász mellett vagy aduban biztos, de pontot ér.")
        elif r in (5, 6):
            mate = c - 1 if r == 6 else c + 1
            if mate in cards and mate not in chosen:
                out.append(f"Szétvágtad a(z) {SUIT_NAME[s]} király–felső párt ({card_name(c)}): a 20 vagy 40 megszakadt.")
    if only_b:
        out.append(f"A motor szerint jobb lett volna ezt letenni: {cards_text(only_b)}.")
    return out


def _bid_why(pol: LearnedPolicy, cards: Sequence[int], chosen: Tuple[int, int, List[int]],
             best: Tuple[int, int, List[int]]) -> List[str]:
    ci, su, disc = chosen
    cj, sj, dj = best
    out: List[str] = []
    if (ci, su) != (cj, sj):
        hand = [c for c in cards if c not in disc]
        ps = []
        for k in CONTRACTS[ci]["comps"]:
            try:
                ps.append((pol.p_success(hand, ci, su, k, False, tuple(disc)), k))
            except KeyError:
                pass
        if ps:
            pmin, k = min(ps)
            out.append(f"A motor szerint a(z) {COMP_NAMES.get(k, k)} sikerének esélye a lapjaiddal kb. {pmin * 100:.0f}%.")
        vc, vb = CONTRACTS[ci]["val"], CONTRACTS[cj]["val"]
        if vc > vb:
            out.append(f"A választott játék értéke {vc} egység, a jobbé {vb}: a magasabb tét bukásnál nagyobb veszteség.")
        elif vc < vb:
            out.append(f"A választott játék értéke {vc} egység, a jobbé {vb}: a lapjaid többet bírtak.")
        if su != sj and su >= 0 and sj >= 0 and CONTRACTS[ci]["kind"] == "trump":
            n1 = sum(1 for c in cards if suit(c) == su)
            n2 = sum(1 for c in cards if suit(c) == sj)
            out.append(f"Aduból {SUIT_NAME[su]}ból {n1}, {SUIT_NAME[sj]}ból {n2} lapod volt.")
    else:
        out += _discard_why(cards, disc, dj, su)
    return out


def _kontra_why(pol: LearnedPolicy, g: Game, p: int, chosen: Sequence[str], alt: Sequence[str]) -> List[str]:
    trump = g.trump if g.trump_game else -1
    out: List[str] = []
    for k in sorted(set(chosen) ^ set(alt)):
        if p == g.decl:
            ps = pol.p_success(g.hands[p], g.ci, trump, k, False, tuple(g.talon))
            out.append(f"A motor szerint a(z) {COMP_NAMES.get(k, k)} sikerének esélye a te lapjaiddal kb. {ps * 100:.0f}%.")
        else:
            ps = pol.p_success(g.hands[p], g.ci, trump, k, True)
            out.append(f"A motor szerint a felvevő a(z) {COMP_NAMES.get(k, k)} vállalást a te lapjaiddal kb. "
                       f"{ps * 100:.0f}% eséllyel teljesíti.")
    return out


def _rate_why(chosen: Sequence[Outcome], better: Sequence[Outcome]) -> List[str]:
    """Komponensenként a teljesülés esélyének változása a szimulációkban (a motor mérése, nem találgatás)."""
    rc, rb = rates(chosen), rates(better)
    out = []
    for k in rc:
        if k in rb and abs(rb[k] - rc[k]) >= 0.10:
            name = COMP_NAMES.get(k, k)
            out.append(f"A(z) {name} teljesülési esélye a szimulációkban {rc[k] * 100:.0f}%-ról {rb[k] * 100:.0f}%-ra "
                       f"{'nő' if rb[k] > rc[k] else 'csökken'}.")
    return out


def _kontra_sets(g: Game, p: int, chosen: Tuple[str, ...]) -> List[Tuple[str, ...]]:
    opts = list(g.kontra_options(p))
    sets = [tuple(chosen), ()] + [(k,) for k in opts] + ([tuple(opts)] if len(opts) > 1 else [])
    out: List[Tuple[str, ...]] = []
    for s in sets:
        s = tuple(sorted(set(s)))
        if s not in out:
            out.append(s)
    return out[:7]


def _set_text(ks: Sequence[str]) -> str:
    return ", ".join(COMP_NAMES.get(k, k) for k in ks) if ks else "nincs kontra"


@dataclass
class Review:
    you: Optional[int]
    summary: str
    findings: List[Finding]
    checked: int                                  # hány döntést vizsgált a motor
    result: Optional[dict] = None

    def text(self) -> str:
        parts = [self.summary]
        if not self.findings:
            parts.append("A vizsgált döntéseid közül egyiknél sem találtam érdemi, szignifikáns hibát.")
        parts += [f.text(self.you) for f in self.findings]
        return "\n\n".join(parts)

    def to_json(self) -> dict:
        return {"you": self.you, "summary": self.summary, "checked": self.checked, "result": self.result,
                "findings": [asdict(f) for f in self.findings]}


def _result_summary(g: Game, you: Optional[int]) -> Tuple[str, dict]:
    r = g.result or {}
    if g.decl < 0:
        return "A leosztás nem fejeződött be.", {}
    ch = r.get("change", [0, 0, 0])
    head = f"{seat_name(g.decl, you)} vitte a(z) {contract_text(g.ci, g.trump)} játékot."
    if r.get("conceded"):
        head += " Bedobták."
    else:
        rows = [f"{COMP_NAMES.get(x['k'], x['k'])}: {'teljesült' if x['win'] else 'bukott'}" for x in r.get("rows", []) if x["k"]]
        head += " " + "; ".join(rows) + "." if rows else ""
    pay = ", ".join(f"{seat_name(p, you)}: {ch[p]:+d}" for p in range(3))
    return f"{head} Pénz: {pay}.", {"change": ch, "decl": g.decl, "contract": CONTRACTS[g.ci]["name"]}


def analyze(rec: DealRecord, you: Optional[int] = None, n_bid: int = 32, n_play: int = 60, seed: int = 1,
            workers: int = 8, max_findings: int = 6, z: float = 2.0, min_diff: float = MIN_DIFF) -> Review:
    """Elemzi a leosztás döntéseit (you: csak annak a játékosnak; None: mindhárom). A motor minden döntésnél
    ugyanazokon a determinizált világokon végigjátssza a választott és a jelölt alternatívákat, és a pénzt hasonlítja."""
    import rules
    if rules.RULESET != rec.rules:
        rules.set_ruleset(rec.rules)
    from kontra_rule import default_policy
    pol = default_policy(_policy_path())
    rng = random.Random(seed)
    findings: List[Finding] = []
    checked = 0
    final: Optional[Game] = None
    for kind, g, ev in replay(rec):
        if kind == "end":
            final = g
            break
        if kind == "bid":
            f, c = _bid_event(g, ev, you, pol, n_bid, rng, workers, z, min_diff)
        elif kind == "kontra":
            f, c = _kontra_event(g, ev, you, pol, n_play, rng, z, min_diff)
        else:
            f, c = _play_event(g, ev, you, pol, n_play, rng, z, min_diff)
        checked += c
        if f:
            findings.append(f)
    findings.sort(key=lambda f: -f.loss)
    summary, res = _result_summary(final, you) if final is not None else ("A leosztás nem fejeződött be.", {})
    return Review(you, summary, findings[:max_findings], checked, res)


# ---------------------------------------------------------------- eseménykezelők
def _topic_comps(ci: int) -> List[str]:
    return list(CONTRACTS[ci]["comps"])


def _bid_text(ci: int, su: int, disc: Sequence[int]) -> str:
    return f"{contract_text(ci, su)}, letett lapok: {cards_text(disc)}"


def _bid_event(g: Game, e: tuple, you: Optional[int], pol: LearnedPolicy, n: int, rng: random.Random,
               workers: int, z: float, min_diff: float) -> Tuple[Optional[Finding], int]:
    p = e[1]
    if you is not None and p != you:
        return None, 0
    if e[0] == "bid":
        return _bid12(g, e, pol, n, rng, workers, z, min_diff)
    return _pickup_event(g, e, pol, n, rng, workers, z, min_diff)


def _bid12(g: Game, e: tuple, pol: LearnedPolicy, n: int, rng: random.Random, workers: int, z: float,
           min_diff: float) -> Tuple[Optional[Finding], int]:
    _, p, ci, su, _, disc = e
    cards = list(g.hands[p])
    cands: List[Tuple[int, int, List[int]]] = [(ci, su, list(disc))]
    for _, c2, s2, d2 in pol.ranked_options(cards, g.min_bid_index(), 5):
        cands.append((c2, s2, d2))
    for _, d2 in pol.ranked_discards(cards, ci, su, 3):
        cands.append((ci, su, d2))
    uniq: List[Tuple[int, int, List[int]]] = []
    seen = set()
    for c2, s2, d2 in cands:
        key = (c2, s2, frozenset(d2))
        if key in seen or g.validate_bid(p, d2, c2, s2):
            continue
        seen.add(key)
        uniq.append((c2, s2, d2))
    if len(uniq) < 2:
        return None, 0
    fns = [(u, (lambda w, u=u: w.do_bid(p, u[2], u[0], u[1]))) for u in uniq]
    vals = _compare(g, p, fns, n, rng, pol, workers)
    best = _best_alt(vals, 0, z, min_diff)
    if best is None:
        return None, 1
    i, m, se = best
    c2, s2, d2 = uniq[i]
    same = (c2, s2) == (ci, su)
    why = _bid_why(pol, cards, (ci, su, list(disc)), (c2, s2, d2)) + _rate_why(vals[0], vals[i])
    why.append(f"{n} szimulált leosztás átlagában {m:+.1f} pont a különbség (± {1.96 * se:.1f}).")
    topic = "talon" if same else "licit"
    return Finding("discard" if same else "bid", p, "Talonletétel" if same else "Licit", _bid_text(ci, su, disc),
                   _bid_text(c2, s2, d2), m, why, book_advice(topic, _topic_comps(c2), 2)), 1


def _pickup_event(g: Game, e: tuple, pol: LearnedPolicy, n: int, rng: random.Random, workers: int, z: float,
                  min_diff: float) -> Tuple[Optional[Finding], int]:
    p = e[1]
    acts: List[Tuple[str, Any]] = [("pass", lambda w: w.do_pass(p))]
    if g.can_pick_up(p):
        acts.append(("pickup", lambda w: w.do_pickup(p)))
    if g.bid and g.bid[2] == p:
        acts.append(("concede", lambda w: w.do_concede(p)))
    if len(acts) < 2:
        return None, 0
    names = [a[0] for a in acts]
    ci0, su0, _ = g.bid
    vals = _compare(g, p, acts, n, rng, pol, workers)
    ch = names.index(e[0])
    best = _best_alt(vals, ch, z, min_diff)
    if best is None:
        return None, 1
    i, m, se = best
    label = {"pass": "passz", "pickup": "rablás (a talon felvétele)", "concede": "bedobás"}
    why = _rate_why(vals[ch], vals[i])
    if "pickup" in names and p != g.bid[2]:
        stay, go = pol.pickup_values(g, p, random.Random(1), 12)
        why.append(f"A motor becslése: védőként maradva {stay:+.1f}, rablással {go:+.1f} pont a várható érték.")
    if e[0] == "concede" or names[i] == "concede":
        why.append(f"A bedobás ára {concede_loss(ci0, None, 1)} pont/ellenfél; a játék folytatása ennél "
                   f"{'kevesebbe' if names[i] == 'concede' else 'többe'} kerül.")
    why.append(f"{n} szimulált leosztás átlagában {m:+.1f} pont a különbség (± {1.96 * se:.1f}).")
    topic = {"pickup": "rablas", "pass": "rablas", "concede": "bedobas"}[names[i]]
    return Finding("pickup" if "pickup" in (e[0], names[i]) else "concede", p, "Rablás / bedobás",
                   label[e[0]], label[names[i]], m, why, book_advice(topic, _topic_comps(ci0), 2)), 1


def _kontra_event(g: Game, e: Tuple[int, tuple], you: Optional[int], pol: LearnedPolicy, n: int, rng: random.Random,
                  z: float, min_diff: float) -> Tuple[Optional[Finding], int]:
    p, ks = e
    if you is not None and p != you:
        return None, 0
    sets = _kontra_sets(g, p, ks)
    if len(sets) < 2:
        return None, 0
    fns = [(s, (lambda w, s=s: w.do_kontra(p, list(s)))) for s in sets]
    vals = _compare(g, p, fns, n, rng, pol, 1)
    ch = sets.index(tuple(sorted(set(ks))))
    best = _best_alt(vals, ch, z, max(min_diff, KONTRA_MIN))
    if best is None:
        return None, 1
    i, m, se = best
    more = len(sets[i]) > len(sets[ch])
    rek = p == g.decl
    title = ("Rekontra" if rek else "Kontra") + (": kontráznod kellett volna" if more else ": fölösleges volt")
    why = _kontra_why(pol, g, p, sets[ch], sets[i]) + _rate_why(vals[ch], vals[i])
    why.append(f"{n} szimulált leosztás átlagában {m:+.1f} pont a különbség (± {1.96 * se:.1f}); a másik két játékos "
               f"lapját csak becsülni tudjuk, ezért a kontra-tanács kevésbé biztos, mint a többi.")
    return Finding("rekontra" if rek else "kontra", p, title, _set_text(sets[ch]), _set_text(sets[i]), m, why,
                   book_advice("rekontra" if rek else "kontra", _topic_comps(g.ci), 2)), 1


def _trick_text(g: Game) -> str:
    return ", ".join(f"{SEATS[q]}: {card_name(c)}" for q, c in g.trick) or "te vezetsz"


def _play_event(g: Game, e: Tuple[int, int], you: Optional[int], pol: LearnedPolicy, n: int, rng: random.Random,
                z: float, min_diff: float) -> Tuple[Optional[Finding], int]:
    p, card = e
    if you is not None and p != you:
        return None, 0
    legal = g.legal_cards(p)
    if len(legal) < 2:
        return None, 0
    cands = [card] + [c for c in legal if c != card]
    fns = [(c, (lambda w, c=c: w.play_card(p, c))) for c in cands]
    vals = _compare(g, p, fns, n, rng, pol, 1)
    best = _best_alt(vals, 0, z + 1.0, max(min_diff, 2.0))      # sok lap-döntés van: szigorúbb küszöb (több összehasonlítás)
    if best is None:
        return None, 1
    i, m, se = best
    why = _rate_why(vals[0], vals[i])
    why.append(f"{n} szimulált folytatás átlagában {m:+.1f} pont a különbség (± {1.96 * se:.1f}).")
    why.append(f"Az ütés addig: {_trick_text(g)}.")
    topic = "kijatszas" if p == g.decl else "vedekezes"
    return Finding("play", p, f"{g.trick_no + 1}. ütés: lapválasztás", card_name(card), card_name(cands[i]), m, why,
                   book_advice(topic, _topic_comps(g.ci), 2), g.trick_no + 1), 1


# ---------------------------------------------------------------- UI-napló (a böngészőből) -> DealRecord
def record_from_ui(d: Dict[str, Any]) -> DealRecord:
    """A UI naplója: {"dealer", "hands": [[id]*3] (az osztás), "blog": [["bid", p, ci, adu, min, [lerakott id]] |
    ["pickup"|"pass"|"concede", p]], "klog": [[p, [komponensek]]], "plays": [[p, id]]} (a kártyaazonosító szín*8+rang)."""
    hands = [[int(c) for c in h] for h in d["hands"]]
    flat = [c for h in hands for c in h]
    if len(hands) != 3 or sorted(len(h) for h in hands) != [10, 10, 12] or sorted(flat) != list(range(32)):
        raise ValueError("az osztásban 3 kéz (12+10+10 lap), összesen a 32 különböző lap kell")
    if int(d["dealer"]) not in (0, 1, 2) or len(hands[(int(d["dealer"]) + 1) % 3]) != 12:
        raise ValueError("a 12 lapos kéz az osztó utáni játékosé")
    blog: List[tuple] = []
    for e in d["blog"]:
        if e[0] == "bid":
            if not (0 <= int(e[2]) < len(CONTRACTS)):
                raise ValueError("ismeretlen vállalás")
            blog.append(("bid", int(e[1]), int(e[2]), int(e[3]), int(e[4]), tuple(int(c) for c in e[5])))
        elif e[0] in ("pickup", "pass", "concede"):
            blog.append((e[0], int(e[1])))
        else:
            raise ValueError("ismeretlen licit-esemény")
    klog = [(int(p), tuple(str(k) for k in ks)) for p, ks in d.get("klog", [])]
    plays = [(int(p), int(c)) for p, c in d.get("plays", [])]
    rec = DealRecord(int(d["dealer"]), hands, blog, klog, plays, d.get("rules", "friend"))
    return rec


# ---------------------------------------------------------------- opcionális LLM-stilizálás (watsonx), számellenőrzéssel
import re

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _facts_of(f: Finding) -> str:
    return "\n".join([f.title, f"Ezt tetted: {f.chosen}", f"Jobb: {f.better}", f"Becsült veszteség: {f.loss:.1f} pont"]
                     + f.why + [f"Könyv: {b['quote']}" for b in f.book])


def faithful(original: str, styled: str) -> bool:
    """Az átfogalmazott szöveg csak az eredetiben szereplő számokat és lapneveket tartalmazhatja (nincs kitalált tény),
    és minden eredeti szám és lapnév megmarad benne."""
    nums = lambda t: sorted(x.replace(",", ".") for x in _NUM.findall(t))
    cards = lambda t: sorted(set(re.findall(r"(?:piros|tök|zöld|makk) (?:VII|VIII|IX|X|alsó|felső|király|ász)", t)))
    return set(nums(styled)) <= set(nums(original)) and set(nums(original)) <= set(nums(styled)) \
        and cards(original) == cards(styled)


def polish(f: Finding, ask=None) -> str:
    """A megállapítás természetesebb magyar szövege. ask(prompt) -> str (alapértelmezés: wx.text). Ha az LLM nem elérhető,
    vagy a szövege bármilyen számot/lapot megváltoztat, a sablonos szöveg marad: tényt az LLM nem módosíthat."""
    base = f.text()
    try:
        if ask is None:
            import wx
            ask = wx.text
        out = ask("Fogalmazd át barátságos, tömör magyar tanácsnak az alábbi ulti-elemzést. Csak a megadott tényeket használd: "
                  "minden számot és lapnevet változtatás nélkül tarts meg, újat ne írj, idézetet ne módosíts.\n\n" + _facts_of(f))
    except Exception:
        return base
    out = (out or "").strip()
    return out if out and faithful(_facts_of(f), out) else base

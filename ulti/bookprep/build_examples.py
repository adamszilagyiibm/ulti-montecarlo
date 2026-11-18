"""
build_examples.py - A példatár példáinak TELJES rekonstrukciója és visszajátszása a rules.Game-en.

Bemenet:   diagrams.json (kezek + talon-sorok a képből), facts.json (vállalás, kontrák, könyv-pontszám),
           az OCR-szöveg (licit-lépések, ütéstábla).
Lépések:   1. a licit lépéseit (ki mit mondott be) a szöveges modell adja, háromszoros szavazással,
           2. a végső kezekből és a talon-sorokból VISSZAFELÉ számoljuk a kiinduló osztást
              (a kiinduló kezek + az eredeti talon = pontosan a 32 lapos pakli: ez kemény ellenőrzés),
           3. a licitet, a kontrát és a kijátszást lejátsszuk a rules.Game-en; minden lépés szabályos kell legyen,
              az ütésnyertes hívja a következő ütést,
           4. a kapott pénzt a könyv Végeredményével vetjük össze.
Kimenet:   replay.json (példánként: státusz, a talált problémák, a kiinduló osztás, a lépések, az eredmény).
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE
import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

import wx
from extract_examples import STARTS
from kodex_cards import card_str
from parse_tricks import NAME_PATS, example_text, forms, pages_text, sim, split_plays, trick_section
from extract_facts import contract_index
from rules import CONTRACTS, Game, nxt, set_ruleset

set_ruleset("book")      # a példákat a KÖNYV díjaival játsszuk vissza (a 20-100 6, a durchmars 7 egység, van 4 ász)

ROOT = Path(__file__).parent
ALL = ("Albert", "Béla", "Csaba", "Dénes")
SUIT_IDX = {"P": 0, "T": 1, "Z": 2, "M": 3}
PART_KEY = {"ulti": "ulti", "parti": "parti", "40-100": "c40", "20-100": "c20", "betli": "betli", "durchmars": "durch", "4asz": "asz4"}

STEP_PROMPT = (
    "Alább egy rablóulti-példa szövege (OCR). A játékosok sorrendje az osztó után: {order}. Az első játékos a felvevő: felveszi a talont, két lapot letesz, és bemond egy vállalást. "
    "Utána a következők vagy passzolnak, vagy RABOLNAK (felveszik a talont, két lapot letesznek, és magasabb vállalást mondanak be).\n"
    "Feladat: add vissza a licit LÉPÉSEIT időrendben, EGYETLEN JSON objektumként, más szöveg nélkül:\n"
    '{{"steps": [{{"player": "<név>", "components": ["<figurák: parti|40-100|20-100|ulti|4asz|durchmars|betli>"], "open": true|false, "trump": "P|T|Z|M|null"}}, ...]}}\n'
    "- Pontosan {nsteps} lépés van (a talon-doboznak {nrows} sora van: az 1. az eredeti talon, minden további sor egy lerakás). Az első lépés a felvevő bemondása (a talonletétel UTÁN), a többi a rablók bemondásai sorrendben.\n"
    "- Ha a felvevő/rabló a szöveg szerint \"valamilyen színre vette fel a talont\" vagy \"figura nélkül\" mondta be, a components ['parti'], a trump a megnevezett szín. Ez akkor is lépés, ha utána \"passzolt\".\n"
    "- A CSENDES figurák (\"csendes ultimó\", \"csendes 100\") NEM bemondások, sosem számítanak lépésnek vagy vállalásnak.\n"
    "- A 'piros' NEM külön figura: a piros színt a trump 'P' jelzi. Az 'open' true, ha a bemondás TERÍTETT (terített durchmars, terített betli).\n"
    "- Csak azokat a játékosokat írd, akik felvették a talont (passzolók nem szerepelnek). Ha valaki ugyanazt a lapot tette vissza, de bemondott, az is lépés.\n"
    "- A trump a vállalás színe (P piros, T tök, Z zöld, M makk); betlinél és színtelen durchmarsnál null.\n\n"
    "--- PÉLDA ---\n{text}\n--- VÉGE ---"
)


def j(txt: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def pre_trick_text(t: str) -> str:
    m = re.search(r"A 10 ütés|Az ütések|ütések így|A \d+ ütés", t)
    return t[: m.start()] if m else t


def extract_steps(n: int, order: List[str], P: Dict[int, str], nrows: int, runs: int = 3) -> Tuple[Optional[list], int, int]:
    t = pre_trick_text(example_text(n, P))
    votes: Counter = Counter()
    store: Dict[str, list] = {}
    for i in range(runs):
        txt = wx.text(STEP_PROMPT.format(order=", ".join(order), nsteps=nrows - 1, nrows=nrows, text=t[:7000]), max_tokens=500,
                      temperature=0.0 if i == 0 else 0.4, seed=i)
        d = j(txt)
        if not d or not isinstance(d.get("steps"), list):
            continue
        steps = []
        ok = True
        for s in d["steps"]:
            try:
                ci = contract_index(s)
                tr = s.get("trump")
                tr = SUIT_IDX.get(tr, -1) if isinstance(tr, str) else -1
                if s["player"] not in order or ci is None:
                    ok = False
                    break
                steps.append((s["player"], ci, tr))
            except Exception:
                ok = False
                break
        if ok and steps and len(steps) == nrows - 1:
            k = json.dumps(steps, ensure_ascii=False)
            votes[k] += 1
            store[k] = steps
    if not votes:
        return None, 0, runs
    k, v = votes.most_common(1)[0]
    return store[k], v, sum(votes.values())


def reconstruct(order: List[str], finals: Dict[str, FrozenSet[int]], rows: List[Tuple[int, int]],
                steps: List[tuple]) -> Tuple[Dict[str, set], List[str]]:
    """Visszafelé számolt kiinduló kezek. A 'step s' a rows[s]-t veszi fel és a rows[s+1]-et teszi le."""
    probs: List[str] = []
    H = {n: set(finals[n]) for n in order}
    for s in range(len(steps) - 1, -1, -1):
        p = steps[s][0]
        taken, put = set(rows[s]), set(rows[s + 1])
        kept, fromhand = taken - put, put - taken
        if not kept <= H[p]:
            probs.append(f"{s + 1}. lépés ({p}): a felvett lapok nincsenek a végső kezében")
        if fromhand & H[p]:
            probs.append(f"{s + 1}. lépés ({p}): a letett lap a végső kezében is benne van")
        H[p] = (H[p] - kept) | fromhand
    for n in order:
        if len(H[n]) != 10:
            probs.append(f"{n} kiinduló keze {len(H[n])} lapos")
    allc = [c for h in H.values() for c in h] + list(rows[0])
    if len(set(allc)) != 32 or len(allc) != 32:
        probs.append(f"a kiinduló kezek + az eredeti talon nem a 32 lapos pakli ({len(set(allc))} különböző)")
    return H, probs


def pick_card(chunk: str, hand: List[int], legal: List[int]) -> Tuple[Optional[int], str]:
    if not hand:
        return None, "nincs lap"
    sc = sorted(((sim(chunk, c), c in legal, c) for c in hand), key=lambda x: (-x[0], -x[1]))
    best = sc[0]
    if not best[1]:
        # szabályos lapok közül a legjobb, ha közel van a legjobbhoz (OCR-hiba)
        lg = [x for x in sc if x[1]]
        if lg and lg[0][0] >= best[0] - 0.12:
            return lg[0][2], "ocr-javított"
        return best[2], "szabálytalan"
    if len(sc) > 1 and sc[1][0] >= best[0] - 1e-9 and sc[1][1]:
        return best[2], "kétértelmű"
    return best[2], "ok"


def replay(n: int, dg: dict, facts: dict, steps: list, P: Dict[int, str]) -> dict:
    out: dict = {"example": n, "issues": [], "tricks_played": 0}
    rd = dg["reading"]
    finals = {nm: frozenset(c) for nm, c in rd["hands"].items()}
    dealer = rd["dealer"]
    if dealer not in ALL:
        dealer = next((a for a in ALL if a not in finals), "")
    if not dealer or dealer in finals:
        out["issues"].append("az osztó nem azonosítható")
        out["status"] = "hiba"
        return out
    i0 = ALL.index(dealer)
    order = [ALL[(i0 + k) % 4] for k in (1, 2, 3)]
    if set(order) != set(finals):
        out["issues"].append(f"a diagram játékosai {sorted(finals)} nem egyeznek az osztó utáni sorrenddel {order}")
        out["status"] = "hiba"
        return out
    rows = [tuple(r) for r in rd["talon"]]
    out["dealer"], out["order"] = dealer, order
    if len(steps) != len(rows) - 1:
        out["issues"].append(f"a licit-lépések száma ({len(steps)}) nem egyezik a talon-sorokkal ({len(rows)} sor -> {len(rows) - 1} lépés)")
        out["status"] = "hiba"
        return out
    if steps[0][0] != order[0]:
        out["issues"].append(f"az első felvevő {steps[0][0]}, de az osztó után {order[0]} jön")
    H, probs = reconstruct(order, finals, rows, steps)
    out["issues"] += probs
    if probs:
        out["status"] = "hiba"
        return out
    seat = {nm: k for k, nm in enumerate(order)}
    out["initial"] = {nm: sorted(H[nm]) for nm in order}
    out["talon_rows"] = [list(r) for r in rows]
    hands = [sorted(H[order[0]] | set(rows[0])), sorted(H[order[1]]), sorted(H[order[2]])]
    g = Game.from_hands(2, hands)
    # --- licit ---
    for s, (nm, ci, tr) in enumerate(steps):
        p = seat[nm]
        if s > 0:
            guard = 0
            while g.phase == "bid" and g.turn != p and guard < 6:
                guard += 1
                g.do_pass(g.turn)
            if g.phase != "bid" or g.turn != p:
                out["issues"].append(f"{s + 1}. lépés: {nm} nem kerül sorra a licitben")
                out["status"] = "hiba"
                return out
            if not g.can_pick_up(p):
                out["issues"].append(f"{s + 1}. lépés: {nm} nem rabolhat (a licit-lépcső már a tetején)")
                out["status"] = "hiba"
                return out
            g.do_pickup(p)
        disc = list(rows[s + 1])
        if ci < g.min_bid_index():
            out["issues"].append(f"{s + 1}. lépés: {nm} bemondása ({CONTRACTS[ci]['name']}) nem magasabb az előzőnél")
            out["status"] = "hiba"
            return out
        err = g.validate_bid(p, disc, ci, tr)
        if err:
            out["issues"].append(f"{s + 1}. lépés: {nm} {CONTRACTS[ci]['name']} bemondása érvénytelen: {err}")
            out["status"] = "hiba"
            return out
        g.do_bid(p, disc, ci, tr)
    guard = 0
    while g.phase == "bid" and guard < 8:
        guard += 1
        g.do_pass(g.turn)
    if g.phase != "kontra":
        out["issues"].append("a licit nem zárult le")
        out["status"] = "hiba"
        return out
    out["final"] = {"declarer": order[g.decl], "contract": CONTRACTS[g.ci]["name"], "trump": g.trump}
    f = facts.get("facts") or {}
    if f.get("final_declarer") and f["final_declarer"] != order[g.decl]:
        out["issues"].append(f"a végső felvevő a szöveg szerint {f['final_declarer']}, a licit-lépések szerint {order[g.decl]}")
    if f.get("contract_index") is not None and f["contract_index"] != g.ci:
        out["issues"].append(f"a végső vállalás a szöveg szerint '{CONTRACTS[f['contract_index']]['name']}', a lépések szerint '{CONTRACTS[g.ci]['name']}'")
    # --- kontra ---
    kreq = []
    for k in f.get("kontra", []) or []:
        if isinstance(k, dict) and k.get("player") in seat:
            kreq.append((seat[k["player"]], k.get("what"), str(k.get("part", "mind")).lower().replace(" ", "")))
    guard = 0
    while g.phase == "kontra" and guard < 6:
        guard += 1
        p = g.turn
        ks: List[str] = []
        opts = g.kontra_options(p)
        for (sk, what, part) in kreq:
            if sk != p or (what == "rekontra") != (p == g.decl):
                continue
            if part == "jatek":
                base = next((k for k in ("parti", "c40", "c20", "betli", "durch", "asz4") if k in opts), None)
                if base:
                    ks.append(base)
                else:
                    out["issues"].append(f"kontra ({order[p]}, játék): nincs kontrázható alapja")
            elif part in PART_KEY:
                ks.append(PART_KEY[part]) if PART_KEY[part] in opts else out["issues"].append(f"kontra ({order[p]}, {part}): nem kontrázható része a vállalásnak")
            elif part == "mind":
                ks += opts
            else:
                out["issues"].append(f"kontra '{part}' ({order[p]}): a motor nem modellezi")
        g.do_kontra(p, list(dict.fromkeys(ks)))
    out["kmult"] = [dict(k) for k in g.kmult]
    # --- kijátszás ---
    sec = trick_section(example_text(n, P))
    plays = split_plays(sec, order)
    tricks = [plays[i:i + 3] for i in range(0, len(plays) - len(plays) % 3, 3)]
    out["tricks_in_text"] = len(tricks)
    log: List[dict] = []
    for ti, tr in enumerate(tricks):
        if g.phase != "play":
            break
        lead = tr[0][0]
        if seat[lead] != g.turn:
            out["issues"].append(f"{ti + 1}. ütés: a szöveg szerint {lead} hív, a szabályok szerint {order[g.turn]}")
            break
        entry = []
        bad = False
        for (nm, chunk) in tr:
            p = seat[nm]
            if g.turn != p:
                out["issues"].append(f"{ti + 1}. ütés: {nm} nem következik")
                bad = True
                break
            c, st = pick_card(chunk, g.hands[p], g.legal_cards(p))
            if c is None or st == "szabálytalan":
                out["issues"].append(f"{ti + 1}. ütés: {nm} '{chunk}' -> {card_str(c) if c is not None else '?'} szabálytalan")
                bad = True
                break
            if st != "ok":
                out["issues"].append(f"{ti + 1}. ütés: {nm} '{chunk}' -> {card_str(c)} ({st})")
            g.play_card(p, c)
            entry.append((nm, c))
        if bad:
            break
        w = g.collect_trick()
        log.append({"n": ti + 1, "plays": entry, "winner": order[w]})
        out["tricks_played"] = ti + 1
    out["tricks"] = log
    if g.phase == "result" and g.result:
        ch = g.result["change"]
        d = g.decl
        out["engine"] = {"declarer_net_total": ch[d], "per_opponent": [ch[q] for q in range(3) if q != d],
                         "rows": [{"k": r["k"], "v": r["v"], "win": r["win"], "delta": r["delta"]} for r in g.result["rows"]]}
        bn = facts.get("book_net")
        out["book_net"] = bn
        if bn is not None:
            per = [ch[q] for q in range(3) if q != d]      # a felvevő ellenfeleinek nyeresége egyenként
            out["match"] = (all(x == 0 for x in per) if bn == 0
                            else all(abs(x) == abs(bn) for x in per) and (per[0] > 0) == (bn > 0))
        out["status"] = "teljes"
    else:
        out["status"] = "részleges" if log else "licit-csak"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(KNOWLEDGE / "replay.json"))
    a = ap.parse_args()
    dgs = json.loads((KNOWLEDGE / "diagrams.json").read_text(encoding="utf-8"))
    fcs = json.loads((KNOWLEDGE / "facts.json").read_text(encoding="utf-8"))
    P = pages_text()
    nums = [int(x) for x in a.only.split(",")] if a.only else sorted(int(k) for k in dgs)
    path = Path(a.out)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for n in nums:
        dg = dgs.get(str(n))
        if not dg or dg.get("status") != "ok":
            data[str(n)] = {"example": n, "status": "kihagyva", "issues": [dg.get("note", "nincs olvasható diagram") if dg else "nincs diagram"]}
            print(f"[{n:2d}] kihagyva ({data[str(n)]['issues'][0][:70]})")
            continue
        rd = dg["reading"]
        dealer = rd["dealer"] if rd["dealer"] in ALL else next((x for x in ALL if x not in rd["hands"]), "")
        order = [ALL[(ALL.index(dealer) + k) % 4] for k in (1, 2, 3)] if dealer in ALL else []
        try:
            steps, v, tot = extract_steps(n, order, P, len(rd["talon"]), a.runs)
        except Exception as e:
            data[str(n)] = {"example": n, "status": "hiba", "issues": [f"licit-kinyerés: {str(e)[:120]}"]}
            print(f"[{n:2d}] licit-kinyerés hiba")
            continue
        if not steps:
            data[str(n)] = {"example": n, "status": "hiba", "issues": ["a licit-lépések nem nyerhetők ki"]}
            print(f"[{n:2d}] a licit-lépések nem nyerhetők ki")
            continue
        r = replay(n, dg, fcs.get(str(n), {}), steps, P)
        r["steps"] = [{"player": s[0], "contract": CONTRACTS[s[1]]["name"], "ci": s[1], "trump": s[2]} for s in steps]
        r["steps_votes"] = f"{v}/{tot}"
        data[str(n)] = r
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        mt = "" if r.get("match") is None else (" EGYEZIK" if r["match"] else f" ELTÉR (motor {r['engine']['declarer_net_total']} vs könyv {r.get('book_net')})")
        print(f"[{n:2d}] {r['status']:9s} lépés {len(steps)} ({r['steps_votes']}), ütés {r['tricks_played']}/{r.get('tricks_in_text', '?')}, "
              f"{len(r['issues'])} megjegyzés{mt}", flush=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

"""
read_diagrams.py - A példatár diagramjainak (kezek + talon-doboz) beolvasása képből, többszörös futással.

Módszer:
  * a modell a nyomtatott jelölést másolja át soronkénti szövegként (nem értelmezi a lapokat),
  * a lapokat a kodex_cards.py értelmezi,
  * minden olvasatot ellenőrzünk: 3 db 10 lapos kéz + a talon utolsó sora = a 32 lapos pakli pontosan,
  * eltérő prompt / felbontás / vágás szerint több olvasat készül, az érvényesek közül a legtöbbször
    előforduló nyer; ha egy sem érvényes, a példa "kézi javításra" jelölt marad.

Használat: python read_diagrams.py [--only 1,2] [--runs 6] [--out diagrams.json]
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE, RAW
import argparse
import io
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from PIL import Image

import wx
from extract_examples import STARTS
from kodex_cards import deck_problems, parse_card_text, parse_cell

ROOT = Path(__file__).parent
PAGES = RAW / "kodex_pages"
DJVU = RAW / "kovacs_endre_ultikodex.djvu"
HI = RAW / "kodex_pages_hi"
OUT = KNOWLEDGE / "diagrams.json"
NAMES = ["Albert", "Béla", "Csaba", "Dénes"]

PROMPTS = {
    "A": ("Ez egy magyar kártyakönyv oldala. Fent van egy példa diagramja: három játékos neve alatt négy sor (P:, M:, T:, Z:) "
          "a lapjaikkal, középen a 'Talon:' doboz számozott sorokkal. Másold át SZÓ SZERINT, ahogy nyomtatva van, ebben a formában, "
          "más szöveg nélkül:\n"
          "JATEKOS <név>\nP: <lapok>\nM: <lapok>\nT: <lapok>\nZ: <lapok>\n(minden játékosra, a nevek: Albert, Béla, Csaba, Dénes)\n"
          "TALON\n1: <két lap>\n2: <két lap>\n... (a doboz minden sora, két lappal; a lap formája pl. 'T a' vagy 'Z k')\n"
          "OSZTO <név>\n"
          "Fontos: 'a' = alsó, 'ász' = ász, 'f' = felső, 'k' = király. A '–' jel üres színt jelent. Egy lapot se hagyj ki, és ne találj ki újat."),
    "B": ("Olvasd le gondosan a példa diagramját. Játékosonként add meg a P, M, T, Z sorok tartalmát pontosan úgy, ahogy nyomtatva van "
          "(az 'ász', '10', 'k', 'f', 'a', '9', '8', '7' jelöléseket változtatás nélkül), majd a Talon doboz sorait sorszámmal, soronként két lappal "
          "(szín betű + rang, pl. 'P a', 'Z 7'), végül ki az osztó. Formátum:\n"
          "JATEKOS <név>\nP: ...\nM: ...\nT: ...\nZ: ...\nTALON\n1: <lap>, <lap>\n2: ...\nOSZTO <név>\n"
          "Csak ezt írd, magyarázat nélkül. Ne keverd össze az 'a' (alsó) és az 'ász' jelölést."),
}


def hires(page: int, scale: int = 300) -> Image.Image:
    HI.mkdir(exist_ok=True)
    f = HI / f"p{page}_{scale}.png"
    if not f.exists():
        pnm = HI / f"p{page}_{scale}.pnm"
        subprocess.run(["ddjvu", "-format=pnm", f"-page={page}", f"-scale={scale}", str(DJVU), str(pnm)], check=True)
        Image.open(pnm).convert("L").save(f)
        pnm.unlink()
    return Image.open(f).convert("L")


def crop_variants(im: Image.Image) -> Dict[str, bytes]:
    """Különböző vágások: felső 62% / 75% / teljes oldal, kissé élesítve."""
    w, h = im.size
    out: Dict[str, bytes] = {}
    for name, frac in (("t62", 0.62), ("t75", 0.75), ("full", 1.0)):
        c = im.crop((0, 0, w, int(h * frac)))
        b = io.BytesIO()
        c.save(b, format="PNG")
        out[name] = b.getvalue()
    return out


Reading = Tuple[Dict[str, Dict[str, FrozenSet[int]]], List[Tuple[int, int]], str]


def parse_reading(txt: str) -> Optional[dict]:
    """A modell soronkénti átírásából: kezek (játékos -> 4 színes cella), talonsorok, osztó. None, ha értelmezhetetlen."""
    players: Dict[str, Dict[str, str]] = {}
    talon: List[Tuple[int, ...]] = []
    talon_nums: List[int] = []
    dealer = ""
    cur: Optional[str] = None
    mode = ""
    for raw in txt.splitlines():
        line = raw.strip().strip("*`#").strip()
        if not line:
            continue
        m = re.match(r"^JATEKOS\s+(\S+)", line, re.I)
        if m:
            cur, mode = m.group(1), "hand"
            players[cur] = {}
            continue
        if re.match(r"^TALON", line, re.I):
            mode = "talon"
            continue
        m = re.match(r"^OSZT[OÓ]\s+(\S+)", line, re.I)
        if m:
            dealer = m.group(1)
            mode = ""
            continue
        if mode == "hand" and cur:
            m = re.match(r"^([PMTZ])\s*:\s*(.*)$", line)
            if m:
                players[cur][m.group(1)] = m.group(2)
        elif mode == "talon":
            m = re.match(r"^(\d+)\s*[:.]\s*(.*)$", line)
            if m:
                if talon_nums and int(m.group(1)) in talon_nums:   # a modell néha ismétli a sorokat: az első kör számít
                    mode = ""
                    continue
                talon_nums.append(int(m.group(1)))
                cards = [parse_card_text(x) for x in re.split(r"[,;]", m.group(2)) if x.strip()]
                if len(cards) == 2 and all(c is not None for c in cards):
                    talon.append(tuple(cards))  # type: ignore[arg-type]
    hands: Dict[str, FrozenSet[int]] = {}
    for name, cells in players.items():
        if set(cells) != set("PMTZ"):
            return None
        acc: set = set()
        for sl, s in cells.items():
            cs = parse_cell(sl, s)
            if cs is None:
                return None
            acc |= cs
        if acc:                       # az osztónak (ha nem játszik) nincs keze: üres sort kihagyjuk
            hands[name] = frozenset(acc)
    if len(hands) != 3 or not talon:
        return None
    return {"hands": hands, "talon": talon, "dealer": dealer}


def check(rd: dict) -> List[str]:
    return deck_problems(list(rd["hands"].values()), list(rd["talon"][-1]))


def key_of(rd: dict) -> str:
    h = {n: sorted(c) for n, c in sorted(rd["hands"].items())}
    return json.dumps({"hands": h, "talon": [list(t) for t in rd["talon"]], "dealer": rd["dealer"]}, ensure_ascii=False)


def read_example(n: int, runs: int, verbose: bool = True) -> dict:
    page = STARTS[n]
    crops = crop_variants(hires(page))
    plan = [(p, v) for v in ("t62", "t75", "full") for p in ("A", "B")]
    valid: Counter = Counter()
    invalid: List[Tuple[dict, List[str]]] = []
    tried = 0
    for i in range(runs):
        pk, vk = plan[i % len(plan)]
        try:
            txt = wx.vision(PROMPTS[pk], crops[vk], max_tokens=900, temperature=0.0 if i < len(plan) else 0.3, seed=i)
        except Exception as e:
            if verbose:
                print(f"    {n}. példa futás {i + 1}: hiba {str(e)[:90]}")
            continue
        tried += 1
        rd = parse_reading(txt)
        if rd is None:
            continue
        probs = check(rd)
        if not probs:
            valid[key_of(rd)] += 1
            if valid.most_common(1)[0][1] >= 3:
                break
        else:
            invalid.append((rd, probs))
    out: dict = {"example": n, "page": page, "runs": tried, "valid": sum(valid.values())}
    if valid:
        best, votes = valid.most_common(1)[0]
        out.update(status="ok", votes=votes, reading=json.loads(best), distinct=len(valid))
    else:
        out.update(status="manual", problems=[p for _, ps in invalid[:3] for p in ps][:6])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    nums = [int(x) for x in a.only.split(",")] if a.only else sorted(STARTS)
    path = Path(a.out)
    data: Dict[str, dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for n in nums:
        r = read_example(n, a.runs)
        data[str(n)] = r
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tag = f"ok ({r['votes']}/{r['valid']} érvényes olvasat egyezik)" if r["status"] == "ok" else f"KÉZI: {r['problems'][:2]}"
        print(f"[{n:2d}. példa, {r['page']}. oldal] {r['runs']} futás -> {tag}", flush=True)


if __name__ == "__main__":
    main()

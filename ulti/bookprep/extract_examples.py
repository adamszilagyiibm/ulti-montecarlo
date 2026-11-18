"""
extract_examples.py - A Kovács-féle Ultikodex példatárának (137-212. oldal) kinyerése az oldalképekből.

Az OCR a kártyás ábrákat (kezek, talon) összekeveri, ezért a látó modell az oldalképet olvassa, és
strukturált példát ad vissza: kezek, talon, licit/rablás/bemondás/kontra, 10 ütés, végeredmény, tanulság.
A kinyert példát a check_examples.py a rules.Game-en visszajátszva ellenőrzi (lapszám, szabályos lapok,
ütésnyertesek), így a modell hibája nem kerül észrevétlenül a tanuló adatba.

Futtatás:  GOOGLE_API_KEY=... python extract_examples.py [--model M] [--only 1,2,3] [--out examples.json]
"""
from __future__ import annotations

from ulti.paths import RAW, WORK
import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

PAGES = RAW / "kodex_pages"
OUT = WORK / "examples.json"

# A példák kezdőoldalai (az OCR "N. példa" fejlécei alapján); a 30. példa a 212. oldalon ér véget.
STARTS: Dict[int, int] = {
    1: 140, 2: 143, 3: 145, 4: 147, 5: 149, 6: 151, 7: 154, 8: 156, 9: 159, 10: 161,
    11: 164, 12: 166, 13: 168, 14: 171, 15: 173, 16: 176, 17: 178, 18: 180, 19: 182, 20: 184,
    21: 186, 22: 189, 23: 192, 24: 194, 25: 197, 26: 200, 27: 202, 28: 204, 29: 206, 30: 208,
}
LAST_PAGE = 212

Suit = Literal["piros", "tok", "zold", "makk"]
Rank = Literal["VII", "VIII", "IX", "X", "Also", "Felso", "Kiraly", "Asz"]


class Card(BaseModel):
    suit: Suit
    rank: Rank


class Hand(BaseModel):
    player: str = Field(description="A játékos neve (Albert, Béla, Csaba, Dénes)")
    cards: List[Card] = Field(description="A diagramban nyomtatott lapok, MINDEN lap külön elemként")


class TalonGroup(BaseModel):
    label: str = Field(description="A talon-doboz sorszáma (1, 2, 3, 4, 5), ahogy nyomtatva van")
    cards: List[Card]


class Event(BaseModel):
    player: str
    action: Literal["licit", "felvetel", "rablas", "passz", "kontra", "rekontra", "bemondas", "jelentes", "bedobas", "egyeb"]
    text: str = Field(description="Mit mondott/csinált, rövid magyar leírás a szövegből")


class Play(BaseModel):
    player: str
    card: Card


class Trick(BaseModel):
    number: int
    plays: List[Play] = Field(description="A kijátszás sorrendjében (az első a hívó)")
    winner: str = Field(description="Az ütés nyertese: a FÉLKÖVÉREN szedett név")


class Score(BaseModel):
    player: str
    points: int = Field(description="Pont: nyereség pozitív, veszteség negatív")


class Example(BaseModel):
    number: int
    title: str
    dealer: str
    hands: List[Hand]
    talon_groups: List[TalonGroup]
    declarer: str = Field(description="Az eredeti felvevő (aki először felvette a talont)")
    contract: str = Field(description="A (végső) bemondott vállalás magyarul, pl. 'TÖK ultimó', 'PIROS 40-100', 'terített betli'")
    trump: Optional[Suit] = Field(description="Az adu színe, vagy null (betli/színtelen)")
    final_declarer: str = Field(description="Aki a végső vállalást játssza (a rabló, ha ő mondta be)")
    events: List[Event]
    tricks: List[Trick]
    result_text: str = Field(description="A 'Végeredmény' rész szó szerint")
    points: List[Score] = Field(description="Játékosonkénti pont, ha a szöveg közli (különben üres lista)")
    explanation: str = Field(description="A 'Magyarázat' rész teljes szövege, szó szerint")
    lesson: str = Field(description="A 'Tanulság' rész szó szerint, vagy üres")
    uncertain: List[str] = Field(description="Mit nem lehetett biztosan kiolvasni (lap, név), vagy üres lista")


PROMPT = (
    "Ez a Kovács Endre-féle Ultikodex (rablóulti) példatárának oldalképe(i). Add vissza a(z) {n}. PÉLDÁT, és csak azt.\n"
    "A lapok: színek P=piros, T=tök, Z=zöld, M=makk. Rangok: ász, 10, k=király, f=felső, a=alsó, 9, 8, 7.\n"
    "Szabályok:\n"
    "- A kezeket a diagramból olvasd: minden lapot külön elemként (pl. 'P: 10, k, a, 8, 7' = öt lap).\n"
    "- A talon-doboz sorait sorszám szerint add vissza (1., 2., 3. ...), mindegyiket a két lappal.\n"
    "- Az ütéseknél az első játékos a hívó, a FÉLKÖVÉR nevű játékos nyerte az ütést.\n"
    "- Az 'a' rang az ALSÓ, a 'f' a FELSŐ, az 'ász' az ÁSZ. Ne keverd össze.\n"
    "- Ha valamit nem látsz biztosan, ne találj ki semmit: írd az 'uncertain' listába.\n"
    "- A Magyarázat és a Tanulság szövegét szó szerint másold.\n"
)


def page_bytes(a: int, b: int) -> List[bytes]:
    return [(PAGES / f"p{p}.png").read_bytes() for p in range(a, b + 1)]


def extract_one(client: genai.Client, model: str, n: int, retries: int = 4) -> Example:
    a = STARTS[n]
    b = STARTS.get(n + 1, LAST_PAGE)
    b = max(b, a)
    parts: List[types.Part] = [types.Part.from_bytes(data=d, mime_type="image/png") for d in page_bytes(a, b)]
    parts.append(types.Part.from_text(text=PROMPT.format(n=n)))
    last: Optional[Exception] = None
    for k in range(retries):
        try:
            r = client.models.generate_content(
                model=model,
                contents=parts,
                config=types.GenerateContentConfig(
                    temperature=0.0, response_mime_type="application/json", response_schema=Example,
                ),
            )
            ex = Example.model_validate_json(r.text)
            return ex
        except Exception as exc:  # átmeneti 503/időtúllépés: újra
            last = exc
            time.sleep(4 * (k + 1))
    raise RuntimeError(f"{n}. példa: {type(last).__name__}: {str(last)[:200]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    ap.add_argument("--only", help="pl. 1,2,3")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise SystemExit("GOOGLE_API_KEY hiányzik")
    client = genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta", timeout=240_000))
    nums = [int(x) for x in a.only.split(",")] if a.only else sorted(STARTS)
    out_path = Path(a.out)
    data: Dict[str, dict] = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    for n in nums:
        t = time.time()
        try:
            ex = extract_one(client, a.model, n)
        except Exception as exc:
            print(f"[!] {n}. példa: {exc}", flush=True)
            continue
        data[str(n)] = {**ex.model_dump(), "model": a.model}
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[+] {n}. példa: {sum(len(h.cards) for h in ex.hands)} kézlap, {len(ex.tricks)} ütés, "
              f"{len(ex.uncertain)} bizonytalan | {time.time() - t:.0f} s", flush=True)


if __name__ == "__main__":
    main()

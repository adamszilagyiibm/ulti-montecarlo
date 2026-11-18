"""
extract_kodex.py - Tanácsok kinyerése a Kovács Endre-féle Ultikodex OCR-szövegéből, ellenőrzött idézetekkel.

A könyvet oldalcsoportokra bontjuk, és csoportonként kérünk strukturált tanácsokat a Geminitől. Minden
tanácshoz SZÓ SZERINTI idézet tartozik. A kód az idézetet megkeresi a forrásszövegben (OCR-hibákat
tűrő összevetéssel), és ami nem található meg, azt eldobja. A modell így nem találhat ki szabályt.

Futtatás:  GOOGLE_API_KEY=... python extract_kodex.py [--pages 85-136] [--limit N]
Kimenet:   kodex_advice.json  (csak az ellenőrzött tanácsok + statisztika)
"""
from __future__ import annotations

from ulti.paths import KNOWLEDGE, RAW
import argparse
import difflib
import json
import re
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from llm import make_llm

SRC = RAW / "kodex_ocr.txt"
OUT = KNOWLEDGE / "kodex_advice.json"
PAGE_RE = re.compile(r"^=== OLDAL (\d+) ===$", re.M)

Topic = Literal["licit", "talon", "kontra", "rekontra", "bedobas", "rablas", "kijatszas", "vedekezes", "pontozas", "egyeb"]


class Advice(BaseModel):
    topic: Topic = Field(description="Melyik döntéshez tartozik a tanács")
    contract: str = Field(description="Melyik vállalásra vonatkozik (pl. 'ulti', 'betli', 'durchmars', '40-100', 'parti'), vagy 'általános'")
    advice: str = Field(description="A tanács egy tömör, magyar mondatban")
    condition: str = Field(description="Mikor érvényes (a lap/helyzet feltétele), vagy üres")
    direction: Literal["tedd", "ne_told", "informacio"] = Field(description="Mit javasol: tedd / ne_told (kerüld) / informacio")
    quote: str = Field(description="SZÓ SZERINTI, összefüggő idézet a szövegből (egy-két mondat), amely a tanácsot igazolja")


class AdviceList(BaseModel):
    items: List[Advice]


PROMPT = (
    "Te egy gondos adatmérnök vagy. Az alábbi szöveg a Kovács Endre-féle Ultikodex (rablóulti) OCR-ból származó "
    "részlete: lehetnek benne OCR-hibák.\n"
    "Feladat: gyűjtsd ki azokat a TANÁCSOKAT és szabályokat, amelyek a licitálásra, a talonletételre, a rablásra, "
    "a kontrára/rekontrára, a bedobásra, a kijátszásra és a védekezésre vonatkoznak.\n\n"
    "Szigorú szabályok:\n"
    "- KIZÁRÓLAG a szövegben szereplő állítást add vissza, semmit ne egészíts ki a saját tudásodból.\n"
    "- Minden tételhez adj SZÓ SZERINTI idézetet (a szövegből másolva, változtatás nélkül, legfeljebb két mondat).\n"
    "- Ne vegyél fel példajátékok egyedi leírását (név, konkrét lapok), csak általános érvényű tanácsot.\n"
    "- Ha a részletben nincs ilyen tanács, adj üres listát.\n\n"
    "--- SZÖVEG KEZDETE ---\n{text}\n--- SZÖVEG VÉGE ---"
)


def split_pages(text: str) -> List[Tuple[int, str]]:
    marks = list(PAGE_RE.finditer(text))
    pages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        pages.append((int(m.group(1)), text[m.end():end].strip()))
    return pages


def chunk_pages(pages: List[Tuple[int, str]], per_chunk: int = 8) -> List[Tuple[Tuple[int, int], str]]:
    out = []
    for i in range(0, len(pages), per_chunk):
        grp = pages[i:i + per_chunk]
        out.append(((grp[0][0], grp[-1][0]), "\n\n".join(t for _, t in grp)))
    return out


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\wáéíóöőúüű]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_in_source(quote: str, source: str, threshold: float = 0.88) -> bool:
    """Az idézet (OCR-zajt tűrve) megtalálható-e a forrásban. A rövid (<25 jel) idézetet nem fogadjuk el."""
    q, s = _norm(quote), _norm(source)
    if len(q) < 25:
        return False
    if q in s:
        return True
    # csúszóablakos hasonlóság: a forrásban az idézet hosszával megegyező ablakokat vetjük össze
    n = len(q)
    step = max(1, n // 6)
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(q)
    for i in range(0, max(1, len(s) - n + 1), step):
        sm.set_seq1(s[i:i + n])
        if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold:
            return True
    return False


def verify(items: List[Advice], source: str) -> Tuple[List[Advice], int]:
    ok = [a for a in items if quote_in_source(a.quote, source)]
    return ok, len(items) - len(ok)


def extract_chunk(source: str) -> List[Advice]:
    llm = make_llm(temperature=0.0).with_structured_output(AdviceList)
    chain = ChatPromptTemplate.from_messages([("system", PROMPT), ("human", "Add vissza a tanácsokat.")]) | llm
    res: Optional[AdviceList] = chain.invoke({"text": source})
    return list(res.items) if res else []


def run(pages_range: Optional[Tuple[int, int]] = None, limit: Optional[int] = None, per_chunk: int = 8) -> dict:
    pages = split_pages(SRC.read_text(encoding="utf-8"))
    if pages_range:
        pages = [p for p in pages if pages_range[0] <= p[0] <= pages_range[1]]
    chunks = chunk_pages(pages, per_chunk)
    if limit:
        chunks = chunks[:limit]
    kept: List[dict] = []
    stats = {"chunks": len(chunks), "proposed": 0, "verified": 0, "rejected": 0, "failed_chunks": []}
    for (a, b), src in chunks:
        try:
            items = extract_chunk(src)
        except Exception as exc:  # egy elbukott szakasz ne vigye el az egészet
            stats["failed_chunks"].append({"pages": f"{a}-{b}", "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
            print(f"[!] {a}-{b}. oldal: hiba ({type(exc).__name__})")
            continue
        ok, bad = verify(items, src)
        stats["proposed"] += len(items)
        stats["verified"] += len(ok)
        stats["rejected"] += bad
        for it in ok:
            kept.append({**it.model_dump(), "pages": f"{a}-{b}"})
        print(f"[*] {a}-{b}. oldal: {len(items)} javasolt, {len(ok)} ellenőrzött, {bad} elvetve")
    data = {"source": "Kovács Endre: Ultikodex (OCR)", "stats": stats, "advice": kept}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[+] {len(kept)} ellenőrzött tanács -> {OUT.name}")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", help="pl. 85-136")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--per-chunk", type=int, default=8)
    a = ap.parse_args()
    pr = tuple(int(x) for x in a.pages.split("-")) if a.pages else None
    run(pr, a.limit, a.per_chunk)  # type: ignore[arg-type]

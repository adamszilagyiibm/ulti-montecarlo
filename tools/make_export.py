"""
make_export.py - Az átadható csomag (export/) összeállítása: ezt a mappát kell bezipelni és elküldeni.

A forrásfa már a végső szerkezetű (ulti/, data/, ui/, tools/, tests/, docs/), ezért az export másolás + szűrés:
bekerül minden, ami a futtatáshoz, tanításhoz, teszthez és dokumentációhoz kell; nem kerül be a venv, a gyorsítótár, a kulcsok (.env),
a Kovács-könyv nyers anyaga (raw/: OCR-szöveg, oldalképek, .djvu: szerzői jog), a work/ (futásidejű kimenetek) és az ideiglenes fájlok.
A könyv feldolgozott tudása (idézetek, példák) alapból bekerül (--no-book kihagyja). A barát eredeti ui/ulti.html-je változatlanul
bekerül, mellé a generált ui/ulti_py.html.

Használat:  python tools/make_export.py            # export/ újragenerálása + titok-ellenőrzés
            python tools/make_export.py --zip      # + export.zip
            python tools/make_export.py --check    # + a tesztek lefuttatása az export/ mappában
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "export"

TREES = ["ulti", "tests", "tools", "docs", "data", "ui"]            # teljes mappák (szűréssel)
FILES = ["README.md", "requirements.txt", ".env.example", ".gitignore"]
# a könyv KINYERT, feldolgozott tudása: alapból bekerül, mert a tanító bot és a kutatási modulok erre épülnek; --no-book kihagyja.
# A nyers anyag (raw/: OCR-szöveg, oldalképek, .djvu) SOHA nem kerül be.
BOOK_DATA = ["data/knowledge/book_knowledge.json", "data/knowledge/replay.json", "data/knowledge/book_bids.json",
             "data/knowledge/facts.json", "data/knowledge/diagrams.json", "data/knowledge/rabloulti_biblia.txt"]
SKIP_NAMES = {"__pycache__", ".DS_Store", "venv", ".venv", "work", "raw", "export", "examples.json", "ulti_standalone.html"}
SKIP_SUFFIX = {".pyc", ".bak", ".key", ".djvu", ".tmp"}

KEY = re.compile(r"AQ\.[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_\-]{30,}")


def _ignore(with_book: bool):
    book = {Path(p).name for p in BOOK_DATA}

    def ig(_dir: str, names: list) -> set:
        out = {n for n in names if n in SKIP_NAMES or Path(n).suffix in SKIP_SUFFIX or n == ".env"}
        if not with_book and Path(_dir).name == "knowledge":
            out |= {n for n in names if n in book}
        return out
    return ig


def build(with_book: bool = True) -> list:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    missing: list = []
    for t in TREES:
        if (ROOT / t).exists():
            shutil.copytree(ROOT / t, OUT / t, ignore=_ignore(with_book))
        else:
            missing.append(t)
    for f in FILES:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, OUT / f)
        else:
            missing.append(f)
    if with_book:
        missing += [p for p in BOOK_DATA if not (OUT / p).exists()]
    return missing


def scan_secrets() -> list:
    hits = []
    for f in OUT.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".js", ".json", ".md", ".html", ".txt", ".example", "") and f.stat().st_size < 5_000_000:
            try:
                if KEY.search(f.read_text("utf-8", errors="ignore")):
                    hits.append(str(f.relative_to(OUT)))
            except OSError:
                pass
        if f.name == ".env" or f.suffix == ".key":
            hits.append(str(f.relative_to(OUT)))
    return hits


def forbidden() -> list:
    bad = []
    for f in OUT.rglob("*"):
        if f.name in ("kodex_ocr.txt", "kodex_pages", "kodex_pages_hi", "league_gens", "venv", "__pycache__") or f.suffix == ".djvu":
            bad.append(str(f.relative_to(OUT)))
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--check", action="store_true", help="az export/ mappában lefuttatja a teszteket (a jelenlegi Pythonnal)")
    ap.add_argument("--no-book", action="store_true", help="a könyv kinyert tudása (idézetek, példák) nélkül; a könyves tesztek elbuknak")
    a = ap.parse_args()
    missing = build(not a.no_book)
    hits, bad = scan_secrets(), forbidden()
    files = [f for f in OUT.rglob("*") if f.is_file()]
    print(f"export/: {len(files)} fájl, {sum(f.stat().st_size for f in files) / 1e6:.1f} MB")
    if missing:
        print("HIÁNYZIK (kihagyva):", ", ".join(missing))
    if hits or bad:
        shutil.rmtree(OUT)
        raise SystemExit(f"LEÁLLVA: titok-gyanús vagy tiltott fájl(ok) az exportban, az export törölve: {hits + bad}")
    if a.check:
        r = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           cwd=OUT, capture_output=True, text=True)
        print((r.stderr or r.stdout).strip().splitlines()[-3:])
        if r.returncode:
            raise SystemExit("az export tesztjei elbuktak")
    if a.zip:
        z = ROOT / "export.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(files):
                zf.write(f, Path("ulti_motor") / f.relative_to(OUT))
        print(f"{z.name}: {z.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

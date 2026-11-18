"""Központi útvonalak: minden adat- és UI-fájl helye egy helyen (a csomag szerkezetét a docs/ATTEKINTES.md írja le)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POLICY = DATA / "policy"          # learned_policy.json, learned_book.json, kontra_rule.json
RULES = DATA / "rules"            # contracts.json, book_prices.json
KNOWLEDGE = DATA / "knowledge"    # a könyv feldolgozott tudása, heuristics_weights.json
RESEARCH = DATA / "research"      # kutatási modulok eredményei
UI = ROOT / "ui"
TOOLS = ROOT / "tools"
RAW = ROOT / "raw"                # a könyv nyers anyaga: SOHA nem kerül exportba
WORK = ROOT / "work"              # futásidejű munkamappa (liga-generációk, részeredmények), nem exportálandó

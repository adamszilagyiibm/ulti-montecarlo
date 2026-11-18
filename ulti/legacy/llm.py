"""
llm.py - Közös LLM-factory és könyvbetöltő (Zero-RAG: a teljes könyv System Prompt).

Környezeti változók:
  GOOGLE_API_KEY  - Gemini API kulcs (NEM kerül fájlba/verziókezelésbe)
  GEMINI_MODEL    - modellnév felülírása (alapértelmezés: gemini-flash-latest)
"""
from ulti.paths import KNOWLEDGE
import os
from pathlib import Path
from typing import Any, Optional


DEFAULT_MODEL = "gemini-flash-latest"
BOOK_PATH = KNOWLEDGE / "rabloulti_biblia.txt"


def make_llm(temperature: float = 0.0, api_key: Optional[str] = None) -> Any:
    """Gemini kliens időtúllépéssel és újrapróbálással (a 503 'high demand' átmeneti). A csomag csak itt töltődik be (opcionális)."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        temperature=temperature,
        google_api_key=api_key,
        timeout=90,
        max_retries=5,
    )


def content_text(content: Any) -> str:
    """A Gemini válasza lehet sima szöveg vagy blokklista; mindkettőből szöveget csinál."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def load_book(path: Path = BOOK_PATH) -> str:
    return Path(path).read_text(encoding="utf-8")

"""
extract_heuristics.py - Offline adatkinyerő: nyers könyvszöveg -> strukturált JSON heurisztika.

A teljes könyv System Promptként megy a Geminihez (Zero-RAG), a kimenetet
`with_structured_output` kényszeríti a Pydantic sémára.
"""
import json
from pathlib import Path
from typing import Dict, List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from engine import Rank
from llm import BOOK_PATH, load_book, make_llm

OUTPUT_PATH = Path(__file__).with_name("heuristics_weights.json")


# ============================================================================
# PYDANTIC OUTPUT SCHEMA
# ============================================================================

class LeadPreferences(BaseModel):
    lead_trump_when_declarer: float = Field(description="Súly adu azonnali lehívására felvevőként (aduzás)")
    lead_singleton_for_defense: float = Field(description="Súly szingli / színhiány kialakítására védőként")
    lead_high_card_first: float = Field(description="Súly biztos ász / magas lap kihívására védőként")
    lead_safe_ten: float = Field(description="Súly védett tízes kijátszására")


class DefenseWeights(BaseModel):
    feed_ten_or_ace_to_partner: float = Field(description="Súly 10 pontos lap bekenésére a védőtárs biztos ütésébe")
    save_trump_seven_for_last: float = Field(description="Súly az adu VII megtartására az utolsó ütésre (ulti)")
    keep_stoppers_in_betli: float = Field(description="Súly a betli buktatásához szükséges fogólapok tartására")


class ContractEvaluation(BaseModel):
    name: str = Field(description="A vállalás neve a licittáblázat szerint, pl. 'Ulti', 'Piros 40-100'")
    base_points: int = Field(description="Díjazás a licittáblázat szerint (pl. ulti 4+1 esetén 5)")
    is_piros: bool = Field(description="Piros aduszínű (dupla értékű) változat-e")
    min_trumps_recommended: int = Field(description="Ajánlott minimális aduszám a könyv szerint")
    required_stoppers_or_aces: int = Field(description="Ajánlott ász/X/ütőlap-szám a könyv szerint")
    conditions: str = Field(description="Teljesítési feltétel és tipikus kezdőlap röviden, a könyv alapján")


class HandCombination(BaseModel):
    contract: str = Field(description="Melyik vállalás kezdőlap-kombinációja")
    description: str = Field(description="A kezdőlap jellemzői (pl. '4-5 adu, ász, X, egy hosszú színnel')")
    min_trumps: int
    min_aces_or_tens: int
    needs_trump_seven: bool = Field(description="Kell-e az adu VII (ultimóhoz)")
    needs_trump_marriage: bool = Field(description="Kell-e az adu Király-Felső pár (40-100-hoz)")


class OddsNote(BaseModel):
    topic: str = Field(description="Pl. 'ellenfelek adueloszlása 4 adulappal'")
    statement: str = Field(description="A könyv állítása (valószínű / ritka / veszélyes eloszlás)")
    estimated_probability: float = Field(description="Becsült valószínűség 0-1 között, ha a könyv alapján adható; különben -1")


class UltiHeuristicsSchema(BaseModel):
    lead_preferences: LeadPreferences
    defense_weights: DefenseWeights
    card_eval_bias: Dict[str, float] = Field(
        description="Lapok alapsúlya kulcsokkal: Rank.ASZ, Rank.X, Rank.KIRALY, Rank.FELSO, Rank.ALSO, Rank.IX, Rank.VIII, Rank.VII"
    )
    contracts: List[ContractEvaluation] = Field(description="A TELJES licittáblázat minden sora")
    hand_combinations: List[HandCombination]
    odds_notes: List[OddsNote]


# ============================================================================
# EXTRACTION PIPELINE
# ============================================================================

SYSTEM_PROMPT = (
    "Te egy kártyajáték-matematikus és adatmérnök vagy. A Rablóulti szakkönyv teljes szövege alább van.\n"
    "Feladatod: a könyvből KIZÁRÓLAG a szövegben szereplő információkat kinyerni a játékmotor számára.\n\n"
    "Szabályok:\n"
    "- A 'contracts' listába a licittáblázat (Egyszerű és összetett vállalások listája) MINDEN sora kerüljön be, "
    "a díjazás számértékével (az 'Ulti 4 + 1' alap pontértéke 5).\n"
    "- A heurisztikus súlyok (lead_preferences, defense_weights, card_eval_bias) 0-50 közötti, a könyv "
    "tanácsainak fontosságát tükröző számok; a card_eval_bias a lapok relatív értéke (Ász a legnagyobb).\n"
    "- Ha egy számot a könyv nem ad meg, adj észszerű becslést a szöveg tanácsai alapján, és ne találj ki ellentmondó szabályt.\n\n"
    "--- KÖNYVSZÖVEG KEZDETE ---\n{book_text}\n--- KÖNYVSZÖVEG VÉGE ---"
)


def extract_heuristics(book_text: str) -> UltiHeuristicsSchema:
    llm = make_llm(temperature=0.0).with_structured_output(UltiHeuristicsSchema)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Végezd el a strukturált heurisztika-kinyerést a könyv alapján."),
    ])
    return (prompt | llm).invoke({"book_text": book_text})


def normalize_rank_keys(bias: Dict[str, float]) -> Dict[str, float]:
    """A modell néha 'ASZ'/'Ász' kulcsokat ad; a motor 'Rank.ASZ' alakot vár."""
    by_name = {r.name: r.name for r in Rank}
    by_name.update({r.value.upper(): r.name for r in Rank})
    out: Dict[str, float] = {}
    for key, value in bias.items():
        name = by_name.get(key.removeprefix("Rank.").upper())
        if name is None:
            raise ValueError(f"Ismeretlen rang a card_eval_bias-ban: {key!r}")
        out[f"Rank.{name}"] = value
    return out


def extract_and_save_heuristics(book_file_path: Path = BOOK_PATH, output_json_path: Path = OUTPUT_PATH) -> dict:
    print("[*] Heurisztikák kinyerése folyamatban a Gemini LLM segítségével...")
    result = extract_heuristics(load_book(Path(book_file_path)))
    data = result.model_dump()
    data["card_eval_bias"] = normalize_rank_keys(data["card_eval_bias"])
    Path(output_json_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Sikeres kinyerés! Súlyok elmentve ide: {output_json_path}")
    return data


if __name__ == "__main__":
    extract_and_save_heuristics()

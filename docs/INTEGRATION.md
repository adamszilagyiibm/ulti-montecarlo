# Átadási leírás: motor cseréje és UI-integráció

Ez a dokumentum annak szól, aki a meglévő UI mögé beköti, vagy lecseréli a játékmotort.

## 1. Gyorsindítás

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -t .   # 110 teszt, API-kulcs nélkül fut
python -m ulti.legacy.main --seed 7         # egy teljes gép-gép-gép játszma (régi lánc)
```

A régi, Gemini-s lánc LLM-es funkcióihoz (`ulti/legacy/`: Tutor, Judge, kinyerés) kell a kulcs. A szerverhez és a tanító bothoz nem kell: ott az opcionális watsonx-réteg csak a `polish`-hoz használ tokent. **A kulcsot soha ne írd fájlba vagy kódba**, csak környezeti változóba:

```bash
cp .env.example .env               # a .env a .gitignore-ban van
export GOOGLE_API_KEY="..."        # vagy töltsd be a .env-ből a saját módszereddel
export GEMINI_MODEL=gemini-flash-lite-latest   # opcionális
```

Modell: alapértelmezés `gemini-flash-latest`. Az élő tesztek idején ez 503 ("high demand") hibát adott, a `gemini-flash-lite-latest` stabilan működött. A `gemini-2.5-flash` és a `gemini-2.5-pro` új felhasználóknak már nem elérhető. A kliens 5 újrapróbálkozással és 90 s időtúllépéssel dolgozik ([`ulti/legacy/llm.py`](../ulti/legacy/llm.py)).

## 2. A régi `api.GameSession` (elavult, `ulti/legacy/`)

A böngészős UI a 3/b fejezet HTTP-végpontjait használja. A `GameSession` a régi, licit nélküli motor CLI/API-ja, tartaléknak megmaradt.

Az [`ulti/legacy/api.py`](../ulti/legacy/api.py) az egyetlen belépési pont, JSON-barát adatokkal. Az ember a 3 hely egyike (`human_id` 0, 1 vagy 2), a másik kettőt a motor játssza.

```python
from api import GameSession   # import ulti után (sys.path)

s = GameSession.new(human_id=0, seed=42)   # a gépek az ember soráig már lejátszottak
v = s.view()                               # ebből rajzol a UI
r = s.play("makk Ász")                     # az ember lépése; ValueError, ha szabálytalan
# r = {"bot_moves": [...a gépek lépései...], "view": {...}}
s.suggest()                                # a motor javaslata (nem hajtja végre)
s.explain("makk Ász")                      # Tutor (LLM) magyarázat, GOOGLE_API_KEY kell
```

A `view()` kulcsai:

| Kulcs | Jelentés |
|---|---|
| `contract`, `trump_suit`, `declarer_id` | a vállalás, az adu (vagy `null`) és a felvevő |
| `my_hand` | az ember lapjai: `{suit, rank, points, id}` |
| `legal_moves` | a most játszható lapok (a többit a UI szürkítse) |
| `current_trick` | az asztalon lévő lapok: `{player, card}` |
| `hand_sizes`, `tricks_won`, `tricks_played` | lapszámok, ütések, lejátszott ütések |
| `next_player`, `is_human_turn`, `game_over` | kinek a sora, vége-e |
| `talon` | csak akkor, ha az ember a felvevő |
| `result` | csak a játszma végén: `declarer_points`, `declarer_tricks`, `declarer_succeeded` |

Az ellenfelek lapjai nincsenek benne a `view()`-ban. A lapok azonosítója `"<szín> <rang>"`, pl. `"makk Ász"`, `"piros X"`. Színek: `piros, tök, zöld, makk`. Rangok: `VII, VIII, IX, Alsó, Felső, Király, X, Ász`. A `play()` a `{"suit": ..., "rank": ...}` objektumot is elfogadja.

## 3. A motor cseréje

A motor egyetlen metódust kell megvalósítson ([`MoveEngine`](../ulti/legacy/api.py)):

```python
class SajatMotor:
    def choose_move(self, state: GameState, player_id: int) -> Card: ...
```

Utána elég átadni a munkamenetnek:

```python
s = GameSession.new(human_id=0, engine=SajatMotor())
```

Szabályok a cseréhez:
- A motornak **szabályos lapot kell visszaadnia**: `state.get_legal_moves(player_id)` elemét. Különben a `play_card` `ValueError`-t dob (renonsz).
- A `state` a teljes információt tartalmazza (minden játékos lapját). Ha a motor csalás nélkül játsszon, csak a saját kezét és a már kijátszott lapokat használja. A mellékelt [`MonteCarloPlayer`](../ulti/legacy/montecarlo.py) a nem látott lapokat véletlenül osztja szét (determinizálás).
- A `state.clone()` gyors másolatot ad szimulációhoz, a `state.play_card()` lejátszik egy lapot.

Ha a barátod motorja más állapotreprezentációt használ, a `GameState` és a `Card` közötti konverziót érdemes egy adapterbe tenni, és az `ulti/legacy/engine.py` szabálylogikáját (`get_legal_moves`, `Trick.winner`) érintetlenül hagyni. Ezt fedik a tesztek.

## 3/b. Bekötés a barát UI-jába (`ui/ulti.html`)

Az eredeti fájlt nem módosítjuk, a `ulti/server/make_ui.py` egy másolatot (`ui/ulti_py.html`) készít egy horoggal:

```bash
python -m ulti.server.make_ui && python -m ulti.server.server   # http://127.0.0.1:8765/ulti_py.html
```

- **Kijátszás:** `botChoose(p)` helyett `botPlay(p)` -> `/api/choose`. A snapshot az ütéseket, a vállalást, a kontrákat (`kmult`), a kényszervállalást (`forced`), a bemondásokat (`marr`), a szabályos lapokat (`legal`) és nyitott játéknál a felfedett kezet (`reveal`) tartalmazza. A szerver ebből `rules.Game`-et épít ([`uibridge.game_from_play_snapshot`](../ulti/server/uibridge.py)), és Monte Carlo keresést futtat ([`mcplay.mc_choose`](../ulti/ai/mcplay.py), alapértelmezés 32 minta, `eps` 0, lépésenként 1,5 s időkeret). A választott lapot a szerver és a horog is ellenőrzi a `legal` listával. Ha a Game nem építhető fel (`ValueError`), a régi [`bridge.py`](../ulti/server/bridge.py) + `engine.py` motor a tartalék.
- **Licit:** `botBidAction(p)` helyett `botBid(p)` -> `/api/bid`. A kérés csak azt tartalmazza, amit a bot valódi játékban látna: a saját lapjait, a saját talonját, az álló licitet és a licitnaplót. A másik két kéz és a más által letett talon véletlen kitöltés ([`uibridge.py`](../ulti/server/uibridge.py)); a motor ezeket nem olvassa, a talon-modell a licitnaplóból következtet. A válaszot a horog érvényesíti (`validateBid`, `canPickUp`), érvénytelen esetén a JS bot dönt.
- **Kontra / rekontra:** `botKontra(p)` helyett `botKon(p)` -> `/api/kontra`.
- **Napló a tanácsadáshoz:** a horog becsomagolja a UI állapotváltó függvényeit (`newDeal`, `doBid`, `doPass`, `doPickUp`, `doConcede`, `doKontra`, `playCard`), és `PYLOG`-ba gyűjti a leosztás teljes naplóját. A leosztás végén egy "Tanács a Python motortól" panel és gomb jelenik meg, amely a naplót a `/api/review`-nak küldi (`you: 0`, az ember ülése). A barát saját "Mi lett volna jobb?" panelje érintetlen marad.
- Hiba vagy elérhetetlen szerver esetén az adott döntésnél a UI a saját botját használja. `?engine=js` ezt kényszeríti.
- A licithez és kontrához a `friend` szabályrendszer és a `data/policy/learned_policy.json` kell (a UI a 42 vállalásos táblával fut). A könyv díjaihoz (`data/policy/learned_book.json`, 4 ász) a UI-nak is át kell vennie a könyv díjait.
- A szerver csak `127.0.0.1`-re figyel, a kérés mérete 64 KB-ra korlátozott.
- **Ellenőrzés:** a generált `ulti_py.html` fejetlenül (jsdom) lefutott egy valódi szerver ellen: licit, kontra, kijátszás és `/api/review` a Pythonon át, 4xx/5xx nélkül. **Böngészős, kézi kipróbálás nem történt.**

## 4. Fájlok

| Hely | Szerep |
|---|---|
| `ulti/server/` | `server.py` (HTTP), `uibridge.py` (licit/kontra/kijátszás snapshot -> `rules.Game`), `bridge.py` (régi tartalék-adapter), `make_ui.py` (UI-generálás) |
| `ulti/ai/` | `policy.py`, `talon.py`, `mcplay.py` (Monte Carlo), `kontra_rule.py`, `arena.py` |
| `ulti/coach/` | `tutor.py` (tanító bot), `wx.py` (opcionális watsonx) |
| `ulti/core/` | `rules.py`, `jsbot.py`, `playbot.py`, `features.py` |
| `ulti/legacy/` | `engine.py`, `montecarlo.py`, `llm.py`, `api.py`, `main.py`, `refinement_loop.py`, `extract_heuristics.py` a `bookprep/`-ben |
| `data/` | `policy/` (tanult politikák), `rules/` (díjtáblák), `knowledge/` (a könyv feldolgozott tudása), `research/` |
| `tools/` | `e2e.js` (JS motor vs. Python motor), `e2e_diff.py`, `xcheck.js` (szabály-keresztellenőrzés), `make_export.py` |
| `tests/` | 110 teszt |

## 5. Ismert korlátok (őszintén)

- **A UI-ban a kijátszást a `rules.py`-s Monte Carlo (`mcplay.py`) adja, a licitet és a kontrát a tanult politika.** A `GameSession` (CLI/API, `main.py`) még a régi `engine.py` motort használja, licit nélkül. A tanító bot a `playbot` kijátszást szimulálja.
- **A régi (`engine.py`) heurisztikus motor gyenge, a régi Monte Carlo a JS bottal egy szintű** (ez már csak tartalék). 300 leosztáson, azonos licittel: heurisztikus 38/150 a JS bot 120/150-éhez képest. Monte Carlo 100/150 vs 90/150 (8 minta), illetve 92/150 vs 100/150 (12 minta), ami mintahibán belüli. A kinyert súlyok a heurisztikus felvevői arányon sem javítottak (12,5% → 12,3%, 600 játszmán): a szűk keresztmetszet a heurisztika szerkezete, nem a súlyok.
- **Nincs böngészős teszt.** Az `ulti_py.html`-t jsdom-ban futtattuk (nincs böngésző a gépen), a kinézetet és a kattintásokat kézzel kell kipróbálni.
- **A Judge nem megbízható bíró.** Élő futáson egy indoklás ellentmondott magának ("nem tartotta be a színadást", miközben a motor csak szabályos lapot választhat). Ezért a súlyfrissítés korlátozott (±3 / iteráció, 0–60 tartomány). Hosszú `--refine` futás előtt készíts mentést a `data/knowledge/heuristics_weights.json`-ról.
- A Tutor magyarázata nem determinisztikus, és a nyers `GameState`-ből dolgozik. A UI mutassa tájékoztató jellegűként.

## 6. API-szerződés (minden külső hívás HTTP-n)

Minden UI -> Python hívás `POST` + JSON a `127.0.0.1`-en. Hiba esetén a válasz mindig `{"error": "..."}`, a UI az adott döntésnél a saját botjára esik vissza.

| Kód | Jelentés |
|---|---|
| 200 | sikeres |
| 400 | hibás kérés (érvénytelen snapshot, ismétlődő lap, nem egyező lapszám, nem a játékos lépése) |
| 404 | ismeretlen végpont |
| 413 | érvénytelen vagy 64 KB-nál nagyobb kérés |
| 429 | `/api/review`: egy elemzés már fut |
| 500 | motorhiba |

| Végpont | Kérés | Válasz |
|---|---|---|
| `GET /api/health` | - | `{"ok", "engine", "version", "endpoints", "mc_samples", "llm"}` |
| `POST /api/choose` | kijátszási snapshot (lásd fent) | `{"id", "card", "why"}` |
| `POST /api/bid` | licit-snapshot (`p`, `dealer`, `hand`, `sizes`, `talon`, `talon_n`, `bid`, `passes`, `blog`) | `{"type": "bid\|pickup\|pass\|concede", "ci", "suit", "disc"}` |
| `POST /api/kontra` | licit-snapshot + `kmult` | `{"ks": [komponens]}` |
| `POST /api/review` | a leosztás naplója (`dealer`, `hands`, `blog`, `klog`, `plays`), `you`, `polish` | `{"summary", "findings", "text", "polished", "polished_findings"?}` |

**LLM.** A tanító bot LLM nélkül teljes értékű (sablonos magyar szöveg, könyvi idézetek). A `"polish": true` a `tutor.polish`-t hívja: watsonx csak átfogalmazza a kiszámolt szöveget, és ha bármely számot vagy lapnevet megváltoztat, vagy a token hiányzik (`WATSONX_TOKEN` vagy `ibmcloud` bejelentkezés), a sablon marad. A `/api/health` `llm` mezője csak azt jelzi, hogy van-e token-forrás, nem hogy a hívás sikerülne. Az LLM nem lát API-kulcsot a válaszban, és nem módosíthat tényt.

**Beállítások (környezeti változók).** `ULTI_ENGINE` (`mc` alapértelmezett | `legacy` | `heuristic`: ezek a régi motor mintaszámát/tartalékát állítják), `ULTI_MC_SAMPLES` (32), `ULTI_KONTRA` (alapértelmezés: várható-pénz szabály, `ulti/ai/kontra_rule.py`; `threshold` a régi küszöbös), `ULTI_MC_BUDGET` (1,5 s/lépés), `ULTI_WORKERS` (8, az elemzéshez), `ULTI_PORT`, `ULTI_VERBOSE`.

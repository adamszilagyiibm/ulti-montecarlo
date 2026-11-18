# Hibrid Rablóulti Szimulátor és Oktató Rendszer

Determinisztikus szabálymotor + tanuló politika (licit, talon, rablás, kontra) + Monte Carlo kijátszás + tanító bot, a barát böngészős UI-jához (`ui/ulti.html`) kötve. A célfüggvény a **pénz**, nem a nyerési arány.

**Hol kezdd:**
- [`docs/ATTEKINTES.md`](docs/ATTEKINTES.md): általános leírás, mit tud, miért jó, mit jelentenek a számok és hogyan mértük őket.
- [`docs/ALLAPOT.md`](docs/ALLAPOT.md): naprakész állapot és mérések.
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md): API-szerződés és a UI bekötése.

## Gyorsindítás

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt            # futtatáshoz elég a numpy; a teljes tesztekhez a többi is kell

python -m ulti.server.make_ui              # ui/ulti_py.html generálása (az eredeti ulti.html érintetlen)
python -m ulti.server.server               # http://127.0.0.1:8765/ulti_py.html
python -m unittest discover -s tests -t .  # 110 teszt, API-kulcs nélkül
```

Böngészőben: `http://127.0.0.1:8765/ulti_py.html`. A `?engine=js` a UI saját botjait használja (összehasonlításhoz). Ha a szerver nem érhető el, a UI az adott döntésnél a saját botjára esik vissza.

## Szerkezet

```
docs/            ATTEKINTES.md, ALLAPOT.md, INTEGRATION.md
ui/              ulti.html (eredeti, érintetlen), ulti_py.html (generált), cards/
tools/           e2e.js, e2e_diff.py, xcheck.js, make_export.py
data/policy/     learned_policy.json, learned_book.json, kontra_rule.json
data/rules/      contracts.json, book_prices.json
data/knowledge/  a Kovács-könyv feldolgozott tudása, heuristics_weights.json
data/research/   a kutatási modulok eredményei
ulti/paths.py    központi útvonalak
ulti/core/       rules, jsbot, playbot, features
ulti/ai/         policy, talon, arena, mcplay, kontra_rule
ulti/coach/      tutor, wx
ulti/server/     server, uibridge, bridge, make_ui
ulti/training/   learn, tune, recal, fit_talon, match, final_eval
ulti/research/   league, book_features, book_policy, book_eval, book_bids
ulti/legacy/     a régi Gemini-s lánc (engine, montecarlo, llm, api, main, refinement_loop)
ulti/bookprep/   a könyv feldolgozó szkriptjei
tests/           test_*.py
```

A modulok egymást lapos névvel importálják (`import rules`, `from policy import ...`): az `ulti/__init__.py` felveszi az alcsomag-mappákat a `sys.path`-ra, ezért minden szkriptet `python -m ulti.<mappa>.<modul>` alakban indíts a csomag gyökeréből. A fejlesztői fán van még `raw/` (a könyv nyers anyaga) és `work/` (futásidejű kimenetek); egyik sincs a csomagban.

## Mit csinál

| Rész | Modul | Szerep |
|---|---|---|
| Szabályok | `ulti/core/rules.py` | A teljes játék valódi szabályokkal és pénzzel. `set_ruleset("friend"/"book")` vagy `ULTI_RULES=book` váltja a díjtáblát (a barát 42 vállalása vs. a Kovács-könyv, utóbbiban van 4 ász bemondás). |
| Politika | `ulti/ai/policy.py`, `talon.py`, `ulti/training/` | Tanult licit/rablás/bedobás/kontra, a célfüggvény a pénz. A talon-modell a licitáló lerakásaiból következtet. |
| Kijátszás | `ulti/ai/mcplay.py` | Determinizált Monte Carlo a `rules.Game`-en; ez az `/api/choose` motorja. |
| Védői kontra | `ulti/ai/kontra_rule.py` | Várható-pénz szabály; a szerver és a tanító bot is ezt használja (`default_policy`). |
| Mérés | `ulti/ai/arena.py`, `ulti/training/match.py` | Páros mérőpad: ugyanaz a leosztás és ülés, csak a politika különbözik. |
| Tanító bot | `ulti/coach/tutor.py` | Leosztás utáni elemzés szimulációval, könyvi idézettel. LLM nélkül is teljes értékű. |
| LLM (opcionális) | `ulti/coach/wx.py` | IBM watsonx.ai: csak átfogalmaz, tényt nem módosíthat. A token az `ibmcloud iam oauth-tokens`-ből jön, fájlba kulcs nem kerül. |
| UI-bekötés | `ulti/server/` | HTTP szerver (stdlib, csak 127.0.0.1), snapshot-konverzió, a `ulti_py.html` generálása. |
| Bot-portok | `ulti/core/jsbot.py`, `playbot.py` | A barát botjának Python másolata: ellenfél és rollout-politika, nem erősségmérő. |

| Végpont | Mit csinál |
|---|---|
| `GET /api/health` | verzió, képességek (`endpoints`, `mc_samples`, `llm`) |
| `POST /api/choose` | a bot lapja: Monte Carlo a `rules.Game`-en; ha a snapshot nem építhető, a régi `engine.py` a tartalék |
| `POST /api/bid` | a bot licitje: bemondás, rablás, passz, bedobás (`learned_policy.json`) |
| `POST /api/kontra` | a bot kontrája / rekontrája (várható-pénz szabály) |
| `POST /api/review` | **tanító bot**: egy lejátszott leosztás elemzése, kb. 10-20 s; `"polish": true` esetén opcionális watsonx-stilizálás |

A licit és a kontra a barát 42 vállalásos táblájával fut (`friend`). A szerver az elemzést egyszerre csak egyszer futtatja (a gép összes magját használja).

## Tanító bot

A leosztás után a motor szimulációval megmondja, mit érdemes másképp csinálni. A tényeket (melyik döntés mennyi pénzbe került) a motor számolja, a magyar szöveg sablonokból áll, és minden megállapításhoz a könyv idézett tanácsa tartozik (oldalszámmal).

Mit vizsgál: licit, talonletétel, rablás/passz/bedobás, kontra/rekontra, lapválasztás. Minden döntésnél ugyanazokon a determinizált világokon végigjátssza a választottat és az alternatívákat, és **páros különbséget** számol. Csak szignifikáns eltérést jelez: az átlagnak legalább `z`=2,0 szórásnyi hibahatárral (lapdöntésnél 3) kell a nulla fölött lennie, és legalább 1 pontosnak kell lennie (lapnál 2, kontránál 5).

```python
import ulti                              # felveszi az alcsomag-mappákat a sys.path-ra
import tutor
rec = tutor.DealRecord.from_game(game)            # vagy tutor.record_from_ui(json) a böngésző naplójából
review = tutor.analyze(rec, you=0)                # you=None: mindhárom játékos
print(review.text())
tutor.polish(review.findings[0])                  # OPCIONÁLIS: watsonx átfogalmazás; számot/lapot nem módosíthat
```

A `polish` ellenőrzi, hogy az átfogalmazott szöveg számai és lapnevei pontosan az eredetiek-e, különben a sablonos szöveg marad.

## Mérés és tanítás

```bash
python -m ulti.training.match learned js 400 16000000      # tanult a JS bot ellen, 400 leosztás, seed 16 000 000
python -m ulti.training.match learned+mc learned 400 14100000
node tools/e2e.js <port> <leosztás> py|js [seed]           # a valódi JS motor ellen, futó szerverrel
E2E_DUMP=/tmp/py.json node tools/e2e.js 8765 240 py 7     # leosztásonkénti pénz mentése
python tools/e2e_diff.py /tmp/py.json /tmp/js.json         # páros különbség ± 95%-os CI
ULTI_RULES=book python -m ulti.training.learn --rounds 6 --deals 8000 --eval 240 --tune 0 --out data/policy/learned_book.json
python -m ulti.research.book_bids                          # a motor licitje a könyv példáin
```

A mért számok (és hogy mit jelentenek és mit nem) a [`docs/ATTEKINTES.md`](docs/ATTEKINTES.md) 4. fejezetében vannak. Röviden: a tanult politika a JS bot ellen +2,03 ± 0,39 pénz/ülés (7500 ülés); a Monte Carlo kijátszás a `playbot`-hoz képest 32 mintánál +4,06 ± 1,34 (telítődik); a valódi JS motor ellen, csak a kijátszást cserélve +0,87 ± 0,45 (240 leosztás). **Emberek ellen nem mértünk.**

## Kutatási modulok (nem kellenek a futtatáshoz)

| Modul | Mit csinál | Eredmény |
|---|---|---|
| `ulti/ai/kontra_rule.py` | védői kontra várható-pénz szabállyal; **a szerver használja** | +0,25 ± 0,11 pénz/ülés |
| `ulti/research/league.py` | liga-tanítás régi generációk és a JS bot ellen | nincs javulás |
| `ulti/research/book_features.py`, `book_policy.py` | 17 könyvi tanács jellemzőként | nincs javulás |
| `ulti/research/book_eval.py` | a motor és a könyv eltérő bemondásainak szimulációs összevetése | döntetlen; a talonletétel a könyvé |

## Régi lánc (`ulti/legacy/`)

Az eredeti, Gemini-s heurisztikus lánc (`engine.py`, `montecarlo.py`, `llm.py`, `api.py`, `main.py`, `refinement_loop.py`). A szerver csak tartaléknak használja az `engine.py`-t. Futtatásához a `requirements.txt` opcionális csomagjai és `GOOGLE_API_KEY` kell (környezeti változóként, **soha fájlban**). A `refinement_loop` a futtatáskor felülírja a `data/knowledge/heuristics_weights.json`-t, előtte készíts mentést.

## Ismert korlátok

- A mérések a JS bot és saját régi változataink ellen készültek, embereken nem.
- Böngészős kipróbálás nem volt, csak jsdom.
- A UI a 42 vállalásos díjtáblával fut; a Kovács-táblával a UI-nak is át kell állnia.
- A tanító bot tanácsait embereken nem próbáltuk ki.
- A védői kontra nyeresége kicsi (+0,255) és szinte csak a betliből jön.

A teljes lista: [`docs/ATTEKINTES.md`](docs/ATTEKINTES.md) 5. fejezete.

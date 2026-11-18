# Állapot: mit tud a Python motor, és mi vár még ránk

## Mit tudunk most

**Teljes játék Pythonban (`rules.py`).** Licit, talon, rablás, bedobás, kontra/rekontra, kijátszás, pontozás és csendes tételek. A `Game` osztály a pénzt számolja, nem a nyerési arányt. Két díjtáblával fut:

- `friend`: a te UI-d táblázata (`contracts.json`, 42 vállalás).
- `book`: a Kovács-könyv 85–88. oldali táblázata (78 vállalás, benne a **4 ász** bemondás). Váltás: `rules.set_ruleset("book")` vagy `ULTI_RULES=book`.

**A szabályokat a te JS-motorodhoz mértük (`xcheck.js`, `test_rules.py`).** 400 véletlen játszmában az eredmény (pénz és sorok) azonos. A teszt érzékeny: két szándékosan elrontott szabályt elkapott.

**A te botod portjai** (`playbot.py`, `jsbot.py`): a kijátszás 9564 lépésen, a licit 323 licit- és 300 „own” ellenőrzésnél (300 játszmából), a kontra 631 döntésnél azonos. Ellenfélként és mérőpadként kellenek. **Nem mérce:** a minta nem biztos, hogy jól játszik.

**Tanult politika (`policy.py`, `learn.py`, `tune.py`, `talon.py`).** 24 jellemzőből tanul önjátékkal, a licitet, a talont, a rablást és a kontrát is a várható pénzre optimalizálja. A talon-modell azt becsüli, mit tehetett le a licitáló, és ebből következtet a talonra rabláskor.

**Monte Carlo (`mcplay.py`).** Determinizált, a pénz a célfüggvény, a védő nem lát a talonba. Az `/api/choose` mögött fut (32 minta, `eps` 0, időkerettel): `uibridge.game_from_play_snapshot` építi a `rules.Game`-et a UI állapotából.

**Tanító bot (`tutor.py`).** Egy lejátszott leosztás licit-, talon-, rablás-, kontra- és lapdöntéseit szimulációval értékeli (páros, közös véletlen), sablonos magyar szöveggel és a könyv idézett tanácsaival. LLM nélkül működik; a `polish` opcionális, számellenőrzéssel. A védő-oldali világokat a felvevő bemondásához igazítja (feltételes mintavétel), mert az egyenletes újraosztás irreálisan gyenge felvevői kezet adott.

**UI-bekötés.** `/api/choose`, `/api/bid`, `/api/kontra`, `/api/review` (lásd `docs/INTEGRATION.md`).

## Mérések (pénz/ülés, páros mérés, 95%-os intervallum fele)

| Mérés | Eredmény |
|---|---|
| Tanult (barát-szabályok) a JS ellen, 1200 ülés | **+2,09 ± 1,17** |
| Tanult (könyv-szabályok) a JS ellen, 1200 ülés | **+3,78 ± 1,37** |
| Tanult (könyv-szabályok) a JS ellen, másik 1500 ülés | **+3,85 ± 1,33** |
| Tanult (könyv) a JS+Monte Carlo ellen, 360 ülés | +2,15 ± 2,52 (kicsi minta) |
| Tanult (könyv) önmaga ellen | 0,00 (várt, azonos politika) |
| Hangolt a hangolatlan ellen, 1500 ülés | +0,59 ± 0,74 (**nem** szignifikáns) |

Amit ebből **nem** szabad kiolvasni:

- A tanult a JS-botot veri. Hogy a licit abszolút értelemben jó-e, azt nem mutatja.
- A tanult felvevő aránya 0,74–0,85 (a JS-é 0,33). Ez gyanúsan sok, mert a motor szinte mindig felvesz, és a pénzt a felvevőként nyert tételekből hozza. Egy emberi játékos ellen ez nem biztos, hogy így marad.
- A bedobás aránya 0,000. A motor **nem tanult meg bedobni**.
- A kontra csak a küszöbökön (`kt`, `rt`) múlik, külön modellje nincs.
- A mérés nem ismeri a tényleges emberi ellenfelet.

## A Kovács-könyv feldolgozása

A könyv a „biblia”. Két dolgot nyertünk ki belőle: **mire mit mondanak be** (licit-lépések) és **mi a lefutás** (a 10 ütés és az eredmény).

**Díjtáblázat (85–88. oldal).** 27 sor, 55 figura, mindegyik értéke kijön az összegből (`check_book_prices.py`). Eltérés a te táblázatodtól:

| Komponens | Könyv | A te UI-d |
|---|---|---|
| 20-100 | 6 | 8 |
| durchmars | 7 | 6 |
| terített durchmars | 14 | 12 |
| 4 ász | 4 (piros 8) | nincs |

A könyv szerint a 20-100-nál és a durchmarsnál a szerző szándékosan tért el az egységes számítástól. Ha a te UI-d táblázata helyi szokás, az rendben van. Ezért van két szabályrendszer.

**Példatár (137–209. oldal, 30 példa).**

- **Diagramok:** 29/30 példa kiolvasva képről (IBM watsonx.ai, Llama 4 Maverick, több futás szavazással). Mindegyiknél a 3×10 lap + a talon utolsó sora pontosan a 32 lapos pakli. **A 23. példa diagramja a könyvben hibás** (a tök alsó kétszer szerepel, a tök 8-as sehol), ezért kizártuk.
- **Visszajátszás a `rules.Game`-en a könyv díjaival:** 29 példából **13 teljes lejátszás, és mind a 13 pontosan egyezik a könyv pontszámával** (kontrával, csendes tételekkel együtt). 4 részleges (az ütéstábla OCR-je hiányos), 5 csak a licitig jut, 6 hibás (a licit-lépések kinyerése vagy az OCR-olvasat).
- **Egy kézi javítás van:** a 22. példánál a szöveg csak az ultimó kontrázását írja, de a könyv eredménye (24 + 8) a játék kontráját is feltételezi. Ezt `facts.json`-ban `manual_fix` jelöli. Két kézi szövegjavítás van még (14. és 30. példa végeredmény-sora, az OCR szétzúzta), a `extract_facts.TEXT_FIX`-ben.
- **A visszajátszás nem bizonyítja a motor jóságát**, csak a szabályok és a pontozás helyességét a könyv saját példáin.

**Szabályok és tanácsok (79–132., 210–223. oldal)** — `book_knowledge.json`: 341 egyedi tétel, mindegyik szó szerinti, az OCR-ben megtalált idézettel. 3 különböző prompt futott, tételenként szavaz: **67 tételt mind a 3, 87-et 2, 187-et csak 1 futás talált meg**. Az 1 szavazatú tétel nem hibás (az idézet ellenőrzött), csak kevésbé biztos, hogy fontos. A renonsz-szabályok (120–131. o.) is köztük vannak (53 tétel), de **a motor még nem használja őket**.

**A motor licitje a könyv példáin (`book_bids.py`).** 59 licit-lépés 25 példából:

- pontos egyezés (vállalás + adu): **44%**, ugyanaz az adu: 64%, ugyanazok a figurák: 46%, ugyanaz a talonletétel: 34%.
- Ahol eltér, ott a motor gyakran **terített betlit** választ (a saját pénz-becslése szerint jobb). Ezt a felvevőként játszott játszmákban is látjuk: terített betli 34 felvételből 2,35 átlag pénzzel, a sima betli 10 felvételből −6,50-nel.
- Ez nem azt jelenti, hogy a könyv téved. A könyv bemondásait a motor saját értékbecslésével hasonlítottuk, a motor becslése pedig önjátékból jön. **Ahol a motor és a könyv eltér, ott kell megnézni, melyik jár jobban**, és ez még nem történt meg.

## Mit nem tudunk, mire figyelj

- **A +3,8 csak a JS-botod ellen mért**, és a könyv díjaival. A te UI-d díjaival +2,03 ± 0,39 (friss 7500 ülés, `pickup_thr` 1,0).
- **A könyvből a példák 45%-a jut el teljes lejátszásig.** A többinél az OCR vagy a licit-kinyerés hibázik.
- **A tanácsok még nem váltak jellemzővé** (`features.py`). Ebben a körben csak kinyertük és idézettel ellenőriztük őket. A motor nem tanul belőlük közvetlenül.
- **A motor a könyv licitjeiből nem tanul.** Az összevetés megvan (`book_bids.py`), de a tanítás önjátékból megy.
- **A böngészős kipróbálás nem történt meg.** A generált UI-t jsdom-ban futtattuk egy valódi szerver ellen (licit, kontra, kijátszás, elemzés, hiba nélkül), a megjelenést nem láttuk.
- **A tanító bot becslés, nem tény.** A másik két játékos lapját csak becsülni tudja. A feltételes mintavétel javított rajta (a védői kontra szimulált értéke +16-ról +5 körülire esett, a ténylegeshez közelebb), de nem tökéletes: ezért a kontra-tanácsot csak ≥5 pont becsült különbségnél adja. A tanácsok ellenfél-modellje a saját motorunk, nem egy ember.
- **A tanító bot tanácsai nincsenek emberekkel kipróbálva.** Hogy tényleg jobb játékot tanítanak-e, azt nem mértük.
- **A védői kontra-modell újratanítása (`recal.py`) a kalibrációt javította, a pénzt nem.** A hiba oka: a `learn.py` védői sorai olyan leosztásokból jöttek, ahol csak az első kéz licitált véletlen vállalással; a valódi licit (rablás, jó vállalás) után a felvevő sokkal gyakrabban teljesít. A `recal.py` a teljes licitmenetű játékból tanít, a tanításban nem látott leosztásokon a Brier-hiba 0,209 → 0,163. Pénzben viszont nem lett jobb: a JS ellen +1,91 ± 0,60 a régi +1,97 ± 0,59 helyett (4500 ülés), a régi önmaga ellen −0,98 ± 0,49 (4500 ülés). A `kt` küszöb hangolása sem segített. A mentett `learned_policy.json` ezért nem az újratanított; a `recal.py` megmaradt az eszközkészletben.
- **Az on-policy újratanítás (`learn.py --init`) sem javított**: −0,72 ± 1,11 a régi ellen (700 páros leosztás).
- **A felvevő arány magas, de nem hiba, hanem a motor stratégiája.** (Korábban 0,74–0,85, a `pickup_thr` 1,0 mellett 0,62; a JS botnál 0,333.) A vállalásonkénti bontás: a motor jövedelmezően mond ultit, piros ultit, 40-100 ultit és terített betlit. Veszteséges a sima betli (−4,4/felvétel) és a piros betli (−19), de ezek tiltása nem javít (sima betli tiltva: −0,13, piros betli tiltva: −0,05, mindkettő tiltva: −0,26 a JS elleni eredményen). A rablás küszöbének emelése viszont számít: `pickup_thr` 1,0 mellett a felvevő arány 0,23–0,60 körüli az ellenfeleknél mért értékekhez képest csökken, és a közvetlen összevetés a −1,0-ás küszöbhöz képest +1,09 ± 0,63 és +1,33 ± 0,65 (két független, 2700 és 3000 ülésnyi minta). A `learned_policy.json`-ban ezért `pickup_thr = 1.0` van. Friss leosztásokon (7500 ülés): +2,03 ± 0,39 a JS ellen, felvevő arány 0,62.
- **Monte Carlo ellen** (`js+mc`, 8 minta): a tanult politika kijátszással együtt (`learned+mc`) +2,04 ± 1,09 (1350 ülés); a tanult licit a JS+MC kijátszás ellen önmagában +0,10 ± 0,61 (4500 ülés), vagyis a licit előnye ott eltűnik, ahol az ellenfél kijátszása erős. Az előny tehát részben a jobb kijátszásból (Monte Carlo) jön, nem csak a licitből.

- **Monte Carlo finomítása** (`learned+mc` vs `learned`, 400 leosztás, 1200 ülés): 4 minta +1,55 ± 1,38; 8: +2,91 ± 1,46; 16: +3,31 ± 1,47; 32: +4,06 ± 1,34 (másik seeden +4,32 ± 1,47); 64: +4,44 ± 1,50. Az előny 32 körül telítődik, a szomszédos mintaszámok különbsége a CI-n belül van. `eps` 0 / 0,1 / 0,2 / 0,5 / 1,0: +3,31 / +3,29 / +2,78 / +2,76 / +1,88 (16 minta). Véletlen rollout-politikával +2,16 a +3,31 helyett. Egy lépés 32 mintával 3-42 ms átlag, legrosszabb esetben ~110 ms. Következtetés: további finomítás (több minta, `eps`) nem hoz mérhetőt; a maradék javulás a rollout-politika minőségétől (a `playbot` gyengébb, mint a motor saját tanult politikája) várható.
- **Valódi JS-motor ellen, a UI tényleges hívásaival** (`e2e.js`, 240 leosztás, seed 7): Python motor (új MC `/api/choose`) − JS bot = +0,87 ± 0,45 pont/ülés; régi `engine.py` motor: −0,90 ± 0,86. 0 szabálytalan lap, 0 HTTP-hiba.

- **Védői kontra, várható-pénz szabály** (`kontra_rule.py`, `ExpectedMoneyKontra`): a küszöbös szabályhoz képest **+0,255 ± 0,109 pénz/ülés** (21 000 ülés, új seed), szinte teljesen a betli kontrájából (+0,225 ± 0,143, 12 000 ülés). Feltétele a recal.py-val kalibrált védői modell (kalibráció nélkül +0,02 ± 0,02), és az, hogy a modell a felvevő rekontráját is beárazza (az egyszerű, rekontra nélküli képlet −0,22 és −1,10 közötti). A felvevői rekontra javítása nem hozott semmit (+0,014 ± 0,029); a szimulációs döntés (10 világ, ~0,7 s/döntés) nem jobb a képletnél. A szerver ezt használja (`ULTI_KONTRA=threshold` a régire kapcsol). **Szerény nyereség**, a 3000 ülésnél kisebb mintán nem látszik.
- **Liga-tanítás** (`league.py`, JS bot + régi generációk az ellenfél-ülésen): **nincs mérhető javulás.** A teljes újratanítás rontott (−2,1 és −3,8 a bajnok ellen), az erős regularizáció és a keverés (`lam` 30, `blend` 0,5) a bajnok közelében marad: +0,07 ± 0,73 a bajnok ellen, +1,65 ± 1,03 vs +1,31 ± 1,11 a JS ellen. A korlát a logisztikus modell kapacitása, nem az ellenfél-választás. A `learned_policy.json` marad; a `learned_league.json` csak a legjobb kísérletet dokumentálja.
- **A könyv tanácsai jellemzőként** (`book_features.py`, `book_policy.py`; 17 jellemző, mindegyikhez szó szerinti idézet): **nincs mérhető javulás.** A JS ellen három mintán összevonva −0,03 ± 0,39 az alap 24 jellemzőhöz képest; a teljesülés-előrejelzés log-lossa nem javult (legfeljebb 0,009). A 17 jellemző előjele 9-nél egyezik a könyvből várttal, 8-nál ellentmond (az ulti jellemzőknél valószínűleg kollinearitás miatt). A könyv haszna a tanító botban van (idézetek), nem a motor súlyaiban. ±0,4 alatti hatást ez a mérés nem tud kimutatni.
- **A motor és a könyv eltérő bemondásai** (`book_eval.py`, a könyv díjaival, 48 eset × 250 világ): 11 esetben a motor, 15-ben a könyv jobb szignifikánsan, 22 nem eldönthető. A vállalás-/adu-választásban döntetlen (átlag −0,03), a **talonletételben a könyv átlagosan 3 ponttal jobb** (16 eset, 5 szignifikánsan a könyv, 1 a motor), ezt érdemes a könyv felé húzni (újratanítással). A motor túl sokat mond betlire az ulti helyett, és a 4 ász változatoknál a könyv Piros partija jobb. **Óvatosan kezelendő**: a szimuláció mindhárom ülésen ugyanazt a politikát játszatja (a motornak jól játszható játékoknak, betli, ulti kedvez), a másik két kéz a licit-előzménytől függetlenül véletlen, és a legnagyobb eltérések (±55-63 pont, pl. 16.1, 11.1, 2.1) a bot kijátszási gyengeségét mutatják 20-100-ban, nem a bemondás értékét.

## Mi vár még ránk

1. **A talonletétel** a könyv felé húzása (tanult súlyok újratanítása a `book_eval.py` eredményei alapján; a könyv itt mérhetően jobb).
2. **A kijátszás gyengesége 20-100-ban** (a `playbot` 70 ponttal megáll): ez torzítja a licit-értékelést és a Monte Carlo rollout-ját is. A mérés szerint a rollout-politika minősége az előny fő korlátja.
3. **Bedobás és felvevői rekontra tanulása** külön modellel (a rekontra képletes javítása nem hozott semmit).
4. **Mérés emberszerűbb ellenfél ellen.** Az eddigi mérések a JS bot és saját régi változataink ellen készültek, embereken nem próbáltuk.
5. A hiányzó példák (6 hibás, 5 licit-csak, 4 részleges) kézi átnézése.

## Gyors indítás

```
python -W ignore -m unittest discover -s tests -t .             # tesztek (110)
python -m ulti.server.make_ui && python -m ulti.server.server   # UI + tanító bot, http://127.0.0.1:8765/ulti_py.html
ULTI_RULES=book python -W ignore -m ulti.training.match learned:data/policy/learned_book.json js 500 8000000
python -W ignore -m ulti.research.book_bids                     # a motor licitje a könyv példáin
python -W ignore -m ulti.bookprep.build_examples --only 1,2     # példák visszajátszása (watsonx kell: ibmcloud login; a raw/ is kell)
node tools/e2e.js <port> 240 py 7                               # a valódi JS motor ellen, futó szerverrel
```

A fájlok helye: lásd a [`README.md`](../README.md) „Szerkezet” részét. A modulnevek (`rules.py`, `policy.py` stb.) ebben a dokumentumban a megfelelő `ulti/<mappa>/` alatt vannak.

Kulcs fájlba nem kerül: az IBM-hozzáférés az `ibmcloud` bejelentkezésből jön (`wx.py`).

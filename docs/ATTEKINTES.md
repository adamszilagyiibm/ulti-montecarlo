# Rablóulti motor: áttekintés

Ez a dokumentum általános leírás: mit tud a rendszer, hogyan hoz döntéseket, mit jelentenek a számok, és hogyan jutottunk hozzájuk. Nem kell hozzá statisztikai vagy gépi tanulási háttér, a szószedet a végén van.

Részletesebb, naprakész állapot: [`ALLAPOT.md`](ALLAPOT.md). Az API szerződése: [`INTEGRATION.md`](INTEGRATION.md).

## 1. Mi ez egy bekezdésben

Ez egy Python motor a Rablóulti UI-hoz (`ui/ulti.html`). Licitál, kontráz és kijátssza a lapokat, vagyis a UI három gépi játékosának a döntéseit tudja átvenni. A UI-hoz HTTP API-n kapcsolódik, és ha a szerver nem érhető el, a UI a saját JS botjára vált vissza. Van hozzá egy **tanító bot** is: egy lejátszott leosztás után megmondja, melyik döntésnél mennyi pontot veszítettél, mi lett volna jobb, miért, és a könyvből (Kovács Endre: Ultikódex) idéz hozzá. A tanító bot **LLM nélkül is működik**: a tényeket a motor szimulációval számolja, a magyar szöveg sablonokból áll. Az LLM (IBM watsonx.ai) csak opcionális átfogalmazásra van, és tényt, számot, lapnevet nem módosíthat.

## 2. Mit tud

| Képesség | Modul | Mire jó |
|---|---|---|
| Teljes szabálymotor, két díjtáblával | `ulti/core/rules.py` | Licit, talon, rablás, bedobás, kontra/rekontra, kijátszás, pontozás. A `friend` táblát (a te 42 vállalásod) és a `book` táblát (Kovács-könyv, 78 vállalás, van 4 ász) is ismeri. |
| A te botod portja: licit, kontra | `ulti/core/jsbot.py` | A te JS licitjének és kontrájának Python másolata. Ez a mérőpad ellenfele. |
| A te botod portja: kijátszás | `ulti/core/playbot.py` | A te `botChoose` függvényed másolata. Ez a Monte Carlo szimulációk "alap játékosa" is. |
| Kézjellemzők | `ulti/core/features.py` | A kézből 24 számot számol (adu hossza, ászok, színhiány stb.), ezekre épül a tanult licit. |
| Tanult licit, talon, rablás, kontra | `ulti/ai/policy.py` | Megmondja, mit érdemes licitálni, mit tegyünk le, érdemes-e rabolni, kontrázni. |
| Talon-modell | `ulti/ai/talon.py` | Becsli, mit tehetett le a licitáló, és ebből mi lehet a kezében. |
| Monte Carlo kijátszás | `ulti/ai/mcplay.py` | Lépésenként szimulálja a lehetséges lapokat, és a legtöbb pénzt hozót választja. |
| Védői kontra szabály | `ulti/ai/kontra_rule.py` | A védő akkor kontráz, ha a várható pénz pozitív. A szerver alapból ezt használja. |
| Mérőpad | `ulti/ai/arena.py` | Két politikát páros méréssel hasonlít össze. |
| Tanító bot | `ulti/coach/tutor.py` | Leosztás utáni elemzés, szimulációval mért pontveszteséggel és könyvi idézettel. |
| Opcionális LLM-réteg | `ulti/coach/wx.py` | watsonx.ai kliens a szöveg átfogalmazásához. |
| HTTP API | `ulti/server/server.py` | `/api/health`, `/api/choose`, `/api/bid`, `/api/kontra`, `/api/review`. Csak 127.0.0.1-en figyel. |
| UI-bekötés | `ulti/server/make_ui.py` | Legenerálja az `ui/ulti_py.html`-t. Az eredeti `ui/ulti.html`-hez **nem nyúl**. |
| Tanulás és hangolás | `ulti/training/` | Önjáték-adatgyűjtés, illesztés, mérés. Csak akkor kell, ha újra akarod tanítani. |

## 3. Hogyan döntenek a részek

### 3.1 A licit és a kontra (tanult rész)

**Mit lát a modell?** A kezet 24 számmá alakítjuk (`features.py`): a bias, az adu hossza és négyzete, van-e adu ász / X / király / felső / VII, az adu sorozat hossza, a három legerősebb adu, a mellékszínű ászok és fedett vagy csupasz tízesek, színhiányok, szinglik, hosszú mellékszín, van-e 40, mellékszíni 20-ak száma és pontja, magas és alacsony lapok, a betli-kockázat, a durchmars-veszteség és a talonba tehető pontok. Mellé 8 környezeti szám jön (melyik vállalásról van szó, piros-e, van-e benne 4 ász stb.). Összesen 32 bemenet.

**Mit tanul?** Vállalás-komponensenként (parti, 40-100, 20-100, ulti, betli, durchmars, színnélküli durchmars, 4 ász) egy-egy **logisztikus modellt**. Ez egy egyszerű képlet, amely a 32 számból 0 és 1 közötti **teljesülési esélyt** ad. Mindegyikből két változat van: felvevői és védői nézet. A "csendes" tételekre (csendes ulti, csendes 100) külön, korlátozott modell van.

**Honnan tanul?** Önjátékból (`learn.py`): a gép véletlen és egyre jobb döntésekkel sok leosztást lejátszik, a kijátszást a `playbot` adja, és a tanulási címke az, hogy a komponens ténylegesen teljesült-e. Az illesztés ridge-regularizált (λ=3), Newton-módszerrel, 25 iterációval.

**Mi a célfüggvény? A pénz, nem a nyerési arány.** A bukott ulti kétszeres díj, a kontra duplázza vagy négyszerezi a tétet, a bedobásnak ára van. Ezért a döntés a **várható pénz**:

```
várható pénz = Σ (teljesülési esély × díj) − (bukási esély × veszteség)
```

A bukott ulti vesztesége a díj kétszerese. Ha az adu VII nincs meg, az ulti értéke gyakorlatilag kizárt.

**Rablás.** A modell mintát vesz a valószínű talonból (`n_talon`=8), kiszámolja a legjobb licitet a felvett 12 lapon (a licit-lépcső feljebb ugrik), és ezt összeveti azzal, amit védőként maradva várhatna. A rablás után licitálni kötelező, ezért a rossz két lap kockázatát is beleszámolja. A `pickup_thr` küszöb 1,0, vagyis rabolni csak akkor érdemes, ha a várható haszon legalább ennyi.

**Bedobás.** Akkor dob be, ha a jelenlegi várható érték rosszabb, mint a bedobás díja.

**Kontra.** A szerver alapból a `kontra_rule.py` szabályát használja: a védő a várható pénz alapján kontráz, a védői modell (a `recal.py` által újrakalibrált változat) esélyéből. A régi küszöbös szabály (`kt`=0,2, `rt`=0,65) a `ULTI_KONTRA=threshold` környezeti változóval kapcsolható vissza.

### 3.2 A Monte Carlo kijátszás

A kijátszásnál a motor nem lát bele a többiek lapjába, ezért **szimulál**:

1. **Determinizálás.** Minden mintánál lemásolja a játékot, és a számára ismeretlen lapokat véletlenül szétosztja a két másik játékos között. Közben betartja, amit már tudunk: ha valaki egy színre nem adott, abból a színből nem kaphat lapot (a "tudott renonsz"). A felvevő ismeri a talont, a védő nem, tehát a védői szimulációban a talon is ismeretlen.
2. **Közös világok.** Minden szabályos jelölt lapot **ugyanazon a mintán** játszat végig. Így a különbség a lapok közt nem a leosztás szerencséjéből jön.
3. **Rollout.** A szimulált játékot a hátralevő ütésekre a `playbot` (a te kijátszásod portja) viszi végig, véletlen lépések nélkül (`eps`=0).
4. **Döntés.** Az a lap nyer, amelyik a minták átlagában a legtöbb pénzt hozza (a tényleges pontozás szerint, kontrával, csendes tételekkel).

A szerver **32 mintát** használ, legalább 4-et, és lépésenként 1,5 s időkeretet. Ha a játékállás nem építhető fel, a régi `engine.py` motor a tartalék. Ha az egész szerver nem érhető el, a UI a saját botját használja.

### 3.3 A tanító bot

A tanító bot a lejátszott leosztás naplójából (`DealRecord`) újrajátssza a leosztást, és minden döntésnél (licit, talonletétel, rablás/passz/bedobás, kontra, lapválasztás) ezt teszi:

1. A választott döntést és a jelölt alternatívákat **ugyanazon a szimulált világ-halmazon** játssza végig (licitnél 32, lapnál 60 világ).
2. Kiszámolja a **páros különbséget**: alternatíva mínusz választott, világonként, és ennek átlagát és szórását.
3. **Csak szignifikáns eltérést jelez.** Legalább 1 pont különbség kell, és az átlagnak legalább 2 szórásnyi hibahatárral nagyobbnak kell lennie a nullánál. A lapdöntésnél ennél szigorúbb a küszöb (3 szórás, legalább 2 pont), a kontránál legalább 5 pont. Egy elemzés legfeljebb 6 megállapítást ad.
4. **Feltételes mintavétel a védő nézetéből.** A védői világokban a felvevő keze a bemondásához igazodik. Anélkül egy terített betlire szinte mindig azt mondta volna, hogy "kontráznod kellett volna, +15–19 pont", mert a világokban a felvevő véletlen, gyenge lapot kapott.
5. Minden megállapításhoz tartozik: mit tettél, mi lett volna jobb, a becsült pontveszteség, az ok (a motor számaiból, például a teljesülési esély vagy a letett adu) és a könyv idézett tanácsa oldalszámmal (`kodex_advice.json` 145 tétel, `book_knowledge.json` 341 tétel).

Az **LLM-réteg** (`polish`) csak átfogalmaz. A `faithful` ellenőrzés összeveti a számokat és lapneveket az eredetivel, és eltérésnél a sablonos szöveg marad. Token nélkül is az marad.

## 4. A számok: mit jelentenek és hogyan mértük

### 4.1 A mérés módszere

**Pénz / pont.** A játék elszámolási egysége, a `data/rules/contracts.json` (a te 42 vállalásos díjtáblád) szerint. A parti alapdíja 1 egység. Néhány példa:

| Vállalás | Egység |
|---|---|
| Parti (passz) | 1 |
| Piros parti | 2 |
| 40-100 | 4 |
| Ulti | 5 (a parti 1 + az ulti 4) |
| Betli | 5 |
| Durchmars | 6 |
| 20-100 | 8 |
| Piros betli | 10 |
| Terített betli | 20 |
| Piros 20-100 ulti terített durchmars (a legdrágább) | 48 |

A piros szín az összes komponenst kétszerezi. A számolás (`rules.Game.finish_hand`) komponensenként megy: a teljesült +díj, a bukott −díj, a bukott ulti −2×díj, ezt **minden védővel külön**. A kontra szorzója komponensenként érvényes. Léteznek csendes tételek is (csendes ulti, csendes 100, csendes 4 ász, csendes durchmars). Az eredmény **nulla összegű**: amit a felvevő nyer, azt a két védő veszti, és fordítva.

**Ülés.** Egy leosztás egy ülésen. Mivel három ülés van, **egy leosztásból 3 ülés** lesz (`jobs = leosztás × 3`). Az "1500 ülés" tehát 500 leosztás, mindhárom ülésről nézve.

**Páros mérés.** Ha két politikát hasonlítunk, nem két külön játékot mérünk, hanem ugyanazt a leosztást kétszer játsszuk le (`arena._one`):

1. Először mindhárom ülésen az **alap** politika ül (alapesetben a JS bot).
2. Aztán ugyanaz a leosztás, ugyanaz az ülés, ugyanaz az osztó (`deal_seed % 3`) és ugyanaz a döntési véletlen (`Random(deal_seed*7919+13)`), de az adott ülésen a **vizsgált** politika ül.
3. A mért szám a vizsgált ülés pénze a másodikban **mínusz** az elsőben.

Így a kártyaszerencse nagyrészt kiesik, és csak a politika különbsége marad.

**A „± érték”.** Az `arena.Stat` az ülésenkénti páros különbségek átlagát adja, a ± pedig **1,96 × átlag standard hibája**, ahol a standard hiba = szórás / √n (n−1-es szórással). Ez a **95%-os konfidenciaintervallum fele**.

> Példa: **+2,03 ± 0,39** azt jelenti, hogy a vizsgált politika átlagosan **2,03 egységgel többet nyer ülésenként**, és kb. 95% valószínűséggel a valódi átlag **1,64 és 2,42 között** van. Mivel a 0 kívül esik, a javulás nem a véletlen műve.
>
> Ha az eredmény **+0,10 ± 0,61**, akkor az intervallum −0,51 és +0,71 között van, tehát **nem tudjuk**, hogy van-e egyáltalán előny.

**Fontos korlát.** A három ülés ugyanabból a leosztásból jön, ezért nem 3 független adat. A valódi bizonytalanság valamivel nagyobb, mint a kiírt ±.

**Seed és friss leosztás.** A leosztásokat egy számból (seed) generáljuk, így a mérés megismételhető. A tanítás és a hangolás **más seed-tartományból** dolgozik, mint a mérés, különben a motor a saját tanítóadatán mérné magát, és túl szépnek látszana. Tartományok: tanítás `learn.TRAIN_SEED0` = 100 000, hangolás 500 000, teszt 900 000. A kutatási kísérletek saját, magasabb tartományokat használnak (pl. liga: tanítás 20 000 000, mérés 30 000 000+).

**A JS bot mint mérce.** A „JS bot” a te botod Python portja (`jsbot.py`, `playbot.py`). Ez **gyenge ellenfél, nem erősségmérő**: nem ember, és a mintája nem biztos, hogy jól játszik. Ha a motor veri, az csak azt mutatja, hogy a te botodnál jobb. A felvevő arány mérésénél ez látszik: három azonos játékosnál a felvevő aránya szimmetriából 1/3 (a kód 0,333-at ad).

### 4.2 A fő számok

Az „ülés” a fenti definíció szerint értendő. A ± mindenhol 95%-os CI fele, ahol nem jelzem másképp.

| Szám | Mit mér | Hogyan (modul, parancs, ülésszám, seed) | Mit jelent | Mit NEM jelent |
|---|---|---|---|---|
| **+2,03 ± 0,39** | A tanult politika (licit, talon, rablás, kontra, kijátszás a `playbot`-tal) a JS bot ellen, a te szabályaiddal | `final_eval.py` / `match.py`, páros, 7500 ülés, friss leosztások (a pontos seed nincs rögzítve). Politika: `learned_policy.json`, `pickup_thr` 1,0 | Átlag +2 egység ülésenként a JS bothoz képest | Nem „jó játékos általában”. Nem ember elleni szám. |
| **+3,8** (+3,78 ± 1,37, 1200 ülés; +3,85 ± 1,33, 1500 ülés) | Ugyanez a Kovács-könyv díjaival (`ULTI_RULES=book`, `learned_book.json`) | `match.py`, páros | A könyv táblája szerint nagyobb az előny | Nem összevethető a +2,03-mal: más a díjtábla (20-100: 6 vs. 8, durchmars: 7 vs. 6, terített durchmars: 14 vs. 12, van 4 ász) és más a politika. A UI a te táblád szerint fut. |
| **0,62** felvevő arány | A tanult motor a leosztások hány százalékában lesz felvevő | Ugyanaz a mérés mint a +2,03. A JS-é 0,333 | A motor gyakrabban vállal, mint a JS bot | Nem „jobb” önmagában. Ha a JS bot licitje túl óvatos, a sok vállalás neki kedvez, ellenfél ellen kisebb lehet. Gyanús lehet, mert a rablás sokat vállal. A régi mérések 0,74–0,85 voltak, a `pickup_thr` 1,0 csökkentette. |
| **Monte Carlo a minták szerint** (`learned+mc` vs `learned`): 4: +1,55 ± 1,38; 8: +2,91 ± 1,46; 16: +3,31 ± 1,47; 32: +4,06 ± 1,34; 64: +4,44 ± 1,50 | Csak a kijátszás különbözik: a Monte Carlo és a `playbot` kijátszás, azonos licittel | `match.py`, 400 leosztás (1200 ülés), seed 14 100 000, `eps` 0. Másik seed (14 300 000): 16 minta +3,03 ± 1,52, 32 minta +4,32 ± 1,47 | A tendencia: több minta, nagyobb előny, 32 körül telítődik | **A szomszédos értékek különbsége nem szignifikáns** (a ± 1,3–1,5), csak az irány. A 64 már nem mutat többletet a 32-höz képest. |
| **`eps` hatása** (16 minta): 0: +3,31; 0,1: +3,29; 0,2: +2,78; 0,5: +2,76; 1,0: +1,88 | A rollout véletlen lépéseinek (`eps`) hatása | Ugyanaz a mérés | 0,1-ig nincs hatás, felette rontja. Az alapérték 0. | Nem optimum-keresés: a ± itt is nagy. |
| **Rollout-politika**: véletlen rollout +2,16 ± 1,24 a +3,31-hez képest | A `playbot` rollout minősége számít-e | Ugyanaz a mérés, 16 minta | Jobb rollout, jobb döntés. A maradék javulás a rollout minőségétől várható, nem a mintaszámtól. | Nem bizonyítja, hogy egy erősebb rollout mennyit adna. |
| **Válaszidő / lépés** (32 minta, egy szál): 1. ütés átlag 42 ms (p95 94 ms, max kb. 110 ms), 5. ütés 16,3 ms, 9. ütés 2,9 ms | Mennyit vár a UI egy lépésre | egyszálas időmérés a `mcplay.mc_choose` hívásain (a mérőszkript ideiglenes volt, az eredmény `ALLAPOT.md`-ben is szerepel) | A UI-nak a szerver gyors, az időkeret 1,5 s/lépés | Nem a hálózati vagy böngészői idő. Más gépen más lehet. |
| **+0,87 ± 0,45** `e2e.js` | A te **valódi JS motorod** ellen: csak a kijátszást a Python adja (új Monte Carlo `/api/choose`) | `node tools/e2e.js <port> 240 py 7`, 240 leosztás, seed 7. 0 szabálytalan lap, 0 HTTP-hiba, 0 tartalék-hívás | A te motorodba bekötve a Python kijátszás ennyivel jobb | **Kisebb, mint a `match.py` +2–4, mert a licit és a kontra itt a te JS botod, csak a kijátszás Python.** A ± a két futás (`E2E_DUMP`) leosztásonkénti különbségéből jön: `python tools/e2e_diff.py py.json js.json` (1,96·szórás/√n), pontosan +0,87 ± 0,45-öt ad. 240 leosztás, kis minta. |
| **−0,90 ± 0,86** `e2e.js`, régi motor | Ugyanez a régi `engine.py` kijátszással | Ugyanaz a mérés | A régi motor rosszabb a JS botnál | A régi motor már csak tartalék. |
| **Védői kontra +0,255 ± 0,109** | A `kontra_rule.py` várható-pénz szabálya a küszöbös helyett | `kontra_rule.json`, `results`, 21 000 ülés (7000 leosztás), seed 18 000 000+ | A védő kontráz, ha a kalibrált modell szerint várhatóan nyer rajta. Ennyit hoz ülésenként. | A nyereség **szinte teljesen a betli kontrájából jön** (`only_betli` +0,225 ± 0,143, a többi nulla körül). **Kalibrált védői modell kell hozzá**: kalibráció nélkül +0,021 ± 0,022. Kicsi szám, 3000 ülésnél kisebb mintán nem látszik (3000 ülésen +0,199 ± 0,299). |
| **Tanult licit a JS+MC kijátszás ellen: +0,10 ± 0,61** | A licit önmagában, ha az ellenfél kijátszása erős | `match.py`, 4500 ülés | A licit előnye erős kijátszás ellen eltűnik | Nem azt, hogy a licit rossz: azt, hogy a +2 nagy része a JS bot kijátszásának gyengeségéből és a Monte Carlóból jön. |
| **`learned+mc` a JS+MC ellen: +2,04 ± 1,09** | Licit + Monte Carlo kijátszás (8 minta) egy Monte Carlo-s ellenfél ellen | `match.py`, 1350 ülés | Az erős ellenféllel szembeni előny jó része a Monte Carlo kijátszásból jön | Kicsi minta, 8 minta. |
| **Negatív: liga-tanítás** | Régi generációk ellen tanítás javít-e | `league.py` → `league_results.json`, 4 kör, 600 ülés/kör. A jelölt a bajnok ellen: −2,14 ± 3,74; −3,77 ± 4,55; −0,50 ± 1,37; −1,23 ± 1,50. Végső (30 000 000+): +0,072 ± 0,73 (1950 ülés) a bajnok ellen, a JS ellen +1,65 ± 1,03 vs. +1,31 ± 1,11 (1050 ülés) | Nincs mérhető javulás, a `learned_policy.json` marad | Nem azt, hogy a liga eleve rossz ötlet: a logisztikus modell kapacitása lehet a korlát. |
| **Negatív: könyvi jellemzők** | A könyv tanácsaiból épített 17 új jellemző javít-e | `book_features.py`, `book_policy.py` → `book_features_results.json`, 3 halmaz (800×3, 800×3, 1200×3 ülés, seed 23–25 000 000). Összevont különbség **−0,028 ± 0,387** (8400 sor) | Nincs hatás: a +0,4-nél kisebbet a mérés nem mutatja ki. A tanult súlyok előjele 9/17-nél egyezik a könyv várakozásával, 8-nál ellentmond. | Nem azt, hogy a könyv téved. A könyv haszna a tanító botban van (idézetek), nem a motor súlyaiban. |
| **Negatív: védői kalibráció (`recal.py`) és on-policy újratanítás** | Jobb kalibráció és újratanítás javít-e pénzben | `recal.py`: a Brier-hiba 0,209 → 0,163, de a JS ellen +1,91 ± 0,60 vs. +1,97 ± 0,59 (4500 ülés), a régi önmaga ellen −0,98 ± 0,49. `learn.py --init`: −0,72 ± 1,11 (700 leosztás). Betli-tiltás: −0,26. `kt` hangolás: nem segített. | A jobb kalibráció önmagában nem hoz pénzt. A mentett politika változatlan. | Nem azt, hogy a kalibráció felesleges: a kontra-szabálynak kell (lásd fent). |
| **`pickup_thr` 1,0** | A rablási küszöb | `match.py`, a −1,0-hoz képest: +1,09 ± 0,63 (2700 ülés) és +1,33 ± 0,65 (3000 ülés) | Mindkét független mintán ugyanabba az irányba | Kb. másfél szórás: nem bizonyított, de beállítottuk. |
| **`book_eval`: 48 eset × 250 világ** | Motor-bemondás vs. a könyv bemondása, ahol eltérnek | `book_eval.py` → `book_eval.json`, a könyv díjaival: 59 eset, 9 azonos, 2 érvénytelen, 48 kiértékelt. Verdikt: motor 11, könyv 15, nem eldönthető 22 | **Vállalás- és aduválasztás: döntetlen** (átlag −0,03, n=32). **Talonletétel: a könyvé** (átlag −3,04, n=16: 5 könyv, 1 motor, 10 nem eldönthető) | A legnagyobb eltérések (±55–63 pont) a bot **gyenge 20-100-as kijátszását** tükrözik, nem a bemondás értékét. A diff = motor − könyv, a negatív a könyvnek kedvez. |
| **Szabályegyezés a te JS motorodéval** | A Python `rules.py` ugyanazt számolja-e, mint a te motorod | `tools/xcheck.js`: 400 véletlen játszma (`400 777`), a pénz és a sorok azonosak. 9564 kijátszási lépés azonos (`400 4242 bot`). Licit-mód (`300 99 auction`): 300 játszma, 631 kontra-döntés, 323 licit-ellenőrzés, 300 „own” ellenőrzés azonos | A szabálymotor és a bot-portok a te kódoddal egyeznek | Nem azt, hogy a **te** motorod egyezik a könyvvel (lásd lent). A tesztek érzékenyek: két szándékosan elrontott szabályt elkaptak. |
| **Díjtábla-ellenőrzés: 27 sor, 55 figura** | A Kovács-könyv 85–88. oldali díjtáblája belsőleg következetes-e | `check_book_prices.py` (a táblázatot képről kézzel írtuk át): minden figura értéke kijön az összegből | A könyv táblája jól van átvéve | Nem bizonyítja, hogy az **egész** könyv helyes: a 20-100-nál és a durchmarsnál a könyv szándékosan tér el az alapszámítástól. |
| **13 teljes visszajátszás egyezik** | A könyv példatárából kinyert leosztásokat a motor lejátssza, és a könyv pontszámát adja-e | `build_examples.py` → `replay.json`, 29 példa: 13 teljes lejátszás mind egyezik a könyv pontszámával, 4 részleges, 5 csak licit, 6 hibás | A szabálymotor a könyv példáin is jól számol (kontrákkal, csendes tételekkel) | A 6 hibás és az 5 csak-licit az OCR-ből vagy a licit-lépések kinyeréséből adódik, nem a motorból. 3 helyen kézi javítás van (22. példa kontrája, a 14. és a 30. példa végeredménye). A 23. példa diagramja a könyvben hibás, kizártuk. |
| **Tesztek: 110** | A csomag automata tesztjei | `python -m unittest discover -s tests -t .` (kb. 95 s) | Minden zöld, kulcs nélkül is fut | Nem fedi le a böngészőt (csak jsdom). |

### 4.3 Ellentmondások a forrásokban (és mi lett velük)

Ezeket nem simítottuk el. Ahol a forrásdokumentum téves volt, kijavítottuk:

1. **Tesztek száma.** Az `INTEGRATION.md` „36 teszt”-et írt. Javítva: **110**.
2. **Szignifikanciaküszöb.** A README „egyoldali ~95%” szintet írt a tanító botnál. A kódban `z`=2,0 (kb. 97,7% egyoldali), lapdöntésnél 3. A README javítva.
3. **Visszajátszás.** 13 teljes és egyező visszajátszás van, de 14 példa „teljes” státuszú: a 7. példánál az ütéstábla kétértelmű, ott az egyezés nem dönthető el. Ez így marad, a szám (13) a megbízható.
4. **Licit-egyezés.** Az `ALLAPOT.md` „300 licit” egyezést írt. A `xcheck.js auction` 300 játszmából 323 licit-, 300 „own” és 631 kontra-ellenőrzést ad. Javítva.
5. **Felvevő arány.** A régi mérések 0,74–0,85, a `pickup_thr` 1,0 mellett 0,62. Az `ALLAPOT.md` zavaros mondata javítva.
6. **A tanult erő több mintán.** +2,09 ± 1,17 (1200 ülés), +1,97 (recal-alapvonal, 4500 ülés), +2,03 ± 0,39 (7500 ülés), +2,033 ± 0,774 (`book_features` r1). Konzisztensek, csak különböző minták. A 7500 ülésnyi mérés seedje nincs rögzítve.
7. **Különböző előnyök.** `learned+mc` a `learned` ellen kb. +4, az `e2e.js` szerint +0,87, a tanult licit a JS+MC ellen +0,10. A három mérés felépítése különbözik (más ellenfél, más, hogy a licit is a vizsgált része-e), ezért nem vonhatók össze.
8. **README.** Az „Architektúra” rész a régi Gemini-s láncot írta le. Újraírva a jelenlegi szerkezetre.
9. **`book_eval`.** A legnagyobb eltérések a bot kijátszási gyengeségét mutatják, nem a bemondás értékét. Ezért a vállalás/adu-választás (−0,03) a megbízhatóbb szám.
10. **`e2e.js` ±.** Az `e2e.js` maga csak a pénzt írja ki. A ± értéket a `tools/e2e_diff.py` számolja a `E2E_DUMP` fájlokból (a +0,87 ± 0,45 reprodukálva).
11. **Kontra a tanító botban.** A tanító bot szimulációja korábban a küszöbös kontrával játszott, miközben az éles szerver a várható-pénz kontrát használja. Javítva: mindkettő a `kontra_rule.default_policy`-t hívja.
12. **`ExpectedMoneyKontra` alapérték.** A rekontra-módja alapból `"ev"` volt, de minden mérés (+0,255) `"base"`-szel készült (az `"ev"` +0,216). Az alapérték `"base"` lett.

### 4.4 Hogyan jutottunk ide

1. **Szabálymotor a te JS-edhez mérve.** Először a teljes játékot írtuk meg Pythonban (`rules.py`), és a te motoroddal keresztellenőriztük: véletlen játszmák, pénz és sorok azonosak (lásd a szabályegyezés sorát).
2. **A te botod portja.** A licitet, a kontrát és a kijátszást is átírtuk (`jsbot.py`, `playbot.py`), mert egy ellenfél kell, amihez mérünk.
3. **Tanult politika önjátékból.** A licit, a talon, a rablás és a kontra komponensenként tanult modell lett, a célfüggvény a pénz, mert a mínuszpont pénzbe kerül.
4. **Mérés a JS ellen.** Páros méréssel kb. +2 egység/ülés a te szabályaiddal, +3,8 a könyv díjaival.
5. **Kiderült, hogy az előny nagy része máshonnan jön.** Erős kijátszás (JS+MC) ellen a tanult licit előnye eltűnt (+0,10 ± 0,61).
6. **Monte Carlo kijátszás.** A `rules.Game`-en futó szimulációs kijátszás +4 körüli előnyt adott a `playbot` kijátszáshoz képest, 32 mintánál telítődött. Ez lett az `/api/choose` motorja.
7. **Talon-modell és rablási döntés.** A licitnapló alapján a motor következtet arra, mit tett le a licitáló, és a rablás várható értékét számolja.
8. **Kontra.** A küszöbös szabály helyett a kalibrált védői modellből számolt várható-pénz szabály lett (+0,255 ± 0,109).
9. **Tanító bot és UI-bekötés.** A `tutor.py`, a `/api/bid`, `/api/kontra`, `/api/review` végpontok és a `make_ui.py` horga. A UI-t jsdom-ban futtattuk, 4xx/5xx nélkül.
10. **A könyv feldolgozása.** A díjtábla képről, a példatár visszajátszása (13 teljes egyezés), 341 idézettel ellenőrzött tétel, a motor és a könyv bemondásainak összevetése.
11. **Amit kipróbáltunk, és nem működött:** liga-tanítás, könyvi jellemzők, védői kalibráció önmagában, on-policy újratanítás, a sima és a piros betli tiltása, a küszöbök hangolása. Ezek a kézenfekvő irányokat zárták ki, ezért a mentett politikák változatlanok.

## 5. Miért jó (őszintén)

**Igazolt:**

| Állítás | Mivel igazolt |
|---|---|
| Pénzben veri a te JS botodat | +2,03 ± 0,39 (7500 ülés), páros mérés, friss leosztásokon |
| A szabályok egyeznek a te motoroddal | 400 játszma, 9564 kijátszási lépés, 631 kontra-döntés azonos |
| Szabályos lapot játszik | `e2e.js`: 240 leosztás, 0 szabálytalan lap, 0 HTTP-hiba |
| Gyors | 32 mintával kb. 3–42 ms átlag lépésenként, legrosszabb kb. 110 ms |
| API-n elérhető, hibánál visszavált | `/api/*` végpontok, 4xx/5xx esetén a UI saját botja játszik |
| Tanít | A tanító bot szimulációval mért pontveszteséget, okot és könyvi idézetet ad, LLM nélkül is |
| Kulcs nélkül fut | A tesztek (110) és a szerver is |

**Nem igazolt, vagy korlátozott:**

- **Embereken nem mértük.** Minden mérés a te JS botod és a saját régi változataink ellen készült.
- **Erősebb ellenféllel az előny kisebb.** A licit előnye erős kijátszás ellen eltűnt (+0,10 ± 0,61).
- **A tanító bot tanácsait embereken nem próbáltuk ki.** Nem tudjuk, hogy jobb játékot tanítanak-e. A kontra-tanácsok különösen bizonytalanok: a betli kontrázása magának a motornak sem segít (+2,46 ± 1,01 a +2,53 ± 1,01 helyett).
- **Böngészős kipróbálás nem volt.** Csak jsdom futott, ezért a kinézetet és a kattintásokat nálad kell kipróbálni.
- **A motor a te 42 vállalásos díjtáblád szerint fut.** A Kovács-féle tábla eltér ettől (lásd a +3,8-as sort). A könyvvel a díjtábla és a példák egyezését ellenőriztük, a motor nem a könyvhöz igazodik.
- **Eltérő bemondásnál nem a könyvhöz igazítottunk.** A `book_eval` szerint a vállalás/adu-választás döntetlen, a talonletétel a könyvé (−3,04 átlag). A talonletétel újratanítása még hátravan.
- **A bedobás aránya 0,000.** A motor nem tanult meg bedobni.
- **A felvevő arány (0,62) magas** a JS 0,333-hoz képest. Emberi ellenfél ellen nem biztos, hogy így marad.
- **A védői kontra nyeresége kicsi** (+0,255), szinte csak a betliből jön.
- **A mérési zaj nagy.** A szomszédos Monte Carlo mintaszámok különbsége nem szignifikáns, csak a tendencia az.
- **A könyv nyers anyaga (OCR-szöveg, oldalképek, `.djvu`) nincs a csomagban.** A feldolgozott tudás (idézetekkel, a `rabloulti_biblia.txt`-tel együtt) igen.

## 6. Szószedet

| Fogalom | Jelentés |
|---|---|
| **Pénz / pont** | A játék elszámolási egysége. A parti alapdíja 1. A nyereség és a veszteség is ebben mérődik. |
| **Ülés** | Egy leosztás egy játékos szemszögéből. Egy leosztásból 3 ülés lesz. |
| **Páros mérés** | Ugyanazt a leosztást kétszer játsszuk le: egyszer az alap politikával, egyszer a vizsgálttal. A különbség csak a politikától függ. |
| **Konfidenciaintervallum (CI)** | A mérés bizonytalansága. A „± érték” a 95%-os intervallum fele. Ha a 0 az intervallumon kívül van, az eredmény valószínűleg nem véletlen. |
| **Standard hiba** | A szórás osztva az ülések számának négyzetgyökével. A ± ennek 1,96-szorosa. |
| **Seed** | A véletlenszám-generátor kezdőértéke. Ugyanaz a seed ugyanazokat a leosztásokat adja, így a mérés megismételhető. |
| **Friss leosztás** | Olyan leosztás, amit a tanítás vagy a hangolás nem látott. Csak ezen mért szám hiteles. |
| **JS bot** | A te botod licitje és kijátszása, Pythonban portolva. Ellenfél és mérce, de gyenge. |
| **Rollout** | Egy szimulált játszma végigjátszása egy adott állásból, a hátralevő lépésekkel. |
| **Determinizálás** | A látatlan lapok véletlen szétosztása a két másik játékos közt, a tudott színhiányok betartásával. Így a rejtett információjú játékból egy teljes információjú „világ” lesz. |
| **Monte Carlo** | Sok ilyen világot lejátszunk, és az átlag alapján döntünk. |
| **Kalibráció** | Az, hogy a modell által jósolt esély egyezik-e a valósággal. Ha 2%-ot jósol, és a valóságban 22%, akkor rosszul kalibrált. |
| **Brier-hiba** | A jósolt esély és a tényleges kimenetel (0 vagy 1) különbségének négyzetes átlaga. Kisebb a jobb. 0,209-ről 0,163-ra javult. |
| **Logisztikus modell** | Egyszerű képlet, amely a kézjellemzőkből 0 és 1 közötti esélyt számol (például: teljesül-e az ulti). |
| **Ridge** | Az illesztésnél a súlyokat „visszafogó” büntetés, hogy a modell ne tanulja meg a zajt. |
| **Önjáték** | A gép saját maga ellen játszik, és az eredményekből tanul. |
| **Kontra / rekontra** | A védő kontrája duplázza a vállalás díját, a felvevő rekontrája négyszerezi. Tovább is mehet (szubkontra 8×, hirskontra 16× stb.). |
| **Felvevő arány** | A leosztások hányadában lesz a játékos felvevő. Három azonos játékosnál 1/3. |
| **Bedobás** | A felvevő a díj kétszeresének megfizetésével feladja a leosztást. |
| **Renonsz** | Szabálytalan lépés. A motor nem renonszol. Tudott színhiány (`known_void`) alatt azt értjük, hogy valaki nem adott színt. |
| **Csendes tétel** | Bemondás nélkül teljesített ulti, 100, 4 ász vagy durchmars. Külön díjat kap. |
| **jsdom** | JavaScript futtatókörnyezet böngésző nélkül. Ezzel futtattuk az `ulti_py.html`-t. Nem helyettesíti a böngészős kipróbálást. |
| **e2e (end-to-end)** | A te valódi JS motorod ellen futó mérés Node-ban, a Python szerver HTTP-n át. |
| **Tartalék (fallback)** | Ha a szerver nem érhető el, a UI a saját JS botját használja. |

## 7. Hol van mi

```
README.md, requirements.txt
docs/            ATTEKINTES.md (ez), ALLAPOT.md, INTEGRATION.md
ui/              ulti.html (eredeti, érintetlen), ulti_py.html (generált), cards/
tools/           e2e.js (valódi JS-motor ellen mér), e2e_diff.py (páros ± a két futásból), xcheck.js, make_export.py
data/policy/     learned_policy.json, learned_book.json, kontra_rule.json
data/rules/      contracts.json, book_prices.json
data/knowledge/  kodex_advice.json, book_knowledge.json, replay.json, facts.json, diagrams.json, book_bids.json, rabloulti_biblia.txt, heuristics_weights.json
data/research/   learned_league.json, learned_bookfeat.json, league_results.json, book_features_results.json, book_eval.json
ulti/paths.py     központi útvonalak (ROOT, DATA, UI, WORK ...)
ulti/__init__.py  az alcsomag-mappákat felveszi a sys.path-ra (a modulok lapos névvel importálnak)
ulti/core/       rules.py, jsbot.py, playbot.py, features.py
ulti/ai/         policy.py, talon.py, arena.py, mcplay.py, kontra_rule.py
ulti/coach/      tutor.py, wx.py
ulti/server/     server.py, uibridge.py, bridge.py, make_ui.py
ulti/training/   learn.py, tune.py, recal.py, fit_talon.py, match.py, final_eval.py
ulti/research/   league.py, book_features.py, book_policy.py, book_eval.py, book_bids.py
ulti/legacy/     engine.py, montecarlo.py, llm.py, api.py, main.py, refinement_loop.py
ulti/bookprep/   extract_*.py, parse_tricks.py, check_book_prices.py, kodex_cards.py, read_diagrams.py, build_examples.py
tests/           test_*.py
work/, raw/      csak a fejlesztői fán: futásidejű kimenetek és a könyv nyers anyaga (nincs a csomagban)
```

| Mappa | Mi van benne |
|---|---|
| `ulti/legacy/` | Az elavult Gemini-s lánc. A szerver csak tartaléknak használja az `engine.py`-t. |
| `ulti/bookprep/` | A könyv feldolgozása. A nyers könyvanyag nincs a csomagban. |
| `work/`, `raw/` | Csak a fejlesztői fán léteznek. A `work/` a futásidejű kimeneteket (liga-generációk) tartja, a `raw/` a könyv nyers anyagát. Egyik sincs a csomagban. |
| `data/research/` | A négy kutatási kísérlet eredménye (liga, könyvi jellemzők, könyv-összevetés). Nem kell a futtatáshoz. |

### Parancsok

| Mit | Parancs |
|---|---|
| UI generálása és szerver indítása | `python -m ulti.server.make_ui && python -m ulti.server.server` |
| Megnyitás | http://127.0.0.1:8765/ulti_py.html (`?engine=js`: a UI saját botjai) |
| Tesztek | `python -m unittest discover -s tests -t .` |
| Mérés (tanult a JS ellen, 400 leosztás, seed 16 000 000) | `python -m ulti.training.match learned js 400 16000000` |
| Mérés a te valódi JS motorod ellen | `node tools/e2e.js <port> <leosztás> py\|js [seed]` |
| Szabály-keresztellenőrzés | `node tools/xcheck.js <játszmák száma> [seed] [bot\|auction]` (a kimenetet a `tests/test_rules.py` hasonlítja a Python lejátszáshoz) |

### Környezeti változók

| Változó | Hatás |
|---|---|
| `ULTI_RULES=book` | A Kovács-könyv díjtáblája a `friend` helyett |
| `ULTI_KONTRA=threshold` | A régi küszöbös kontra a várható-pénz szabály helyett |
| `GOOGLE_API_KEY` | Csak a régi, elavult Gemini-lánchoz kell. A szerverhez nem. |
| watsonx-hozzáférés | Csak az opcionális `polish` és a könyvkinyerés használja. A token a `ibmcloud` bejelentkezésből jön, fájlba nem kerül. |

> **Biztonság.** A fejlesztés alatt egy API-kulcs a chatben nyilvánossá vált. Ha a tiéd, vond vissza és generálj újat. A csomagban nincs kulcs, és ne is tegyél bele: a kulcsot mindig környezeti változóként add meg.

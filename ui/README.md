# Ulti – két bot ellen

Böngészős, egyszemélyes magyar ulti (rablóulti) játék: a játékos két bot (Géza, Marika) ellen játszik. Tipp rendszerrel és oktató móddal, magyar kártyaképekkel.

## Fájlok

- `ulti.html` – a teljes játék egyetlen fájlban (HTML + CSS + JavaScript, külső függőség csak a Google Fonts).
- `cards/c0.webp` … `cards/c31.webp` – a 32 kártyakép. Az oldal relatív úton tölti be őket (`cards/cN.webp`), ezért a mappának az `ulti.html` mellett kell lennie.
- `ulti_standalone.html` – ugyanaz a játék, a kártyaképek base64-ként beágyazva. Egy fájlként, internet nélkül is megnyitható. LLM-nek ne ezt add, mert a képek miatt nagy (~450 KB); a kódhoz az `ulti.html` elég.

Indítás: nyisd meg az `ulti.html`-t böngészőben (a `cards` mappával együtt), vagy a standalone fájlt.

## Kártyaazonosítók

`id = s*8 + r`
- szín `s`: 0 piros, 1 tök, 2 zöld, 3 makk
- rang `r`: 0 VII, 1 VIII, 2 IX, 3 X, 4 alsó, 5 felső, 6 király, 7 ász

Adus erősorrend (`TP` tömb): ász > X > király > felső > alsó > IX > VIII > VII. Adu nélküli játékban (betli, szín nélküli durchmars) a természetes `r` sorrend: ász > király > felső > alsó > X > IX > VIII > VII.

## A kód szerkezete (`ulti.html` `<script>`)

| Szakasz | Tartalom |
|---|---|
| `ENGINE START … ENGINE END` | Tiszta játéklogika, DOM nélkül. Node-ban külön is futtatható tesztekhez. |
| `CONTRACTS` | A licitsor (42 vállalás) a Jánoska–Zsigri könyv táblázata szerint: `name`, `val`, `comps` (részek és értékük: `parti`, `c40`, `c20`, `ulti`, `betli`, `durch`), `kind` (`trump` / `betli` / `durch` = szín nélküli), `red`, `open` (terített). |
| állapot `G` | `hands`, `talon`, `phase` (`bid` → `kontra` → `play` → `result`), `bid`, `decl`, `trump`, `trick`, `hist`, `won`, `kmult` (kontraszorzók védőnként és részenként), `forced` (kényszervállalás), `marr` (20/40 bemondások), `log`. |
| licit | `doBid`, `doPass`, `doPickUp`, `doConcede`, `minBidIndex` (csak nagyobb pontértékű vállalás), `checkReq`, `forcedParts`, `validateBid`. Önrablás: két passz után a licitáló visszaveheti a saját talonját. |
| kontra | `kontraOptions`, `doKontra`, `mustKontra` (kényszervállalásnál kötelező), `kontraCommon` (adus játékban közös, betliben és szín nélküli durchmarsban egyéni). |
| lejátszás | `legalCards` (színkényszer, felülütés, adukényszer, ulti: a VII nem adható ki, amíg más adu van), `beats`, `playCard`, `collectTrick`. |
| elszámolás | `finishHand`, `defGain` (bukott ulti: kontra nélkül 2x, kontrával 3x, rekontrával 5x), csendes ulti / 100 / négy ász / durchmars. |
| bot licit | `trumpStats`, `pBetli`, `pDurch`, `compP` (esélybecslés, a könyv feltételeinek nem megfelelő vállalás 0,82-es szorzót kap), `estimate`, `bidOptions`, `botBidAction`. |
| stratégia | `handFacts`, `bookComp`, `bookCheck`, `bookHTML` (a könyv licitfeltételei pipálható listaként), `knownVoid`, `partnerSuits`, `stopperSuit`. |
| bot kontra | `kontraJudge` (a könyv kontratanácsai, rekontra alullicitálásnál), `botKontra`. |
| bot kijátszás | `botChoose` – minden döntés indoklást ír a `WHY` változóba, ezt mutatja a Tipp. |
| UI | `render*` függvények, `computeHint` (Tipp), `buildAnalysis` / `analyzeBid` / `simulate` (oktató mód: kör utáni elemzés, a lehetséges vállalások gépi lejátszása a valódi leosztással). |

## Szabályok (döntések)

Alap: ultiblog.hu szabálykönyv, kiegészítve Jánoska Levente – Zsigri: *A rablóulti szabályai* könyvvel.

- Kezdő 12 lap, a többiek 10. Licit a talon felvételével, mindig pontszámban értékesebb vállalás.
- 42 vállalás, összetett vállalásokkal (40-100 / 20-100 / ulti / adus durchmars kombinációk, piros és terített változatok). A könyv táblázatán felül benne maradt a „Piros 20-100” (16).
- Kényszervállalás: 40-100 a 40 nélkül, 20-100 a 20 nélkül mondható; a hiányzó bemondás pontja nem számít, a kulcslapot tartó védő kötelezően kontráz.
- Kontra / rekontra (szubkontra nincs), az első lap előtt.
- Bedobás bármikor (licit végén vagy játék közben), mindig kontrázott áron: a vállalás duplája védőnként, bukott ulti háromszorosa.
- 20/40 bemondás csak akkor számít, ha a fél vitt ászt, X-et vagy az utolsó ütést.
- Csendes ulti (2, bukva 4), csendes 100 (2), csendes négy ász (2), csendes durchmars (3); piros adunál dupla.

## Tesztelés

Az `ENGINE START` és `ENGINE END` közötti rész kivágható és Node-ban futtatható: botok egymás elleni leosztásaival ellenőrizhető a szabályos lapadás, hogy a pontok összege 0, és a vállalások nyerési aránya.

A kártyaképek a felhasználó által feltöltött magyar kártya táblaképből vannak kivágva.

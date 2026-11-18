// e2e.js - A barátod valódi JS-motorja (ENGINE START..END) + a Python motor HTTP-n át.
// Futtatás: node e2e.js <port> <leosztások> [py|js]
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'ui', 'ulti.html'), 'utf8');
const a = html.indexOf('// ===== ENGINE START =====');
const b = html.indexOf('// ===== ENGINE END =====');
if (a < 0 || b < 0) { console.error('ENGINE jelölők nem találhatók'); process.exit(2); }

// seedelt véletlen: ugyanaz a leosztás és licit py és js módban, így a mérés páros
let _s = 1;
Math.random = () => { _s = (_s + 0x6D2B79F5) | 0; let t = Math.imul(_s ^ (_s >>> 15), 1 | _s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const seed0 = +process.argv[5] || 1;
const port = process.argv[2], deals = +process.argv[3] || 20, mode = process.argv[4] || 'py';
const code = html.slice(a, b) + `
function snapshot(p){
  const c=CONTRACTS[G.ci];
  const ids=a=>a.map(x=>x.id);
  const tr=t=>t.map(x=>({p:x.p,c:x.c.id}));
  const s={p,dealer:G.dealer,ci:G.ci,decl:G.decl,kind:c.kind,comps:Object.keys(c.comps),red:!!c.red,open:!!c.open,trump:G.trump,
    hand:ids(G.hands[p]),sizes:G.hands.map(h=>h.length),talon:p===G.decl?ids(G.talon):[],
    trick:tr(G.trick),hist:(G.hist||[]).map(tr),legal:ids(legalCards(p)),
    kmult:G.kmult,forced:G.forced||{},marr:G.marr};
  if(c.open&&G.revealed&&p!==G.decl)s.reveal={[G.decl]:ids(G.hands[G.decl])}; // a nyitott játék felfedett keze mindenkinek látszik
  return s;
}
return {newDeal,botBidAction,doBid,doPass,doPickUp,doConcede,doKontra,botKontra,startPlay,playCard,collectTrick,
        botChoose,legalCards,snapshot,get G(){return G;},CONTRACTS};`;
const E = new Function(code)();

let legacy = 0;
async function pyChoose(p) {
  const r = await fetch(`http://127.0.0.1:${port}/api/choose`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(E.snapshot(p))});
  if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + await r.text());
  const j = await r.json();
  if (!/Monte Carlo/.test(j.why || '')) legacy++;
  return j.id;
}

(async () => {
  let played = 0, illegal = 0, http = 0, decl = {py: [0, 0], js: [0, 0]}, perDeal = [], money = {py: 0, js: 0}, seats = {py: 0, js: 0}, sumZero = true, skipped = 0;
  for (let d = 0; d < deals; d++) {
    _s = seed0 * 100003 + d;
    E.newDeal(d % 3);
    let G = E.G, guard = 0;
    while (G.phase === 'bid' && guard++ < 60) {
      const p = G.turn, act = E.botBidAction(p);
      if (act.type === 'bid') E.doBid(p, act.disc, act.ci, act.suit);
      else if (act.type === 'pickup') E.doPickUp(p);
      else if (act.type === 'concede') { E.doConcede(p); break; }
      else E.doPass(p);
    }
    G = E.G;
    if (G.phase === 'result') { skipped++; continue; }
    while (G.phase === 'kontra') { const p = G.turn; E.doKontra(p, E.botKontra(p)); }
    G = E.G;
    if (G.phase !== 'play') { skipped++; continue; }
    const pyDecl = d % 2 === 0;           // páros leosztásnál a felvevő a Python motor, páratlannál a védők
    const usePy = p => mode === 'py' && (pyDecl ? p === G.decl : p !== G.decl);
    let steps = 0;
    while (G.phase === 'play' && steps++ < 40) {
      if (G.trick.length === 3) { E.collectTrick(); continue; }
      const p = G.turn, legal = E.legalCards(p);
      let card;
      if (usePy(p)) {
        try { const id = await pyChoose(p); card = legal.find(x => x.id === id); if (!card) illegal++; }
        catch (e) { if (++http <= 3) console.error('HIBA', e.message.slice(0, 120)); }
      }
      card = card || E.botChoose(p);
      E.playCard(p, card); played++;
    }
    if (G.phase === 'result' && G.result) {
      const sum = G.result.change.reduce((x, y) => x + y, 0);
      if (sum !== 0) sumZero = false;
      const win = G.result.change[G.decl] > 0;
      const k = (mode === 'py' && pyDecl) ? 'py' : 'js';
      decl[k][0] += win; decl[k][1]++;
      // pénz a Python motor ülésein (mode=py: felvevő vagy a két védő), mode=js: ugyanazok az ülések a JS botnak
      const seatsPy = pyDecl ? [G.decl] : [0, 1, 2].filter(q => q !== G.decl);
      const m = seatsPy.reduce((x, q) => x + G.result.change[q], 0);
      money[mode] += m; seats[mode] += seatsPy.length; perDeal.push(m / seatsPy.length);
    }
  }
  console.log(JSON.stringify({deals, skipped, played, illegal, http_errors: http, legacy_fallbacks: legacy, sum_zero: sumZero,
    decl_wins: decl, money_per_seat: seats[mode] ? +(money[mode] / seats[mode]).toFixed(2) : null}));
  if (process.env.E2E_DUMP) fs.writeFileSync(process.env.E2E_DUMP, JSON.stringify(perDeal));
  process.exit(illegal || http || !sumZero ? 1 : 0);
})();

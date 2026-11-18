// xcheck.js - A JS motorral véletlen játszmákat játszik le, és kimenti a teljes lépéssort + az eredményt.
// A Python (test_rules.py) ugyanezt lejátssza a rules.py-val, és az eredménynek pontosan egyeznie kell.
// Futtatás: node xcheck.js <játszmák száma> > /tmp/xcheck.json
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'ui', 'ulti.html'), 'utf8');
const a = html.indexOf('// ===== ENGINE START =====');
const b = html.indexOf('// ===== ENGINE END =====');
const code = html.slice(a, b) + `
return {newDeal,doBid,doPass,doPickUp,doConcede,doKontra,kontraOptions,collectTrick,legalCards,validateBid,
  canPickUp,minBidIndex,suitsFor,holding12,playCard,botChoose,botBidAction,botKontra,bidOptions,botWantsPickup,estimate,get G(){return G;},set G(v){G=v;},CONTRACTS};`;
const E = new Function(code)();

// determinisztikus véletlen, hogy a futás reprodukálható legyen
let seed = +process.argv[3] || 12345;
const BOT = process.argv[4] === 'bot';
const AUC = process.argv[4] === 'auction';
const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
Math.random = rnd;
const pick = arr => arr[Math.floor(rnd() * arr.length)];
const ids = arr => arr.map(x => x.id);

function randomBid(p, G, hand) {
  hand = hand || G.hands[p];
  const min = E.minBidIndex();
  const cand = [];
  for (let ci = min; ci < E.CONTRACTS.length; ci++) cand.push(ci);
  // véletlen sorrend, hogy ne mindig ugyanazt válassza
  for (let i = cand.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [cand[i], cand[j]] = [cand[j], cand[i]]; }
  // az olcsóbbak gyakoribbak
  cand.sort((a, b) => (a + rnd() * 20) - (b + rnd() * 20));
  for (const ci of cand) {
    const su = pick(E.suitsFor(E.CONTRACTS[ci]));
    for (let t = 0; t < 12; t++) {
      const i = Math.floor(rnd() * hand.length);
      let j = Math.floor(rnd() * (hand.length - 1)); if (j >= i) j++;
      const disc = [hand[i], hand[j]];
      const ids2 = new Set(disc.map(x => x.id));
      const rest = hand.filter(x => !ids2.has(x.id));
      const err = E.validateBid.length && (function () {
        const save = G.hands[p]; G.hands[p] = hand; const e = E.validateBid(p, disc, ci, su); G.hands[p] = save; return e;
      })();
      if (!err) return { ci, su, disc };
    }
  }
  return null;
}

function auctionMain(n) {
  const out = [];
  for (let g = 0; g < n; g++) {
    E.newDeal(g % 3);
    const G = E.G, rec = { dealer: g % 3, hands: G.hands.map(ids), acts: [], checks: [] };
    let guard = 0;
    while (G.phase === 'bid' && guard++ < 80) {
      const p = G.turn;
      if (E.holding12(p)) {
        const o = E.bidOptions(G.hands[p], E.minBidIndex(), 0)[0];
        rec.checks.push({ t: 'bid', p, ci: o.ci, su: o.suit, ev: o.ev, at: rec.acts.length });
      } else if (G.bid && G.bid.seat === p && E.canPickUp(p)) {
        const o = E.bidOptions([...G.hands[p], ...G.talon], E.minBidIndex(), 0)[0];
        rec.checks.push({ t: 'own', p, ci: o ? o.ci : -1, ev: o ? o.ev : null, cur: E.estimate(G.hands[p], G.bid.ci, G.bid.suit, null), at: rec.acts.length });
      }
      const act = E.botBidAction(p);
      if (act.type === 'bid') { rec.acts.push({ t: 'bid', p, ci: act.ci, su: act.suit, disc: ids(act.disc) }); E.doBid(p, act.disc, act.ci, act.suit); }
      else if (act.type === 'pickup') { rec.acts.push({ t: 'pickup', p }); E.doPickUp(p); }
      else if (act.type === 'concede') { rec.acts.push({ t: 'concede', p }); E.doConcede(p); break; }
      else { rec.acts.push({ t: 'pass', p }); E.doPass(p); }
    }
    while (G.phase === 'kontra') {
      const p = G.turn, ks = E.botKontra(p).map(o => o.k);
      rec.checks.push({ t: 'kontra', p, ks, at: rec.acts.length });
      rec.acts.push({ t: 'kontra', p, ks });
      E.doKontra(p, E.botKontra(p));
    }
    rec.ci = G.ci; rec.decl = G.decl;
    out.push(rec);
  }
  process.stdout.write(JSON.stringify(out));
}
if (AUC) { auctionMain(+process.argv[2] || 100); } else {
const out = [];
const n = +process.argv[2] || 100;
for (let g = 0; g < n; g++) {
  const dealer = g % 3;
  E.newDeal(dealer);
  const G = E.G;
  const rec = { dealer, hands: G.hands.map(ids), acts: [] };
  let guard = 0;
  while (G.phase === 'bid' && guard++ < 80) {
    const p = G.turn;
    if (E.holding12(p)) {
      const bd = randomBid(p, G);
      if (!bd) { console.error('nincs érvényes licit'); process.exit(3); }
      rec.acts.push({ t: 'bid', p, ci: bd.ci, su: bd.su, disc: ids(bd.disc) });
      E.doBid(p, bd.disc, bd.ci, bd.su);
      continue;
    }
    const own = G.bid && G.bid.seat === p;
    const r = rnd();
    if (E.canPickUp(p) && r < (own ? 0.25 : 0.3) && randomBid(p, G, G.hands[p].concat(G.talon))) { rec.acts.push({ t: 'pickup', p }); E.doPickUp(p); continue; }
    if (own && r > 0.93) { rec.acts.push({ t: 'concede', p }); E.doConcede(p); break; }
    rec.acts.push({ t: 'pass', p }); E.doPass(p);
  }
  if (G.phase === 'kontra') {
    guard = 0;
    while (G.phase === 'kontra' && guard++ < 10) {
      const p = G.turn, opts = E.kontraOptions(p);
      const chosen = opts.filter(() => rnd() < 0.35);
      rec.acts.push({ t: 'kontra', p, ks: chosen.map(o => o.k) });
      E.doKontra(p, chosen);
    }
  }
  guard = 0;
  while (G.phase === 'play' && guard++ < 40) {
    if (G.trick.length === 3) { rec.acts.push({ t: 'collect' }); E.collectTrick(); continue; }
    const p = G.turn, L = E.legalCards(p);
    const c = BOT ? E.botChoose(p) : pick(L);
    rec.acts.push({ t: 'card', p, id: c.id });
    // playCard a motorban nincs exportálva, ezért újra beépítjük a hívást
    E.playCard(p, c);
  }
  rec.change = G.result ? G.result.change : null;
  rec.conceded = !!(G.result && G.result.conceded);
  rec.ci = G.ci; rec.decl = G.decl; rec.trump = G.trump;
  rec.rows = G.result ? G.result.rows.map(r => ({ k: r.k, win: !!r.win, delta: r.delta })) : [];
  out.push(rec);
}
process.stdout.write(JSON.stringify(out));
}

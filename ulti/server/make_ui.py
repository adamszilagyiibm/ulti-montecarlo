"""
make_ui.py - Legenerálja az ulti_py.html-t az eredeti ulti.html-ből egy minimális horoggal.

Az eredeti fájl érintetlen marad. Ha a barátod újra kiad egy ulti.html-t, futtasd újra:
    python make_ui.py
A horog a botok KIJÁTSZÁSÁT (/api/choose), LICITJÉT (/api/bid) és KONTRÁJÁT (/api/kontra) a Python motorra cseréli,
és a leosztás végén egy "Tanács a Python motortól" gombot ad (/api/review, a tutor.py elemzése).
Ha a szerver nem elérhető vagy hibát ad, az adott döntésnél a JS bot játszik (és a UI jelzi).
A licit és a kontra a barát 42 vállalásos táblájával és a learned_policy.json-nal megy.
"""
from ulti.paths import UI as UI_ROOT
from pathlib import Path

UI_DIR = UI_ROOT
SRC = UI_DIR / "ulti.html"
DST = UI_DIR / "ulti_py.html"

OLD_CALL = "else if(G.turn!==0)timer=setTimeout(()=>{playCard(G.turn,botChoose(G.turn));advance();},700);"
NEW_CALL = "else if(G.turn!==0)timer=setTimeout(()=>{botPlay(G.turn);},700);"
OLD_BID = "const p=G.turn,a=botBidAction(p);\n      if(a.type==='bid')doBid(p,a.disc,a.ci,a.suit);else if(a.type==='pickup')doPickUp(p);else if(a.type==='concede')doConcede(p);else doPass(p);\n      advance();"
NEW_BID = "botBid(G.turn);"
OLD_KON = "timer=setTimeout(()=>{const p=G.turn;doKontra(p,botKontra(p));advance();},900);"
NEW_KON = "timer=setTimeout(()=>{botKon(G.turn);},900);"
OLD_PANEL = "if($('anBtn'))$('anBtn').onclick=()=>{REV.show=true;render();};"
NEW_PANEL = "if($('anBtn'))$('anBtn').onclick=()=>{REV.show=true;render();};pyReviewUI();"
ANCHOR = "function advance(){"

HOOK = r"""
// ===== PYTHON MOTOR HOROG (make_ui.py) =====
const PY_ENGINE=new URLSearchParams(location.search).get('engine')!=='js';
let PY_OK=null; // null: még nem próbáltuk, true/false: eredmény
const PYLOG={blog:[],klog:[],plays:[],hands:null,dealer:0};
function pyReset(){PYLOG.blog=[];PYLOG.klog=[];PYLOG.plays=[];PYLOG.hands=G.hands.map(h=>h.map(c=>c.id));PYLOG.dealer=G.dealer;PYLOG.rev=null;}
// a napló a UI saját állapotváltó függvényeinek becsomagolásával épül (a barát kódja érintetlen)
(function(){
  const _nd=newDeal,_bid=doBid,_pass=doPass,_pick=doPickUp,_con=doConcede,_kon=doKontra,_play=playCard;
  newDeal=function(d){_nd(d);pyReset();};
  doBid=function(p,disc,ci,su){PYLOG.blog.push(['bid',p,ci,su,minBidIndex(),disc.map(x=>x.id)]);_bid(p,disc,ci,su);};
  doPass=function(p){PYLOG.blog.push(['pass',p]);_pass(p);};
  doPickUp=function(p){PYLOG.blog.push(['pickup',p]);_pick(p);};
  doConcede=function(p){PYLOG.blog.push(['concede',p]);_con(p);};
  doKontra=function(p,l){const r=_kon(p,l);PYLOG.klog.push([p,l.map(o=>o.k)]);return r;};
  playCard=function(p,c){PYLOG.plays.push([p,c.id]);return _play(p,c);};
})();
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
async function pyPost(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok)throw new Error('HTTP '+r.status);
  return r.json();
}
function pyDown(e){PY_OK=false;console.warn('Python motor nem elérhető, JS bot:',e);addLog('A Python motor nem elérhető, a JS bot játszik.');}
async function pyChoose(p){
  const j=await pyPost('/api/choose',snapshot(p));
  const card=legalCards(p).find(x=>x.id===j.id);
  if(!card)throw new Error('a motor nem szabályos lapot adott');
  return card;
}
async function botPlay(p){
  const g=G,tn=G.trickNo,len=G.trick.length;
  let card=null;
  if(PY_ENGINE&&PY_OK!==false){
    try{card=await pyChoose(p);if(PY_OK!==true){PY_OK=true;addLog('Python motor aktív a botok játékához.');}}
    catch(e){pyDown(e);}
  }
  if(G!==g||G.phase!=='play'||G.turn!==p||G.trickNo!==tn||G.trick.length!==len)return; // közben új játék/lépés
  playCard(p,card||botChoose(p));advance();
}
// licit: csak a döntő saját lapjai, a licitnapló és az álló licit megy ki; a motor talonját a talon-modell becsli
function bidSnapshot(p){
  return {p,dealer:G.dealer,hand:G.hands[p].map(x=>x.id),sizes:G.hands.map(h=>h.length),
    talon:G.bid&&G.bid.seat===p?G.talon.map(x=>x.id):[],talon_n:G.talon.length,
    bid:G.bid?{ci:G.bid.ci,suit:G.bid.suit,seat:G.bid.seat}:null,passes:G.passes,
    blog:PYLOG.blog.map(e=>e[0]==='bid'&&e[1]!==p?[e[0],e[1],e[2],e[3],e[4],[]]:e)};
}
function pyBidAction(j,p){
  if(j.type==='bid'){
    const ids=new Set(j.disc),disc=G.hands[p].filter(x=>ids.has(x.id));
    if(disc.length!==2||validateBid(p,disc,j.ci,j.suit))throw new Error('a motor érvénytelen licitet adott');
    return {type:'bid',ci:j.ci,suit:j.suit,disc};
  }
  if(j.type==='pickup'&&!canPickUp(p))throw new Error('a motor nem szabályos rablást adott');
  if(!['pickup','pass','concede'].includes(j.type))throw new Error('ismeretlen licit-lépés');
  return {type:j.type};
}
async function botBid(p){
  const g=G,len=PYLOG.blog.length;
  let a=null;
  if(PY_ENGINE&&PY_OK!==false){
    try{a=pyBidAction(await pyPost('/api/bid',bidSnapshot(p)),p);if(PY_OK!==true){PY_OK=true;addLog('Python motor aktív a botok játékához.');}}
    catch(e){pyDown(e);}
  }
  if(G!==g||G.phase!=='bid'||G.turn!==p||PYLOG.blog.length!==len)return;
  a=a||botBidAction(p);
  if(a.type==='bid')doBid(p,a.disc,a.ci,a.suit);else if(a.type==='pickup')doPickUp(p);else if(a.type==='concede')doConcede(p);else doPass(p);
  advance();
}
async function botKon(p){
  const g=G,len=PYLOG.klog.length;
  let list=null;
  if(PY_ENGINE&&PY_OK!==false){
    try{
      const s=bidSnapshot(p);s.talon=p===G.decl?G.talon.map(x=>x.id):[];s.talon_n=2;
      s.bid={ci:G.ci,suit:G.trump,seat:G.decl};s.kmult=G.kmult;
      const j=await pyPost('/api/kontra',s),opts=kontraOptions(p);
      list=opts.filter(o=>j.ks.includes(o.k));
      if(PY_OK!==true){PY_OK=true;addLog('Python motor aktív a botok játékához.');}
    }catch(e){pyDown(e);}
  }
  if(G!==g||G.phase!=='kontra'||G.turn!==p||PYLOG.klog.length!==len)return;
  doKontra(p,list||botKontra(p));advance();
}
// leosztás utáni tanács a Python motortól (a tutor.py szimulációs elemzése)
function pyReviewUI(){
  const host=$('panel');if(!host||G.phase!=='result'||!PY_ENGINE||$('pyRevBox'))return;
  const box=document.createElement('div');box.id='pyRevBox';box.className='analysis';
  box.innerHTML='<h3>Tanács a Python motortól</h3><div class="row"><button class="btn ghost" id="pyRevBtn">Elemezd a játékomat (kb. 15 mp)</button></div><div id="pyRevOut"></div>';
  host.appendChild(box);
  const out=()=>$('pyRevOut'),done=PYLOG.rev;
  const show=r=>{
    const esc=t=>String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    let h=`<p class="status">${esc(r.summary)}</p>`;
    if(!r.findings.length)h+='<p class="status">A vizsgált döntéseidnél nem találtam érdemi, szignifikáns hibát.</p>';
    for(const f of r.findings){
      h+=`<div class="an-i"><h4 class="an-h">${esc(f.title)} <small>(becsült veszteség: ${f.loss.toFixed(1)} pont)</small></h4>`
        +`<p>Ezt tetted: <b>${esc(f.chosen)}</b><br>Jobb: <b>${esc(f.better)}</b></p>`
        +f.why.map(w=>`<p>• ${esc(w)}</p>`).join('')
        +f.book.map(b=>`<p class="det">Könyv (${esc(b.pages)}. o.): „${esc(b.quote.slice(0,220))}”</p>`).join('')+'</div>';
    }
    out().innerHTML=h;
  };
  if(done){$('pyRevBtn').style.display='none';show(done);return;}
  $('pyRevBtn').onclick=async()=>{
    $('pyRevBtn').disabled=true;out().innerHTML='<p class="status">A motor szimulálja a leosztást…</p>';
    try{
      const r=await pyPost('/api/review',{dealer:PYLOG.dealer,hands:PYLOG.hands,blog:PYLOG.blog,klog:PYLOG.klog,plays:PYLOG.plays,you:0});
      PYLOG.rev=r;show(r);$('pyRevBtn').style.display='none';
    }catch(e){out().innerHTML='<p class="err">Az elemzés nem sikerült ('+e.message+').</p>';$('pyRevBtn').disabled=false;}
  };
}
// ===== HOROG VÉGE =====
"""


def build() -> None:
    src = SRC.read_text(encoding="utf-8")
    for needle in (OLD_CALL, ANCHOR, OLD_BID, OLD_KON, OLD_PANEL):
        if src.count(needle) != 1:
            raise SystemExit(f"Az ulti.html szerkezete megváltozott: a horog helyét kézzel kell igazítani (make_ui.py): {needle[:50]!r}")
    out = (src.replace(OLD_CALL, NEW_CALL).replace(OLD_BID, NEW_BID).replace(OLD_KON, NEW_KON)
              .replace(OLD_PANEL, NEW_PANEL).replace(ANCHOR, HOOK + ANCHOR))
    DST.write_text(out, encoding="utf-8")
    print(f"[+] {DST.name} legenerálva ({len(out)} karakter)")


if __name__ == "__main__":
    build()

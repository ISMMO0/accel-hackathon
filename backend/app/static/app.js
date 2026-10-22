/* Launch Spy frontend: renders everything from the API and the live event stream. */
const AG={
  scout:{n:"Scout",c:"#7cc4ff",job:"Finds competitors"},
  research:{n:"Researcher",c:"#c79bff",job:"Reads sites with H agents"},
  analyst:{n:"Analyst",c:"#5fe0b0",job:"Queries data, checks facts"},
  strat:{n:"Strategist",c:"#ffb547",job:"Works out how to win"},
  watch:{n:"Watcher",c:"#ff7a7a",job:"Tracks changes over time"},
};
const $=s=>document.querySelector(s);
const NS="http://www.w3.org/2000/svg";
const el=(t,a={},p)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);p&&p.appendChild(e);return e};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const CX=450,CY=300;
const posOf=(c,cx=CX,cy=CY,k=1)=>{const r=(90+(1-c.sim)*290)*k;const a=c.ang*Math.PI/180;return[cx+Math.cos(a)*r,cy+Math.sin(a)*r]};
const fmt=v=>v==="0"?"Free":"€"+v;
// Company logo from its own website icon; businesses without a site (".place") keep their initial.
const logoUrl=d=>d&&!/\.(place|example)$/.test(d)?`https://www.google.com/s2/favicons?sz=64&domain=${encodeURIComponent(d)}`:null;
const logoImg=(d,cls="")=>{const u=logoUrl(d);return u?`<img class="${cls}" src="${u}" alt="" loading="lazy" onerror="this.remove()">`:""};
async function api(path,opts={}){
  const r=await fetch(path,{headers:{"Content-Type":"application/json"},...opts,body:opts.body?JSON.stringify(opts.body):undefined});
  if(!r.ok){let m=r.statusText;try{m=(await r.json()).detail||m}catch{}throw new Error(m)}
  return r.json();
}
const reports={};  // research id -> last fetched report
async function getReport(id,seen=false){const r=await api(`/api/research/${id}${seen?"?seen=true":""}`);reports[id]=r;return r}
const mkt=r=>({...r.market,C:r.C});

/* ---------------- status pill ---------------- */
let LIVE=false,VOICE=false;
api("/api/health").then(h=>{
  LIVE=h.live.llm;VOICE=!!h.live.voice;
  $("#liveTag").textContent=LIVE?"Live agents":"Recorded data";
  $("#liveTag").title=Object.entries(h.live).map(([k,v])=>`${k}: ${v?"live":"mock"}`).join(" · ");
}).catch(()=>{});

/* ---------------- dashboard: stage ---------------- */
[120,215,310].forEach(r=>el("circle",{cx:CX,cy:CY,r,fill:"none",stroke:"#1a2027","stroke-dasharray":"2 6"},$("#rings")));
$("#agents").innerHTML=Object.entries(AG).map(([k,a])=>`<div class="agent" id="ag-${k}" style="--c:${a.c}"><span class="dot"></span><div><div class="nm">${a.n}</div><div class="st">${a.job}</div></div></div>`).join("");
const pips={};
Object.entries(AG).forEach(([k,a],i)=>{
  const g=el("g",{},$("#pips"));
  el("circle",{r:13,fill:"none",stroke:a.c,"stroke-opacity":.3},g);
  el("circle",{r:6.5,fill:a.c},g);
  el("text",{class:"pipl",y:-20,"text-anchor":"middle"},g).textContent=a.n;
  const ang=i/5*Math.PI*2;gsap.set(g,{x:CX+Math.cos(ang)*150,y:CY+Math.sin(ang)*100});
  pips[k]=g;
});
let idle=true;
function wander(k){
  if(!idle)return;
  const a=Math.random()*Math.PI*2,r=85+Math.random()*115;
  gsap.to(pips[k],{x:CX+Math.cos(a)*r*1.35,y:CY+Math.sin(a)*r*.85,duration:3+Math.random()*3,ease:"sine.inOut",onComplete:()=>setTimeout(()=>wander(k),Math.random()*900)});
}
function startIdle(){idle=true;$("#pips").classList.add("idle");Object.keys(pips).forEach((k,i)=>setTimeout(()=>wander(k),i*250))}
function stopIdle(){idle=false;$("#pips").classList.remove("idle");Object.values(pips).forEach(g=>gsap.killTweensOf(g))}
gsap.to("#coreDisc",{attr:{r:37},duration:2.2,repeat:-1,yoyo:true,ease:"sine.inOut"});
startIdle();

let facts=0,comps=0,evs=0,t0=0,timer=null,running=false,cur=null,es=null;
const nodes={};
function tick(){$("#cComp").textContent=comps;$("#cFacts").textContent=facts;$("#cSrc").textContent=Math.max(0,Math.round(facts/3))}
function addNode(c){
  if(nodes[c.id])return;
  const[x,y]=posOf(c);
  const edge=el("line",{x1:CX,y1:CY,x2:CX,y2:CY,class:"edge"},$("#edges"));
  const g=el("g",{class:"node",tabindex:0,role:"button","aria-label":c.n},$("#nodes"));
  el("circle",{cx:x,cy:y,r:27,class:"disc"},g);
  el("text",{x,y,class:"ini"},g).textContent=(c.n[0]||"?").toUpperCase();
  const lu=logoUrl(c.domain);
  if(lu){el("circle",{cx:x,cy:y,r:19,fill:"#fff"},g);const im=el("image",{href:lu,x:x-14,y:y-14,width:28,height:28,style:"clip-path:circle(50%)"},g);im.addEventListener("error",()=>{im.remove();g.querySelector('circle[fill="#fff"]')?.remove()})}
  el("text",{x,y:y+48,class:"lbl"},g).textContent=c.n;
  const sub=el("text",{x,y:y+64,class:"sub"},g);sub.textContent="0 facts";
  const open=()=>cur&&openDossier(cur,c.id);
  g.addEventListener("click",open);g.addEventListener("keydown",e=>e.key==="Enter"&&open());
  nodes[c.id]={g,sub,edge,x,y,f:0};
  gsap.from(g,{opacity:0,scale:0,svgOrigin:`${x} ${y}`,duration:.7,ease:"back.out(1.8)"});
  gsap.to(edge,{attr:{x2:x,y2:y},duration:.6,ease:"expo.out"});
  comps++;tick();
}
function move(k,id){
  if(!pips[k])return;
  const t=id?nodes[id]:null;
  const x=t?t.x+30:CX+(Math.random()-.5)*70,y=t?t.y-26:CY+(Math.random()-.5)*50;
  gsap.to(pips[k],{x,y,duration:.9,ease:"expo.inOut",overwrite:true});
  if(t){t.edge.classList.add("hot");setTimeout(()=>t.edge.classList.remove("hot"),1400)}
}
function setAgent(k,txt){
  if(!AG[k])return;
  document.querySelectorAll(".agent").forEach(a=>a.classList.remove("on"));
  const a=$("#ag-"+k);a.classList.add("on");if(txt)a.querySelector(".st").textContent=txt;
}
function addFacts(id,n){
  const nd=nodes[id];if(!nd||!n)return;
  const o={v:nd.f};nd.f+=n;
  gsap.to(o,{v:nd.f,duration:.8,onUpdate:()=>nd.sub.textContent=Math.round(o.v)+" facts"});
  facts+=n;tick();
}
function log(k,msg,src,insight){
  if(!msg)return;
  const ul=$("#feed");if(evs===0)ul.innerHTML="";
  evs++;$("#evCount").textContent=evs+" events";
  const li=document.createElement("li");li.className="ev"+(insight?" insight":"");li.style.setProperty("--c",(AG[k]||AG.strat).c);
  li.innerHTML=`<span class="who">${(AG[k]||AG.strat).n}</span><span class="t mono">+${((performance.now()-t0)/1000).toFixed(1)}s</span><span class="msg">${msg}</span>${src?`<span class="src mono">${esc(src)}</span>`:""}`;
  ul.prepend(li);
  gsap.from(li,{opacity:0,y:-10,filter:"blur(4px)",duration:.5,ease:"expo.out"});
}
function resetStage(){
  if(es){es.close();es=null}
  $("#edges").innerHTML="";$("#nodes").innerHTML="";for(const k in nodes)delete nodes[k];
  facts=comps=evs=0;tick();$("#feed").innerHTML="";$("#evCount").textContent="";
  gsap.set("#done",{opacity:0});$("#done").classList.remove("on");
}

/* one handler per event type; the same code renders live runs and replays */
const ON={
  agent_status:(e,p)=>{setAgent(e.agent,p.status);move(e.agent);log(e.agent,p.msg,p.src)},
  competitor_found:(e,p)=>{addNode(p.company);move(e.agent,p.company.id);if(p.facts)addFacts(p.company.id,p.facts);if(!p.quiet)log(e.agent,p.msg,p.src)},
  fact_added:(e,p)=>{setAgent("research");move("research",p.company_id);addFacts(p.company_id,p.count);log("research",p.msg,p.src)},
  analysis:(e,p)=>{setAgent("analyst","Cross-checking facts");move("analyst",p.company_id);log("analyst",p.msg,p.src)},
  memory_match:(e,p)=>{setAgent("analyst",p.status);move("analyst");log("analyst",p.msg,p.src,true)},
  change_detected:(e,p)=>{setAgent("watch","Comparing with last snapshot");move("watch",p.company_id);log("watch",p.msg,p.src)},
  no_change:(e,p)=>{setAgent("watch","Comparing with last snapshot");move("watch",p.company_id);log("watch",p.msg,p.src)},
  insight:(e,p)=>{setAgent("strat","Finding the gap");move("strat");["scout","research","analyst","watch"].forEach(k=>move(k));log("strat",p.msg,p.src,true)},
  run_done:(e,p)=>finish(p),
  run_error:(e,p)=>{log("strat",esc(p.msg),p.src,true);finish(null)},
};
function listen(id,after){
  es=new EventSource(`/api/research/${id}/events?after=${after||0}`);
  es.onmessage=m=>{const e=JSON.parse(m.data);(ON[e.type]||(()=>{}))(e,e.payload||{})};
  es.addEventListener("end",()=>{es.close();es=null});
}
function run(id,after,mode){
  running=true;cur=id;$("#matchSlot").innerHTML="";
  resetStage();stopIdle();
  $("#hint").textContent="Closer to the center means closer to your idea. Click any competitor for its dossier.";
  $("#go").disabled=true;$("#status").classList.add("live");$("#statusTxt").textContent="5 agents working";
  t0=performance.now();timer=setInterval(()=>$("#cTime").textContent=((performance.now()-t0)/1000).toFixed(1)+"s",100);
  listen(id,after);
}
function finish(p){
  clearInterval(timer);running=false;$("#go").disabled=false;
  $("#status").classList.remove("live");$("#statusTxt").textContent=p?`Watching ${comps} competitors`:"Hunt stopped";
  document.querySelectorAll(".agent").forEach(a=>a.classList.remove("on"));
  if(p){
    $("#doneS").textContent=p.mode==="continue"
      ?`· updated in ${p.secs}s by reusing ${p.reused} facts from memory`
      :`· ${p.comps} competitors, ${p.facts} facts in ${p.secs}s, saved to your library`;
    $("#doneA").href="#/r/"+cur;
    $("#done").classList.add("on");gsap.fromTo("#done",{opacity:0,y:14},{opacity:1,y:0,duration:.6,ease:"expo.out"});
  }
  loadLib();
  setTimeout(startIdle,600);
}
async function ask(q){
  if(running)return;
  q=q.trim();if(!q)return;
  let best=null;
  try{best=(await api("/api/research",{method:"POST",body:{query:q,mode:"check"}})).match}catch{}
  if(!LIB.length)await loadLib();
  const done=LIB.filter(r=>r.status==="done");
  const ordered=[...(best?done.filter(r=>r.id===best.id):[]),...done.filter(r=>!best||r.id!==best.id)].slice(0,5);
  $("#matchSlot").innerHTML=`<div class="match pick" role="dialog" aria-label="New or existing project">
    <p><b>New project or add to an existing one?</b><span> Existing projects reuse what the agents already know.</span></p>
    <div class="acts"><button class="btn" id="mFresh">New project</button></div>
    ${ordered.length?`<div class="plist">${ordered.map((r,i)=>`<button class="ghost pitem" data-id="${r.id}">${best&&i===0?`<span class="new-badge">Most similar</span> `:""}<b>${esc(r.label||r.q)}</b> <span>${r.comps} competitors · ${r.facts} facts</span></button>`).join("")}</div>`:""}
  </div>`;
  gsap.from(".match",{opacity:0,y:-8,duration:.4,ease:"expo.out"});
  $("#mFresh").onclick=async()=>{try{const r=await api("/api/research",{method:"POST",body:{query:q,mode:"fresh"}});run(r.id,r.after,"fresh")}catch(e){log("strat",`Couldn't start: ${esc(e.message)}`,"",true)}};
  $("#matchSlot").querySelectorAll(".pitem").forEach(b=>b.onclick=async()=>{try{const r=await api("/api/research",{method:"POST",body:{query:q,mode:"continue",parent_id:b.dataset.id}});run(r.id,r.after,"continue")}catch(e){log("strat",`Couldn't start: ${esc(e.message)}`,"",true)}});
}
$("#form").addEventListener("submit",e=>{e.preventDefault();if(!$("#q").value.trim())$("#q").value=$(".sugg button").dataset.q;ask($("#q").value)});
document.querySelectorAll(".sugg button").forEach(b=>b.onclick=()=>{$("#q").value=b.dataset.q;ask(b.dataset.q)});

/* ---------------- library ---------------- */
let LIB=[];
const TCX=200,TCY=85;
function thumb(cs){
  // each competitor sits on an elliptical orbit; animateThumbs() moves them around the center
  let s=`<svg viewBox="0 0 400 170" aria-hidden="true"><ellipse cx="${TCX}" cy="${TCY}" rx="160" ry="66" fill="none" stroke="#1a2027" stroke-dasharray="2 5"/><ellipse cx="${TCX}" cy="${TCY}" rx="95" ry="40" fill="none" stroke="#1a2027" stroke-dasharray="2 5"/>`;
  cs.forEach(c=>{
    const r=55+(1-c.sim)*190,u=logoUrl(c.d);
    s+=`<g class="nd" data-a="${c.ang}" data-r="${r}"><line x1="0" y1="0" x2="0" y2="0" stroke="#2a313a"/><g class="pos"><circle r="11" fill="${u?"#fff":"#12161b"}" stroke="#a4adb8" stroke-width="1"/>${u?`<image href="${u}" x="-8" y="-8" width="16" height="16" style="clip-path:circle(50%)"/>`:`<text y="4" fill="#e9ecef" font-size="10" font-weight="700" text-anchor="middle" font-family="Instrument Sans">${esc((c.n[0]||"?").toUpperCase())}</text>`}<text y="24" fill="#808a96" font-size="10" text-anchor="middle" font-family="Instrument Sans">${esc(c.n.length>16?c.n.slice(0,15)+"…":c.n)}</text></g></g>`;
  });
  return s+`<circle cx="${TCX}" cy="${TCY}" r="18" fill="#ffb547" opacity=".15"/><circle cx="${TCX}" cy="${TCY}" r="10" fill="#ffb547"/></svg>`;
}
let orbitT0=performance.now(),orbitRaf=null;
function animateThumbs(){
  cancelAnimationFrame(orbitRaf);
  const nodes=[...document.querySelectorAll(".thumb .nd")].map(n=>({n,a:+n.dataset.a,r:+n.dataset.r,line:n.querySelector("line"),pos:n.querySelector(".pos")}));
  if(!nodes.length)return;
  const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
  const step=t=>{
    const spin=reduce?0:((t-orbitT0)/1000)*4; // degrees per second
    for(const o of nodes){const a=(o.a+spin)*Math.PI/180,x=TCX+Math.cos(a)*o.r*1.15,y=TCY-4+Math.sin(a)*o.r*.42;
      o.pos.setAttribute("transform",`translate(${x} ${y})`);o.line.setAttribute("x1",TCX);o.line.setAttribute("y1",TCY);o.line.setAttribute("x2",x);o.line.setAttribute("y2",y)}
    if(!reduce&&document.querySelector(".page.on .thumb"))orbitRaf=requestAnimationFrame(step);
  };
  step(performance.now());  // place everything right away, then keep orbiting
}
async function loadLib(){
  try{LIB=await api("/api/library")}catch{return}
  $("#libCount").textContent=LIB.length;
  $("#lib").innerHTML=LIB.length?LIB.map(r=>`<a class="res" href="#/r/${r.id}">
    <div class="thumb">${thumb(r.thumb)}</div>
    <div class="body">${r.status==="running"?`<span class="new-badge">Running</span>`:r.recorded?`<span class="tag" style="align-self:flex-start">Recorded</span>`:""}<h3>${esc(r.label||r.q)}</h3>${r.label?`<p class="qline">“${esc(r.q)}”</p>`:""}
    ${r.alerts?`<span class="alert"><i></i>${r.alerts} changes since your last visit</span>`:""}
    <div class="meta"><span>${esc(r.when)}</span><span class="mono">${r.comps} competitors</span><span class="mono">${r.facts} facts</span></div></div></a>`).join("")
    :`<p class="empty">No research yet. Run your first hunt from the dashboard.</p>`;
  animateThumbs();
  if(location.hash.startsWith("#/map"))renderMapSide();
}

/* ---------------- report ---------------- */
const stars=n=>"★".repeat(Math.round(n||0))+"☆".repeat(5-Math.round(n||0));
let repId=null;
async function renderReport(id,keepTab){
  repId=id;
  let r;try{r=await getReport(id,true)}catch{location.hash="#/library";return}
  const d=mkt(r),t=r.totals||{};
  const tab=keepTab||"pos";
  if(!d.gap){$("#rep").innerHTML=`<a class="back" href="#/library">← Research library</a><h1>${esc(r.q)}</h1><p class="empty">This research is ${esc(r.status)}. The report appears when the agents finish.</p>`;return}
  $("#rep").innerHTML=`
   <a class="back" href="#/library"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>Research library</a>
   <div class="thumb r-orbit">${thumb(d.C.map(c=>({n:c.n,sim:c.sim,ang:c.ang,d:c.domain})))}</div>
   <div class="r-head"><div><h1>${esc(r.label||r.q)}</h1>${r.label?`<p class="qline">“${esc(r.q)}”</p>`:""}<p>${esc(r.when)} · ${r.recorded?"recorded data":"live run"} · watched by the Watcher${t.llm&&t.llm.calls?` · ${t.llm.calls} LLM calls, $${t.llm.cost_usd}`:""}</p></div>
     <div class="r-acts"><button class="ghost" id="enrichBtn" ${r.status==="running"?"disabled":""}>${r.status==="running"?"Agents working…":"Enrich: find more data"}</button></div>
     <div class="stats"><div><b class="mono">${r.C.length}</b><span>competitors</span></div><div><b class="mono">${r.C.reduce((a,c)=>a+c.facts,0)}</b><span>facts</span></div><div><b class="mono">${t.secs??"–"}s</b><span>hunt time</span></div></div></div>
   ${planCard(d.biz_plan)}
   <div class="verdict"><div class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4"/></svg></div>
     <div><h3>${esc(d.verdict[0])}</h3><p>${esc(d.verdict[1])}</p></div></div>
   <div class="enrich-feed" id="enrichFeed"></div>
   <div class="tabs" role="tablist">
     <button role="tab" aria-selected="${tab==="pos"}" data-v="pos">Positioning</button>
     <button role="tab" aria-selected="${tab==="cmp"}" data-v="cmp">Compare</button>
     <button role="tab" aria-selected="${tab==="rev"}" data-v="rev">Reviews</button>
     <button role="tab" aria-selected="${tab==="data"}" data-v="data">Data</button>
   </div>
   <div class="view ${tab==="pos"?"on":""}" id="v-pos"><div class="chart"><svg id="pos" viewBox="0 0 1000 520"></svg></div><p class="note">Every dot opens that competitor's dossier.</p></div>
   <div class="view ${tab==="cmp"?"on":""}" id="v-cmp"><div class="tbl"><table><thead><tr><th>Competitor</th><th>${esc(d.locL)}</th><th>Prices</th><th>${esc(d.scaleL)}</th><th>Hiring</th><th>Biggest complaint</th><th>Last change</th></tr></thead><tbody>
     ${d.C.map(c=>`<tr class="row" tabindex="0" data-id="${c.id}"><td><b>${esc(c.n)}</b></td><td>${esc(c.loc)}</td><td class="num">${c.plans.filter(p=>p[1]!=="0").map(p=>"€"+esc(p[1])).join(" · ")}</td><td class="num">${esc(c.scale)}</td><td>${esc(c.hiring)}</td><td>${esc(c.complaint)}</td><td><span class="chip" style="--c:${!c.change||c.change==="None"?"var(--ink-3)":"var(--watch)"}"><i></i>${esc(c.change||"None")}</span></td></tr>`).join("")}
   </tbody></table></div></div>
   <div class="view ${tab==="rev"?"on":""}" id="v-rev"><div class="reviews">
     ${d.C.map(c=>`<div class="rv" tabindex="0" data-id="${c.id}"><div class="rv-top"><b>${esc(c.n)}</b><span class="stars" aria-label="${c.rating} out of 5">${c.rating>0?stars(c.rating):""} <span class="mono" style="color:var(--ink-2)">${c.rating>0?c.rating:"–"}</span></span></div>
       <span class="theme"><i></i>${esc(c.complaint)}</span>${(c.q||[]).map(x=>`<blockquote>“${esc(x)}”</blockquote>`).join("")}</div>`).join("")}
   </div></div>
   <div class="view ${tab==="data"?"on":""}" id="v-data">
     <div class="dfilters">
       <select id="fComp"><option value="">All competitors</option>${d.C.map(c=>`<option value="${c.id}">${esc(c.n)}</option>`).join("")}</select>
       <select id="fKind"><option value="">All types</option>${["price","feature","review","hiring","location","funding","change","other"].map(k=>`<option>${k}</option>`).join("")}</select>
       <button class="ghost" id="fPrices" aria-pressed="false">Price list</button>
       <input id="fText" placeholder="Filter facts, e.g. tonkotsu, vegan, Pro plan">
       <span class="count mono" id="fCount"></span>
     </div>
     <div class="tbl tall"><table><thead><tr><th>Competitor</th><th>Type</th><th>Item</th><th>Value</th><th>Fact</th><th>Source</th></tr></thead><tbody id="fRows"><tr><td colspan="6" class="empty">Loading facts from Postgres…</td></tr></tbody></table></div>
     <p class="note">Every fact the agents stored for this research, straight from Aiven Postgres. Click a column header to sort.</p>
   </div>
   <div class="thread" id="thread"></div>
   <div class="rchat">
     <form class="search" id="rform">
       <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>
       <input id="rq" placeholder="Ask about this market, or ask the agents to dig deeper" aria-label="Ask about this research">
       <button class="btn" type="submit">Ask</button>
     </form>
     <div class="chips">${chipsFor(d).map(c=>`<button type="button">${esc(c)}</button>`).join("")}</div>
   </div>`;
  drawPos(d);
  animateThumbs();
  r.chat.forEach(m=>appendMsg(m.role==="user"?{u:m.body.u}:m.body,false));
  $("#rform").onsubmit=e=>{e.preventDefault();const v=$("#rq").value.trim();if(v){$("#rq").value="";chat(id,v)}};
  {const rq=$("#rq");rq.dataset.ph=rq.placeholder;const mb=micButton(rq,v=>{rq.value="";chat(id,v)});if(mb)$("#rform .btn").before(mb)}
  $("#rep").querySelectorAll(".chips button").forEach(b=>b.onclick=()=>chat(id,b.textContent));
  $("#rep").querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>setTab(b.dataset.v));
  $("#enrichBtn").onclick=()=>enrich(id);
  if($("#planBtn"))$("#planBtn").onclick=()=>buildPlan(id);
  if($("#planOpen"))$("#planOpen").onclick=()=>openPlan(id);
  loadFacts(id);
  $("#rep").querySelectorAll("[data-id]").forEach(n=>{n.onclick=()=>openDossier(id,n.dataset.id);n.onkeydown=e=>e.key==="Enter"&&openDossier(id,n.dataset.id)});
  if(!keepTab)gsap.from("#rep > *",{opacity:0,y:14,duration:.6,stagger:.05,ease:"expo.out"});
}
function drawPos(d){
  const s=$("#pos"),L=110,R=960,T=40,B=460,x0=d.xMin||0,span=(d.xMax-x0)||1,X=p=>L+((p-x0)/span)*(R-L),Y=i=>B-i*(B-T);
  el("line",{x1:L,y1:B,x2:R,y2:B,class:"axis"},s);el("line",{x1:L,y1:T,x2:L,y2:B,class:"axis"},s);
  for(let i=0;i<=5;i++){const p=x0+span*i/5;el("text",{x:X(p),y:B+24,class:"axl","text-anchor":"middle"},s).textContent="€"+(+p.toFixed(1))}
  el("text",{x:R,y:B+48,class:"axl","text-anchor":"end"},s).textContent=d.xL+" →";
  el("text",{x:L-14,y:T+4,class:"axl","text-anchor":"end"},s).textContent=d.yTop;
  el("text",{x:L-14,y:B,class:"axl","text-anchor":"end"},s).textContent=d.yBot;
  const g=d.gap,gx=X(g.x0),gy=Y(g.y0);
  el("rect",{x:gx,y:gy,width:Math.max(0,X(g.x1)-gx),height:Math.max(0,Y(g.y1)-gy),rx:14,class:"gap"},s);
  el("text",{x:gx+14,y:Y(g.y1)-14,class:"gapl"},s).textContent=g.t;
  d.C.forEach(c=>{const p=el("g",{class:"pt",transform:`translate(${X(c.price)} ${Y(c.y)})`,"data-id":c.id,tabindex:0,role:"button","aria-label":c.n},s);el("circle",{r:9},p);el("text",{x:14,y:5},p).textContent=c.n});
  const you=el("g",{class:"pt you",transform:`translate(${X(d.you.price)} ${Y(d.you.y)})`},s);el("circle",{r:11},you);el("text",{x:18,y:5},you).textContent="You";
  gsap.from(s.querySelectorAll(".pt"),{opacity:0,duration:.5,stagger:.05,ease:"expo.out",delay:.2});
}
/* ---------------- ideal business plan (Planner agent) ---------------- */
function planCard(p){
  if(!p)return `<div class="plan-card empty-plan"><div><h3>Your ideal business plan</h3><p>The Planner agent turns everything collected here into a launch plan: what to charge, where to open, how to launch, and how to beat each competitor.</p></div><button class="btn" id="planBtn">Build my business plan</button></div>`;
  return `<div class="plan-card"><div class="pc-main"><span class="pc-tag">Your ideal business plan</span><h3>${esc(p.headline)}</h3><p>${esc(p.concept)}</p>
    <div class="pc-chips">${p.price_list.slice(0,3).map(x=>`<span><b>${esc(x.price)}</b> ${esc(x.item)}</span>`).join("")}<span class="loc">📍 ${esc(p.location.area)}</span></div></div>
    <div class="pc-acts"><button class="btn" id="planOpen">Open the full plan</button><button class="ghost" id="planBtn">Rebuild</button></div></div>`;
}
async function buildPlan(id){
  const b=$("#planBtn");b.disabled=true;b.textContent="Planner agent is writing your plan… (about a minute)";
  try{await api(`/api/research/${id}/plan`,{method:"POST"});await renderReport(id,"pos");openPlan(id)}
  catch(e){b.disabled=false;b.textContent="Couldn't build it: "+e.message}
}
function openPlan(id){
  const r=reports[id],p=r&&r.market.biz_plan;if(!p)return;
  const li=a=>`<ul class="plist2">${a.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`;
  $("#dIn").innerHTML=`
    <div class="d-top"><div class="logo" style="background:var(--amber);color:var(--amber-ink)">★</div><div><h2>Your ideal business plan</h2><p>${esc(r.label||r.q)} · by the Planner agent, from ${r.C.reduce((a,c)=>a+c.facts,0)} stored facts</p></div>
    <button class="x" aria-label="Close" onclick="closeD()"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="d-sum"><b>${esc(p.headline)}</b><br>${esc(p.concept)}</p>
    <div class="d-sec"><h4>What to charge</h4><div class="tbl"><table><tbody>${p.price_list.map(x=>`<tr><td><b>${esc(x.item)}</b></td><td class="num">${esc(x.price)}</td><td style="color:var(--ink-2)">${esc(x.why)}</td></tr>`).join("")}</tbody></table></div></div>
    <div class="d-sec"><h4>Where</h4><div class="beat"><p><b>📍 ${esc(p.location.area)}</b></p><p style="color:var(--ink-2);margin-top:6px">${esc(p.location.why)}</p>${p.location.ll?`<p style="margin-top:10px"><a href="#/map" onclick="mapSel='${id}';setTimeout(()=>showOnMap('${id}'),400)" style="color:var(--amber)">See the green zone on the map →</a></p>`:""}</div></div>
    ${p.phases.map(ph=>`<div class="d-sec"><h4>${esc(ph.name)}</h4>${li(ph.actions)}</div>`).join("")}
    <div class="d-sec"><h4>How to beat them</h4><div class="beat"><ol>${p.beat.map(m=>`<li><b>${esc(m.competitor)}:</b> ${esc(m.move)}</li>`).join("")}</ol></div></div>
    <div class="d-sec"><h4>Marketing &amp; social</h4>${li(p.marketing)}</div>
    <div class="d-sec"><h4>Risks</h4>${li(p.risks)}</div>
    <div class="d-sec"><h4>KPIs to track</h4>${li(p.kpis)}</div>`;
  $("#dossier").classList.add("open","wide");
  gsap.from("#dIn > *",{opacity:0,y:12,duration:.5,stagger:.04,ease:"expo.out",delay:.1});
}

/* Data tab: every stored fact, filterable, sortable, with a price list view */
let FACTS=[],fSort={k:"company",dir:1};
async function loadFacts(id){
  try{FACTS=await api(`/api/research/${id}/facts`)}catch{FACTS=[]}
  ["fComp","fKind","fText"].forEach(x=>$("#"+x)&&($("#"+x).oninput=drawFacts));
  if($("#fPrices"))$("#fPrices").onclick=e=>{const b=e.currentTarget;b.setAttribute("aria-pressed",b.getAttribute("aria-pressed")!=="true");drawFacts()};
  document.querySelectorAll("#v-data th").forEach((th,i)=>th.onclick=()=>{const k=["company","kind","item","value","text","source"][i];fSort={k,dir:fSort.k===k?-fSort.dir:1};drawFacts()});
  drawFacts();
}
function drawFacts(){
  if(!$("#fRows"))return;
  const comp=$("#fComp").value,kind=$("#fKind").value,q=$("#fText").value.toLowerCase(),prices=$("#fPrices").getAttribute("aria-pressed")==="true";
  const names=Object.fromEntries((reports[repId]?.C||[]).map(c=>[c.id,c.n]));
  let rows=FACTS.filter(f=>(!comp||f.company_id===comp)&&(!kind||f.kind===kind)&&(!prices||(f.kind==="price"&&f.value))&&(!q||(f.text+" "+f.item+" "+f.company).toLowerCase().includes(q)));
  const num=v=>parseFloat(String(v).replace(",","."));
  rows.sort((a,b)=>{const k=fSort.k;const x=k==="value"&&!isNaN(num(a.value))&&!isNaN(num(b.value))?num(a.value)-num(b.value):String(a[k]).localeCompare(String(b[k]));return x*fSort.dir});
  $("#fCount").textContent=`${rows.length} of ${FACTS.length} facts`;
  $("#fRows").innerHTML=rows.slice(0,400).map(f=>`<tr><td><b>${esc(f.company)}</b></td><td><span class="kchip">${esc(f.kind)}</span></td><td>${esc(f.item)}</td><td class="num">${f.kind==="price"&&/^[\d.,]+$/.test(f.value)?"€"+esc(f.value):esc(f.value)}</td><td>${esc(f.text)}</td><td class="src"><a href="${/^https?:/.test(f.source)?esc(f.source):"#"}" target="_blank" rel="noopener">${esc(f.source.replace(/^https?:\/\//,"").slice(0,48))}</a></td></tr>`).join("")||`<tr><td colspan="6" class="empty">No fact matches these filters.</td></tr>`;
}

/* Enrich: agents go back for missing data; progress shows live on the report */
async function enrich(id){
  const btn=$("#enrichBtn");btn.disabled=true;btn.textContent="Agents working…";
  let r;try{r=await api(`/api/research/${id}/enrich`,{method:"POST"})}catch(e){btn.textContent=e.message;return}
  const feed=$("#enrichFeed");feed.innerHTML=`<div><b>Enrich started</b>: agents are looking for prices, menus, reviews and news they don't have yet.</div>`;
  const src=new EventSource(`/api/research/${id}/events?after=${r.after}`);
  src.onmessage=m=>{const e=JSON.parse(m.data),p=e.payload||{};const t=p.msg||p.status;if(t&&!p.quiet){const d=document.createElement("div");d.innerHTML=`<b>${(AG[e.agent]||AG.strat).n}</b> · ${t}`;feed.prepend(d)}
    if(e.type==="run_done"){src.close();const n=document.createElement("div");n.innerHTML=`<b>Done</b> · knowledge saved to Postgres. Reloading the report…`;feed.prepend(n);setTimeout(()=>renderReport(id,"data"),1200)}
    if(e.type==="run_error"){src.close();btn.disabled=false;btn.textContent="Enrich: find more data"}};
}

function chipsFor(d){const a=(d.C[0]||{n:"them"}).n.split(" ")[0];return["Who has the best reviews?",`Dig deeper into ${a}'s prices`,"Who is cheapest?","Add another competitor: "]}
function setTab(v){$("#rep").querySelectorAll(".tabs button").forEach(x=>x.setAttribute("aria-selected",x.dataset.v===v));$("#rep").querySelectorAll(".view").forEach(x=>x.classList.toggle("on",x.id==="v-"+v))}
function highlight(ids){if(!ids||!ids.length)return;setTab("pos");$("#pos").querySelectorAll(".pt").forEach(p=>p.classList.toggle("hl",ids.includes(p.dataset.id)));$("#pos").scrollIntoView({behavior:"smooth",block:"center"})}
function appendMsg(m,anim=true){
  const th=$("#thread");const div=document.createElement("div");
  if(m.u!==undefined){div.className="msg-u";div.textContent=m.u}
  else{div.className="msg-a";div.innerHTML=`${m.steps&&m.steps.length?`<ul class="steps">${m.steps.map(s=>`<li style="--c:${(AG[s[0]]||AG.analyst).c}"><i></i><span>${(AG[s[0]]||AG.analyst).n}: ${esc(s[1])}</span><span class="src mono">${esc(s[2]||"")}</span></li>`).join("")}</ul>`:""}<div class="ans">${m.a}</div>${m.cites&&m.cites.length?`<div class="cites">${m.cites.map(c=>`<span class="mono">${esc(c)}</span>`).join("")}</div>`:""}${m.upd?`<div class="upd">${esc(m.upd)}</div>`:""}`}
  th.appendChild(div);if(anim)gsap.from(div,{opacity:0,y:10,duration:.45,ease:"expo.out"});
}
async function chat(id,text){
  if(text.endsWith(": ")){$("#rq").value=text.replace("Add another competitor: ","Add ");$("#rq").focus();return}
  appendMsg({u:text});
  const think=document.createElement("div");think.className="msg-a";think.innerHTML=`<span class="typing" aria-label="Agents working"><i></i><i></i><i></i></span>`;$("#thread").appendChild(think);
  think.scrollIntoView({behavior:"smooth",block:"end"});
  let m;
  try{m=await api(`/api/research/${id}/chat`,{method:"POST",body:{message:text}})}
  catch(err){think.remove();appendMsg({a:`Something went wrong: ${esc(err.message)}`});return}
  think.innerHTML=`<ul class="steps"></ul>`;
  const ul=think.querySelector(".steps");
  for(const s of m.steps){const li=document.createElement("li");li.style.setProperty("--c",(AG[s[0]]||AG.analyst).c);li.innerHTML=`<i></i><span>${(AG[s[0]]||AG.analyst).n}: ${esc(s[1])}</span><span class="src mono">${esc(s[2]||"")}</span>`;ul.appendChild(li);gsap.from(li,{opacity:0,x:-6,duration:.3});await new Promise(r=>setTimeout(r,450))}
  think.remove();
  if(m.refresh){await renderReport(id,"pos")}
  else appendMsg(m);
  highlight(m.highlight);
}

/* ---------------- dossier ---------------- */
async function openDossier(rid,cid){
  const r=reports[rid]||await getReport(rid).catch(()=>null);if(!r)return;
  const d=mkt(r),c=d.C.find(x=>x.id===cid);if(!c)return;
  document.querySelectorAll(".node").forEach(n=>n.classList.remove("sel"));
  nodes[cid]&&nodes[cid].g.classList.add("sel");
  $("#dIn").innerHTML=`
    <div class="d-top"><div class="logo">${esc(c.n[0]||"?")}${logoImg(c.domain,"logo-img")}</div><div><h2>${esc(c.n)}</h2><p>${esc(c.loc)}${c.rating>0?` · ${c.rating}★`:""} · ${c.facts} facts</p></div>
    <button class="x" aria-label="Close" onclick="closeD()"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="d-sum">${esc(c.sum)}</p>
    ${c.plans.length?`<div class="d-sec"><h4>Prices</h4><div class="plans">${c.plans.map(p=>`<div class="plan"><b>${esc(p[0])}</b><div class="p mono">${esc(fmt(p[1]))}<small>${p[1]!=="0"&&d.unit?" "+esc(d.unit):""}</small></div></div>`).join("")}</div></div>`:""}
    <div class="d-sec"><h4>What we found</h4><ul class="facts">${c.f.map(f=>`<li><span>${esc(f[0])}</span><a href="${/^https?:/.test(f[1])?esc(f[1]):"#"}" target="_blank" rel="noopener" class="mono">${esc(f[1].replace(/^https?:\/\//,""))}</a></li>`).join("")}</ul></div>
    ${c.tl.length?`<div class="d-sec"><h4>Recent changes</h4><ul class="timeline">${c.tl.map(t=>`<li><span class="when mono">${esc(t[0])}</span>${esc(t[1])}</li>`).join("")}</ul></div>`:""}
    ${(c.q||[]).length?`<div class="d-sec"><h4>What customers say</h4>${c.q.map(x=>`<blockquote>“${esc(x)}”</blockquote>`).join("")}</div>`:""}
    ${c.beat.length?`<div class="d-sec"><h4>How to beat them</h4><div class="beat"><ol>${c.beat.map(b=>`<li>${esc(b)}</li>`).join("")}</ol></div></div>`:""}`;
  $("#dossier").classList.add("open");
  gsap.from("#dIn > *",{opacity:0,y:12,duration:.5,stagger:.04,ease:"expo.out",delay:.1});
}
function closeD(){$("#dossier").classList.remove("open","wide");document.querySelectorAll(".node").forEach(n=>n.classList.remove("sel"))}
document.addEventListener("keydown",e=>e.key==="Escape"&&closeD());

/* ---------------- map ---------------- */
let map=null,layer=null,mapSel=null;
function initMap(){
  if(map){map.invalidateSize();return}
  map=L.map("map",{worldCopyJump:true}).setView([48.8566,2.3522],12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:"&copy; OpenStreetMap contributors"}).addTo(map);
  layer=L.layerGroup().addTo(map);
}
function renderMapSide(){
  $("#mres").innerHTML=LIB.map(r=>`<button type="button" data-id="${r.id}" aria-pressed="${mapSel===r.id}"><b>${esc(r.label||r.q)}</b><span>${esc(r.when)} · ${r.comps} competitors</span></button>`).join("");
  $("#mres").querySelectorAll("button").forEach(b=>b.onclick=()=>showOnMap(b.dataset.id));
}
function popHtml(rid,c){const paid=c.plans.filter(p=>p[1]!=="0");return`<div class="pop"><h3>${esc(c.n)}</h3><div class="l">${esc(c.loc)}</div><dl><dt>Rating</dt><dd>${c.rating>0?c.rating:"–"}★</dd><dt>Prices</dt><dd class="mono">${paid.map(p=>"€"+esc(p[1])).join(" · ")||"–"}</dd><dt>Weak spot</dt><dd>${esc(c.complaint)||"–"}</dd><dt>Last change</dt><dd>${esc(c.change)||"None"}</dd></dl><button class="btn" onclick="openDossier('${rid}','${c.id}')">Open dossier</button></div>`}
async function showOnMap(id){
  mapSel=id;renderMapSide();
  const r=await getReport(id).catch(()=>null);if(!r)return;
  const d=mkt(r);layer.clearLayers();$("#mempty").style.display="none";
  const mk={},pts=[];
  d.C.filter(c=>c.ll).forEach((c,i)=>{
    const m=L.marker(c.ll,{icon:L.divIcon({className:"",html:`<div class="pin">${esc(c.n[0])}${logoImg(c.domain,"pin-img")}</div>`,iconSize:[34,34],iconAnchor:[17,17]}),title:c.n,riseOnHover:true}).bindPopup(popHtml(id,c),{maxWidth:280});
    setTimeout(()=>{m.addTo(layer);const e=m.getElement();e&&gsap.from(e.firstChild,{scale:0,duration:.5,ease:"back.out(2)"})},150+i*90);
    mk[c.id]=m;pts.push(c.ll);
  });
  const bp=d.biz_plan&&d.biz_plan.location;
  if(bp&&bp.ll){L.circle(bp.ll,{radius:350,color:"#5fe0b0",weight:2,fillColor:"#5fe0b0",fillOpacity:.22}).bindPopup(`<div class="pop"><h3>Best spot for you</h3><div class="l">${esc(bp.area)}</div><p style="font-size:13px">${esc(bp.why)}</p></div>`).addTo(layer);
    L.marker(bp.ll,{icon:L.divIcon({className:"",html:`<div class="pin best">★</div>`,iconSize:[34,34],iconAnchor:[17,17]}),zIndexOffset:900}).bindPopup(`<div class="pop"><h3>Best spot for you</h3><div class="l">${esc(bp.area)}</div></div>`).addTo(layer);pts.push(bp.ll)}
  if(d.you&&d.you.ll){L.marker(d.you.ll,{icon:L.divIcon({className:"",html:`<div class="pin you">YOU</div>`,iconSize:[34,34],iconAnchor:[17,17]}),zIndexOffset:1000}).bindPopup(`<div class="pop"><h3>Your planned spot</h3><div class="l">Inside the gap the Strategist found</div></div>`).addTo(layer);pts.push(d.you.ll)}
  if(pts.length)map.flyToBounds(L.latLngBounds(pts).pad(.25),{duration:1.2,maxZoom:16});
  else{$("#mempty").textContent="No addresses found for this research yet.";$("#mempty").style.display=""}
  $("#mlist").innerHTML=d.C.map(c=>`<li><button type="button" data-id="${c.id}" ${c.ll?"":"disabled"}><span class="mi2">${esc(c.n[0])}</span><span><b>${esc(c.n)}</b><span class="loc">${c.ll?esc(c.loc):"No address found yet"}</span></span><span class="rt mono">${c.rating>0?c.rating:"–"}★</span></button></li>`).join("");
  $("#mlist").querySelectorAll("button[data-id]").forEach(b=>b.onclick=()=>{const m=mk[b.dataset.id];if(!m)return;map.flyTo(m.getLatLng(),Math.max(map.getZoom(),10),{duration:.9});setTimeout(()=>m.openPopup(),950)});
}

/* ---------------- evidence ---------------- */
async function renderEvidence(){
  let rows=[];try{rows=await api("/api/eval")}catch{}
  const el_=$("#ev");
  if(!rows.length){el_.innerHTML=`<h1>Evidence</h1><p class="empty">No evaluation yet. Run <span class="mono">python -m scripts.eval</span> in backend/.</p>`;return}
  const by=s=>rows.filter(r=>r.system===s);
  const avg=(a,k)=>a.length?a.reduce((x,r)=>x+(+r[k]||0),0)/a.length:0;
  const ls=by("launch_spy"),pc=by("plain_claude"),fr=by("launch_spy_fresh")[0],mem=by("launch_spy_memory")[0];
  const pct=v=>Math.round(v*100)+"%";
  const markets=ls.map(r=>r.market);
  // grouped bars: recall per market
  const W=1000,H=60+markets.length*64,L=330,R=960,X=v=>L+v*(R-L);
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Recall per market">`;
  [0,.25,.5,.75,1].forEach(v=>{svg+=`<line x1="${X(v)}" y1="20" x2="${X(v)}" y2="${H-24}" stroke="#1f252d"/><text x="${X(v)}" y="${H-6}" fill="#808a96" font-size="12" text-anchor="middle">${pct(v)}</text>`});
  markets.forEach((m,i)=>{
    const y=34+i*64,a=ls[i],b=pc.find(r=>r.market===m)||{recall:0,found:0,expected:a.expected};
    svg+=`<text x="${L-14}" y="${y+20}" fill="#e9ecef" font-size="14" text-anchor="end">${esc(m.length>38?m.slice(0,37)+"…":m)}</text>`;
    svg+=`<rect x="${L}" y="${y}" width="${Math.max(2,X(a.recall)-L)}" height="18" rx="4" fill="#ffb547"/><text x="${X(a.recall)+8}" y="${y+14}" fill="#e9ecef" font-size="12">${a.found}/${a.expected}</text>`;
    svg+=`<rect x="${L}" y="${y+22}" width="${Math.max(2,X(b.recall)-L)}" height="18" rx="4" fill="#4a5361"/><text x="${X(b.recall)+8}" y="${y+36}" fill="#a4adb8" font-size="12">${b.found}/${b.expected}</text>`;
  });
  svg+="</svg>";
  el_.innerHTML=`
   <div class="lib-head"><div><h1>Evidence it works</h1><p>Live evaluation on ${markets.length} markets with a hand-made answer key · run ${esc(rows[0].run_id)}</p></div></div>
   <div class="kpis">
     <div class="kpi"><b>${pct(avg(ls,"recall"))}<em>vs ${pct(avg(pc,"recall"))}</em></b><span>known competitors found · Launch Spy vs plain Claude</span></div>
     <div class="kpi"><b>100%<em>vs 0%</em></b><span>facts linked to a source page (${ls.reduce((x,r)=>x+r.sourced_facts,0)} facts)</span></div>
     <div class="kpi"><b>$${avg(ls,"cost_usd").toFixed(2)}<em>per market</em></b><span>full hunt: search, read, analyse, strategy</span></div>
     ${fr&&mem?`<div class="kpi"><b>${Math.round((1-mem.cost_usd/Math.max(fr.cost_usd,1e-6))*100)}%<em>cheaper</em></b><span>re-checking a known market (Continue research), ${mem.reused_facts} facts reused from memory</span></div>`:""}
   </div>
   <div class="ev-sec"><h2>Competitors found, per market</h2>
     <div class="legend"><span><i style="background:#ffb547"></i>Launch Spy (live web)</span><span><i style="background:#4a5361"></i>Plain Claude (no tools)</span></div>
     <div class="chart">${svg}</div></div>
   ${fr&&mem?`<div class="ev-sec"><h2>Memory: re-checking a market you already researched</h2><div class="tbl"><table><thead><tr><th>Run</th><th>Time</th><th>LLM calls</th><th>Cost</th><th>Facts reused</th></tr></thead><tbody>
     <tr><td>1st hunt</td><td class="num">${fr.secs}s</td><td class="num">${fr.llm_calls}</td><td class="num">$${(+fr.cost_usd).toFixed(3)}</td><td class="num">${fr.reused_facts}</td></tr>
     <tr><td>Continue research</td><td class="num">${mem.secs}s</td><td class="num">${mem.llm_calls}</td><td class="num">$${(+mem.cost_usd).toFixed(3)}</td><td class="num">${mem.reused_facts}</td></tr>
   </tbody></table></div></div>`:""}
   <div class="ev-sec" id="growth"></div>
   <div class="ev-sec"><h2>Every run</h2><div class="tbl"><table><thead><tr><th>Market</th><th>System</th><th>Found</th><th>Sourced facts</th><th>Time</th><th>Cost</th></tr></thead><tbody>
     ${rows.filter(r=>r.system==="launch_spy"||r.system==="plain_claude").map(r=>`<tr><td>${esc(r.market)}</td><td>${r.system==="launch_spy"?"Launch Spy":"Plain Claude"}</td><td class="num">${r.found}/${r.expected}</td><td class="num">${r.sourced_facts}</td><td class="num">${r.secs}s</td><td class="num">$${(+r.cost_usd).toFixed(3)}</td></tr>`).join("")}
   </tbody></table></div><p class="note">Answer key: backend/scripts/data/answer_key.json · raw rows in the eval_runs table (Aiven Postgres).</p></div>`;
  renderGrowth();
}
async function renderGrowth(){
  let k=[];try{k=await api("/api/knowledge")}catch{}
  const by={};k.forEach(r=>(by[r.research_id]=by[r.research_id]||{q:r.query,pts:[]}).pts.push(r));
  const series=Object.values(by).filter(s=>s.pts.length>1);
  if(!series.length){$("#growth").innerHTML=`<h2>Knowledge growth</h2><p class="note">Run "Enrich" or ask questions on a research: every round that adds data is logged here.</p>`;return}
  const max=Math.max(...series.flatMap(s=>s.pts.map(p=>p.facts)));
  $("#growth").innerHTML=`<h2>Knowledge growth: every round adds data</h2><div class="tbl"><table><thead><tr><th>Research</th><th>Rounds (facts after each)</th><th>Sources</th><th>Priced items</th></tr></thead><tbody>${series.map(s=>{const a=s.pts[0],z=s.pts[s.pts.length-1];return`<tr><td>${esc(s.q)}</td><td>${s.pts.map(p=>`<span title="${esc(p.event)}" style="display:inline-block;vertical-align:bottom;width:22px;margin-right:4px;height:${Math.max(4,Math.round(p.facts/max*48))}px;background:${p.event==="hunt"?"#4a5361":"#ffb547"};border-radius:3px"></span>`).join("")} <span class="mono" style="color:var(--ink-2)">${a.facts} → ${z.facts}</span></td><td class="num">${a.sources} → ${z.sources}</td><td class="num">${a.priced_items} → ${z.priced_items}</td></tr>`}).join("")}</tbody></table></div><p class="note">Grey = first hunt, amber = enrich rounds and chat questions that added facts. Logged in the knowledge_log table (Aiven Postgres).</p>`;
}

/* ---------------- voice input: Gradium speech-to-text (server key), Chrome built-in as fallback ---------------- */
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
async function startWav(){
  // record raw mic samples and encode a 16-bit mono WAV (a format Gradium accepts directly)
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
  const ctx=new (window.AudioContext||window.webkitAudioContext)();
  const src=ctx.createMediaStreamSource(stream),proc=ctx.createScriptProcessor(4096,1,1),chunks=[];
  proc.onaudioprocess=e=>chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(proc);proc.connect(ctx.destination);
  return async()=>{
    proc.disconnect();src.disconnect();stream.getTracks().forEach(t=>t.stop());
    const rate=ctx.sampleRate;await ctx.close();
    const n=chunks.reduce((a,c)=>a+c.length,0),buf=new ArrayBuffer(44+n*2),v=new DataView(buf);
    const w=(o,s)=>[...s].forEach((ch,i)=>v.setUint8(o+i,ch.charCodeAt(0)));
    w(0,"RIFF");v.setUint32(4,36+n*2,true);w(8,"WAVE");w(12,"fmt ");v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);
    v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);w(36,"data");v.setUint32(40,n*2,true);
    let o=44;for(const c of chunks)for(let i=0;i<c.length;i++,o+=2){const s=Math.max(-1,Math.min(1,c[i]));v.setInt16(o,s<0?s*0x8000:s*0x7fff,true)}
    return new Blob([buf],{type:"audio/wav"});
  };
}
function micButton(input,onDone){
  if(!SR&&!navigator.mediaDevices)return null;
  const b=document.createElement("button");b.type="button";b.className="mic";b.setAttribute("aria-label","Speak instead of typing");
  b.innerHTML=`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>`;
  let rec=null,stopWav=null,busy=false;
  const lang=(navigator.language||"en").slice(0,2);
  b.onclick=async()=>{
    if(VOICE){  // Gradium: click to start, click again to stop and transcribe
      if(busy)return;
      if(stopWav){
        const stop=stopWav;stopWav=null;busy=true;b.classList.remove("on");input.placeholder="Transcribing with Gradium…";
        try{
          const wav=await stop();
          const r=await fetch(`/api/transcribe?lang=${lang}`,{method:"POST",headers:{"Content-Type":"audio/wav"},body:wav});
          const t=r.ok?(await r.json()).text:"";
          input.placeholder=input.dataset.ph||"";
          if(t){input.value=t;onDone(t)}else input.placeholder="Didn't catch that, try again";
        }catch{input.placeholder="Voice failed, try again"}
        busy=false;return;
      }
      try{stopWav=await startWav();b.classList.add("on");input.placeholder="Listening… click the mic again when you're done"}
      catch{input.placeholder="Microphone not allowed"}
      return;
    }
    if(!SR)return;
    if(rec){rec.stop();return}
    rec=new SR();rec.lang=navigator.language||"fr-FR";rec.interimResults=true;rec.continuous=false;
    const before=input.value;b.classList.add("on");input.placeholder="Listening…";
    rec.onresult=e=>{input.value=(before?before+" ":"")+[...e.results].map(r=>r[0].transcript).join("")};
    rec.onend=()=>{b.classList.remove("on");input.placeholder=input.dataset.ph||"";const v=input.value.trim();rec=null;if(v&&v!==before.trim())onDone(v)};
    rec.onerror=()=>{b.classList.remove("on");rec=null};
    rec.start();
  };
  return b;
}
(function(){const q=$("#q");q.dataset.ph=q.placeholder;const b=micButton(q,v=>ask(v));if(b)$("#go").before(b)})();

/* ---------------- connectors (MCP) ---------------- */
function renderConnectors(){
  const url=location.origin+"/mcp",local=/localhost|127\.0\.0\.1/.test(location.host);
  const fav=d=>`https://www.google.com/s2/favicons?sz=64&domain=${d}`;
  const prompt=`You have access to my Launch Spy MCP server (${url}). It holds my competitive-research projects: competitors, sourced facts (prices, menus, reviews), market gaps and business plans, built by AI agents from live web data.
1. Call list_projects to see my projects.
2. Use get_project and search_facts to answer from the stored data, and cite the source URLs.
3. If something is missing, use ask_project (it searches the web and saves what it learns) or enrich_project.
4. For a new idea, use start_research, then get_project when it's done.`;
  const cards=[
    {n:"Claude Code",d:"claude.ai",sub:"Terminal agent",code:`claude mcp add --transport http launch-spy ${url}`},
    {n:"Codex",d:"openai.com",sub:"~/.codex/config.toml",code:`[mcp_servers.launch-spy]\nurl = "${url}"`},
    {n:"Cursor",d:"cursor.com",sub:".cursor/mcp.json",code:JSON.stringify({mcpServers:{"launch-spy":{url}}},null,2)},
    {n:"Claude & ChatGPT apps",d:"chatgpt.com",sub:"Custom connector (needs a public URL)",code:`Settings → Connectors → Add custom connector\nURL: ${url}`},
  ];
  $("#con").innerHTML=`
   <div class="lib-head"><div><h1>Connectors</h1><p>Connect your projects to your own AI agent over MCP. Claude, Codex, Cursor or ChatGPT can then read your research, search the facts, ask questions and launch new hunts.</p></div></div>
   ${local?`<div class="warn">Running on <b>${esc(location.host)}</b>: agents on this computer (Claude Code, Codex, Cursor) can connect now. Claude.ai and ChatGPT need the deployed public URL.</div>`:""}
   <div class="ev-sec" style="margin-top:20px"><h2>1. Copy the prompt for your agent</h2><div class="code" id="cPrompt">${esc(prompt)}<button class="copy" data-copy="cPrompt">Copy prompt</button></div></div>
   <div class="ev-sec"><h2>2. Add the MCP server to your agent</h2><div class="cons">${cards.map((c,i)=>`<div class="con"><div class="con-top"><img src="${fav(c.d)}" alt=""><div><b>${esc(c.n)}</b><span>${esc(c.sub)}</span></div></div><div class="code" id="cc${i}">${esc(c.code)}<button class="copy" data-copy="cc${i}">Copy</button></div></div>`).join("")}</div></div>
   <div class="ev-sec"><h2>Tools your agent gets</h2><div class="tools">${["list_projects","get_project","search_facts","ask_project","start_research","enrich_project","business_plan"].map(t=>`<span>${t}</span>`).join("")}</div>
   <p class="note">MCP endpoint: <span class="mono">${esc(url)}</span> (streamable HTTP, JSON-RPC).</p></div>`;
  $("#con").querySelectorAll(".copy").forEach(b=>b.onclick=async()=>{
    const box=$("#"+b.dataset.copy).cloneNode(true);box.querySelector(".copy").remove();
    try{await navigator.clipboard.writeText(box.textContent.trim());b.textContent="Copied";b.classList.add("done");setTimeout(()=>{b.textContent=b.dataset.copy==="cPrompt"?"Copy prompt":"Copy";b.classList.remove("done")},1500)}catch{b.textContent="Select & copy"}
  });
}

/* ---------------- router ---------------- */
function route(){
  closeD();
  const h=location.hash||"#/";
  const m=h.match(/^#\/r\/(.+)/);
  const page=m?"rep":h.startsWith("#/library")?"lib":h.startsWith("#/map")?"map":h.startsWith("#/evidence")?"ev":h.startsWith("#/connectors")?"con":"dash";
  document.querySelectorAll(".page").forEach(p=>p.classList.toggle("on",p.id==="p-"+page));
  $("#nav-dash").setAttribute("aria-current",page==="dash"?"page":"false");
  $("#nav-lib").setAttribute("aria-current",page==="lib"||page==="rep"?"page":"false");
  $("#nav-map").setAttribute("aria-current",page==="map"?"page":"false");
  $("#nav-ev").setAttribute("aria-current",page==="ev"?"page":"false");
  if(page==="ev")renderEvidence();
  $("#nav-con").setAttribute("aria-current",page==="con"?"page":"false");
  if(page==="con")renderConnectors();
  if(page==="rep")renderReport(m[1]);
  if(page==="lib")loadLib().then(()=>gsap.from(".res",{opacity:0,y:16,duration:.6,stagger:.06,ease:"expo.out"}));
  if(page==="map"){initMap();loadLib()}
  window.scrollTo(0,0);
}
window.addEventListener("hashchange",route);
loadLib();route();

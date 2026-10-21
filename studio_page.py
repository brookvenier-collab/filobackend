"""The Filo Studio page. Plain and functional, in Filo's colours. Everything shown is escaped."""

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Filo Studio</title>
<style>
:root{--bone:#f8f5f0;--ink:#1c1a18;--stone:#6b625b;--taupe:#a89c90;--greige:#e6dfd6;--ox:#6e1f24;--ok:#3d5a40}
*{box-sizing:border-box}
body{margin:0;background:var(--bone);color:var(--ink);font:14px/1.45 -apple-system,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 16px 60px}
h1{font:400 30px Georgia,serif;margin:0}
h2{font:400 22px Georgia,serif;margin:0 0 10px}
h3{font-size:12px;letter-spacing:1.6px;text-transform:uppercase;color:var(--stone);margin:18px 0 8px;font-weight:600}
a{color:var(--ox)}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:16px 0 18px}
.tabs button{background:transparent;color:var(--stone);border:1px solid var(--greige)}
.tabs button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
input,select,textarea,button{font:inherit}
input[type=text],input[type=password],input[type=number],select,textarea{border:1px solid var(--greige);background:#fff;border-radius:10px;padding:7px 10px}
textarea{width:100%;min-height:60px}
button{border:0;border-radius:999px;padding:7px 14px;background:var(--ink);color:#fff;cursor:pointer}
button.ghost{background:#fff;color:var(--ink);border:1px solid var(--greige)}
button.warn{background:var(--ox)}
button:disabled{opacity:.45;cursor:default}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0}
.panel{background:#fff;border:1px solid var(--greige);border-radius:16px;padding:16px;margin-bottom:14px;overflow-x:auto}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--stone);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--greige)}
td{padding:7px 8px;border-bottom:1px solid var(--greige);vertical-align:top}
tr.click{cursor:pointer}tr.click:hover td{background:#fbf9f6}
.pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;background:var(--greige);color:var(--ink);white-space:nowrap}
.pill.live{background:#dfe8dd;color:var(--ok)}.pill.in_review{background:#f3e7d3}.pill.hidden_stale,.pill.hold{background:#f1dcdc;color:var(--ox)}
.pill.pass{background:#dfe8dd;color:var(--ok)}.pill.conditional{background:#f3e7d3}
.muted{color:var(--stone)}.small{font-size:12px}
.msg{min-height:18px;margin:6px 0;color:var(--stone)}.msg.err{color:var(--ox)}
.quote{font-style:italic;color:var(--stone)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.tile{background:#fff;border:1px solid var(--greige);border-radius:14px;padding:14px}
.tile .n{font:400 30px Georgia,serif}
.chip{display:inline-flex;gap:6px;align-items:center;background:var(--bone);border:1px solid var(--greige);border-radius:999px;padding:2px 4px 2px 10px;margin:2px;font-size:12px}
.chip.removed{text-decoration:line-through;opacity:.5}
.chip button{padding:0 7px;background:transparent;color:var(--stone);font-size:14px}
.problems{background:#f8eeee;border:1px solid #ecd2d2;border-radius:12px;padding:10px 12px;color:var(--ox);margin:8px 0}
.note{background:#f6f1e6;border:1px solid #eadfca;border-radius:12px;padding:10px 12px;margin:8px 0}
.back{margin-bottom:10px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.split{grid-template-columns:1fr}td,th{padding:6px 4px}}
#login{max-width:360px;margin:12vh auto}
</style></head><body><div class="wrap">

<div id="login" hidden>
  <div class="small muted" style="letter-spacing:2px">FILO</div>
  <h1>Studio</h1>
  <p class="muted" id="loginNote">Type the Studio password.</p>
  <div class="row"><input type="password" id="pw" autocomplete="current-password" style="flex:1"><button id="go">Sign in</button></div>
  <div class="msg" id="loginMsg"></div>
</div>

<div id="app" hidden>
  <div class="top"><div><div class="small muted" style="letter-spacing:2px">FILO</div><h1>Studio</h1></div>
    <button class="ghost" id="out">Sign out</button></div>
  <div class="tabs" id="tabs"></div>
  <div class="msg" id="msg"></div>
  <div id="view"></div>
</div>

<script>
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl=u=>/^https?:\/\//i.test(u||'')?esc(u):'';
const link=(u,t)=>safeUrl(u)?`<a href="${safeUrl(u)}" target="_blank" rel="noopener noreferrer">${esc(t||u)}</a>`:esc(t||u||'');
const words=s=>esc(String(s??'').replace(/_/g,' '));
const money=(a,c)=>a==null?'':`${c==='CAD'?'C$':c==='USD'?'US$':c==='GBP'?'£':c==='EUR'?'€':(c||'')+' '}${Number(a).toFixed(2)}`;
const when=t=>t?new Date(t).toLocaleString():'';
const SOURCE={verified:'Checked',brand_reported:'Brand says',customer_reported:'Buyers say',inferred:"Filo's read"};
const STATUS={confirmed:'Confirmed',from_photo:'From the photos',unknown:'Unknown',conflicts:'Conflicts'};
const DECISION={pass:'Pass',conditional:'Conditional',hold:'Hold',insufficient_evidence:'Not enough evidence'};
const PSTATUS={draft:'Draft',in_review:'Needs review',live:'Live',hidden_stale:'Hidden: page failing',hidden_out_of_stock:'Hidden: sold out',retired:'Retired'};

function say(t,err){const m=$('#msg');m.textContent=t||'';m.className='msg'+(err?' err':'');}
async function api(path,body){
  const opt={credentials:'same-origin',headers:{'X-Studio':'1'}};
  if(body!==undefined){opt.method='POST';opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body);}
  const r=await fetch(path,opt);
  let d={};try{d=await r.json();}catch(e){}
  if(r.status===401&&!path.endsWith('/login')){showLogin();throw new Error('Sign in again.');}
  if(!r.ok)throw new Error(d.detail||d.message||('Something went wrong ('+r.status+')'));
  return d;
}
async function act(fn,done){say('Working…');try{const r=await fn();say(done||'Saved.');return r;}catch(e){say(e.message,true);throw e;}}

function showLogin(note){$('#app').hidden=true;$('#login').hidden=false;if(note)$('#loginNote').textContent=note;$('#pw').focus();}
async function boot(){
  const s=await fetch('/studio/session',{credentials:'same-origin'}).then(r=>r.json());
  if(!s.enabled){showLogin('Studio is off. Add STUDIO_PASSWORD in Railway to switch it on.');$('#go').disabled=true;return;}
  if(!s.signed_in){showLogin();return;}
  $('#login').hidden=true;$('#app').hidden=false;tab(location.hash.slice(1)||'products');
}
$('#go').onclick=async()=>{
  $('#loginMsg').textContent='';
  try{await api('/studio/login',{password:$('#pw').value});$('#pw').value='';boot();}
  catch(e){$('#loginMsg').textContent=e.message;$('#loginMsg').className='msg err';}
};
$('#pw').onkeydown=e=>{if(e.key==='Enter')$('#go').click();};
$('#out').onclick=async()=>{await api('/studio/logout',{});showLogin('Signed out.');};

const TABS=[['hunt','Test hunt'],['products','Products'],['brands','Brands'],['standards','Standards'],['vocabulary','Vocabulary'],['backlog','Backlog'],['health','Health'],['sources','Sources']];
let curTab='';
function tab(name,arg){
  if(!TABS.find(t=>t[0]===name))name='products';
  curTab=name;if(location.hash.slice(1)!==name)history.replaceState(null,'','#'+name);say('');
  $('#tabs').innerHTML=TABS.map(([k,l])=>`<button class="${k===name?'on':''}" data-t="${k}">${l}</button>`).join('');
  document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>tab(b.dataset.t));
  $('#view').innerHTML='<p class="muted">Loading…</p>';
  ({hunt:viewHunt,products:viewProducts,brands:viewBrands,standards:viewStandards,vocabulary:viewVocab,backlog:viewBacklog,health:viewHealth,sources:viewSources})[name](arg).catch(e=>say(e.message,true));
}

/* ---------------- products ---------------- */
let pFilter={status:'',q:''};
async function viewProducts(){
  const d=await api(`/studio/api/products?status=${encodeURIComponent(pFilter.status)}&q=${encodeURIComponent(pFilter.q)}`);
  const counts=d.by_status||{};
  const chips=[['','All'],...Object.keys(PSTATUS).map(k=>[k,PSTATUS[k]]),['rules','Approved by the rules'],['spot','Spot checks']].map(([k,l])=>
    `<button class="${pFilter.status===k?'':'ghost'}" data-s="${k}">${esc(l)}${k&&counts[k]?' ('+counts[k]+')':''}</button>`).join('');
  $('#view').innerHTML=`
  <div class="panel"><h2>Import a product</h2>
    <p class="muted small">Paste the link to one product page. It's saved for review; nothing goes live until you confirm it.</p>
    <div class="row"><input type="text" id="url" placeholder="https://" style="flex:1;min-width:240px"><button id="imp">Import</button></div>
    <div class="msg" id="impMsg"></div></div>
  <div class="row">${chips}<input type="text" id="pq" placeholder="Search" value="${esc(pFilter.q)}"></div>
  <div class="panel" style="padding:6px 8px">${d.products.length?`<table><tr><th>Product</th><th>Status</th><th>Decision</th><th>Price</th><th>Checked</th></tr>
  ${d.products.map(p=>`<tr class="click" data-id="${esc(p.product_id)}"><td><b>${esc(p.brand)}</b> ${esc(p.name)}${p.unique_find?' <span class="pill">Unique find</span>':''}<div class="small muted">${words(p.category)}${p.retailer?' · '+esc(p.retailer):''}${p.brand_approved?'':' · brand not approved'}</div></td>
    <td><span class="pill ${esc(p.status)}">${esc(PSTATUS[p.status]||p.status)}</span></td>
    <td>${p.decision?`<span class="pill ${esc(p.decision)}">${esc(DECISION[p.decision])}</span>`:''}<div class="small muted">${p.decided_by==='brooklyn'?'Confirmed by you':p.decided_by==='rules'?'Approved by the rules':'Suggested'}</div>${p.spot_check?'<span class="pill in_review">Spot check</span>':''}</td>
    <td>${money(p.price,p.currency)}${p.price_cad&&p.currency!=='CAD'?`<div class="small muted">${money(p.price_cad,'CAD')}</div>`:''}</td>
    <td class="small muted">${when(p.last_checked)}</td></tr>`).join('')}</table>`:'<p class="muted">No products yet.</p>'}</div>`;
  document.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{pFilter.status=b.dataset.s;viewProducts();});
  $('#pq').onchange=e=>{pFilter.q=e.target.value;viewProducts();};
  document.querySelectorAll('tr[data-id]').forEach(r=>r.onclick=()=>viewProduct(r.dataset.id));
  $('#imp').onclick=async()=>{
    const m=$('#impMsg');m.className='msg';m.textContent='Reading the page… this can take up to a minute.';$('#imp').disabled=true;
    try{const r=await api('/studio/api/import',{url:$('#url').value});m.textContent=r.message;m.className='msg'+(r.saved?'':' err');
      if(r.saved){$('#url').value='';setTimeout(()=>viewProduct(r.product_id),600);}}
    catch(e){m.textContent=e.message;m.className='msg err';}
    $('#imp').disabled=false;
  };
}

function factValue(f){
  const v=f.value;
  if(v==null)return '<span class="muted">Not known</span>';
  if(Array.isArray(v)&&v.length&&v[0]&&v[0].fibre!==undefined)return esc(v.map(x=>`${x.percent}% ${x.fibre}`).join(', '));
  if(v===true)return 'Yes';
  if(typeof v==='object')return esc(JSON.stringify(v));
  return esc(v)+(f.unit?' '+esc(f.unit):'');
}

async function viewProduct(id){
  const d=await api('/studio/api/products/'+encodeURIComponent(id));
  const r=d.record,f=r.filo||{},ver=d.product_version;
  const tags=((r.fit||{}).fit_tags||[]);
  const tagSet=new Set(tags.map(t=>t.tag));
  const tagChoices=d.fit_tag_choices.map(c=>{const t=tags.find(x=>x.tag===c.concept_id);
    return `<label class="chip" style="padding-right:10px"><input type="checkbox" value="${esc(c.concept_id)}" ${tagSet.has(c.concept_id)?'checked':''}> ${words(c.concept_id)}${t?` <span class="small muted">(${esc(STATUS[t.fact.status]||t.fact.status)})</span>`:''}</label>`;}).join('');
  const isLive=d.status==='live'||d.status==='hidden_out_of_stock';
  $('#view').innerHTML=`
  <div class="back"><button class="ghost" id="bk">Back to products</button></div>
  <div class="panel">
    <div class="small muted">${esc(d.brand.name)}${d.brand.approved?'':' · brand not approved'} · ${words(r.category)} · version ${esc(ver)}</div>
    <h2>${esc(r.name)}</h2>
    <div class="row"><span class="pill ${esc(d.status)}">${esc(PSTATUS[d.status]||d.status)}</span>
      ${d.decision?`<span class="pill ${esc(d.decision)}">${esc(DECISION[d.decision])}</span>`:''}
      <span class="small muted">${d.decided_by==='brooklyn'?'Decision confirmed by you':d.decided_by==='rules'?'Approved by the rules':'Decision is a suggestion'}</span>
      ${link(d.product_url,'Open the page')}${r.retailer?' <span class="small muted">sold by '+esc(r.retailer)+'</span>':''}</div>
    <p>${esc(f.decision_reason||'')}</p>
    ${(f.missing_evidence||[]).length?`<p class="small muted">Missing: ${esc(f.missing_evidence.join('; '))}</p>`:''}
    ${f.filo_score!=null?`<p class="small">Filo score ${esc(f.filo_score)}${f.fair_price_verdict?' · '+esc(f.fair_price_verdict):''}</p>`:''}
    ${d.spot_check?`<div class="note"><b>Spot check.</b> The rules put this live on their own. If it looks right, confirm it; if not, take it down or put it on hold.</div>`:''}
    ${(d.auto_reasons||[]).length&&d.status!=='live'?`<div class="note"><b>Why the rules didn't put it live:</b> ${esc(d.auto_reasons.join(' '))}</div>`:''}
    ${d.go_live_problems.length?`<div class="problems"><b>Can't go live yet:</b> ${esc(d.go_live_problems.join(' '))}</div>`:''}
    ${d.stock_note?`<div class="note">${esc(d.stock_note)}</div>`:''}
    <h3>Your decision</h3>
    <div class="row">${Object.keys(DECISION).map(k=>`<label><input type="radio" name="dec" value="${k}" ${(d.decision||'')===k?'checked':''}> ${DECISION[k]}</label>`).join(' &nbsp; ')}</div>
    <textarea id="why" placeholder="Why (optional, for your records)"></textarea>
    <div class="row"><button id="save">Save decision</button>
      <button id="pub" ${d.go_live_problems.length?'disabled':''}>Confirm and go live</button>
      ${isLive?'<button class="ghost" id="down">Take down</button>':''}
      <button class="ghost" id="chk">Check the page now</button>
      <button class="ghost warn" id="ret" style="color:#fff">Retire</button></div>
  </div>
  <div class="split">
    <div class="panel"><h3>Marks</h3>
      <label class="row"><input type="checkbox" id="uf" ${f.is_unique_find?'checked':''}> Unique find</label>
      <label class="row"><input type="checkbox" id="il" ${f.is_independent_label?'checked':''}> Independent label</label>
      <textarea id="sn" placeholder="Note">${esc(f.studio_note||'')}</textarea>
      <div class="row"><button class="ghost" id="saveMarks">Save marks</button></div>
      <h3>In Filo Standards</h3>
      ${d.standards.length?d.standards.map(s=>`<div class="small">${words(s.basic)}: ${words(s.slot)}${s.price_line?' (under C$'+esc(s.price_line)+')':''}</div>`).join(''):'<p class="small muted">Not in any yet. Add it from Standards once it\'s confirmed.</p>'}
    </div>
    <div class="panel"><h3>Cut words</h3>
      <p class="small muted">Untick what's wrong, tick what's missing. Words you add count as your read of the photos, never as confirmed.</p>
      <div id="tagBox">${tagChoices}</div>
      <div class="row"><button class="ghost" id="saveTags">Save cut words</button></div>
    </div>
  </div>
  <div class="panel"><h3>Facts and where they came from</h3>
    <table><tr><th>Fact</th><th>Value</th><th>Status</th><th>Evidence</th></tr>
    ${d.facts.map(x=>`<tr><td>${esc(x.field.replace(/^(materials|construction|fit|people_and_sourcing|animal_welfare|buying)\./,'').replace(/[._]/g,' '))}</td>
      <td>${factValue(x)}</td>
      <td><span class="pill">${esc(STATUS[x.status]||x.status)}</span>${x.source_type?`<div class="small muted">${esc(SOURCE[x.source_type]||x.source_type)}${x.scope?' · '+words(x.scope):''}</div>`:''}</td>
      <td class="small">${x.evidence.map(e=>`<div>${e.quote?`<span class="quote">"${esc(e.quote)}"</span> `:esc(e.claim)+' '}${link(e.source_url,'source')} <span class="muted">${when(e.checked_at)}${e.status!=='current'?' · '+words(e.status):''}</span></div>`).join('')||'<span class="muted">None</span>'}</td></tr>`).join('')}
    </table></div>
  <div class="panel"><h3>Colours, sizes and prices</h3>
    <table><tr><th>Colour</th><th>Size</th><th>Price</th><th>CAD</th><th>Stock</th></tr>
    ${d.variants.map(v=>`<tr><td>${esc(v.colour)}</td><td>${esc(v.size_label)}</td>
      <td>${v.sale_price!=null?`${money(v.sale_price,v.currency)} <span class="muted small">was ${money(v.price,v.currency)}</span>`:money(v.price,v.currency)||'<span class="muted">No price</span>'}</td>
      <td>${money(v.price_cad,'CAD')}${v.fx_rate_id&&v.currency!=='CAD'?`<div class="small muted">${esc(v.fx_rate_id)}</div>`:''}</td><td>${words(v.availability)}</td></tr>`).join('')}
    </table></div>
  <div class="panel"><h3>Recent checks of the page</h3>
    ${d.sources.map(s=>`<div class="small">${when(s.fetched_at)}: ${words(s.status)}${s.error?' <span class="muted">('+esc(s.error)+')</span>':''}</div>`).join('')||'<p class="small muted">None yet.</p>'}</div>`;
  $('#bk').onclick=()=>tab('products');
  const dec=()=>{const c=document.querySelector('input[name=dec]:checked');return c?c.value:'';};
  $('#save').onclick=()=>act(()=>api(`/studio/api/products/${encodeURIComponent(id)}/decide`,{decision:dec(),reason:$('#why').value,version:ver})).then(()=>viewProduct(id));
  $('#pub').onclick=()=>act(()=>api(`/studio/api/products/${encodeURIComponent(id)}/decide`,{decision:dec(),reason:$('#why').value,version:ver,publish:true}),'Live.').then(()=>viewProduct(id));
  if($('#down'))$('#down').onclick=()=>act(()=>api(`/studio/api/products/${encodeURIComponent(id)}/take_down`,{}),'Taken down.').then(()=>viewProduct(id));
  $('#chk').onclick=()=>act(()=>api(`/studio/api/products/${encodeURIComponent(id)}/refresh`,{}),'Checked.').then(()=>viewProduct(id));
  $('#ret').onclick=()=>{if(confirm('Retire this product? It won\'t show or be checked again.'))act(()=>api(`/studio/api/products/${encodeURIComponent(id)}/retire`,{}),'Retired.').then(()=>viewProduct(id));};
  $('#saveMarks').onclick=()=>act(()=>api(`/studio/api/products/${encodeURIComponent(id)}/flags`,{version:ver,changes:{is_unique_find:$('#uf').checked,is_independent_label:$('#il').checked,studio_note:$('#sn').value}})).then(()=>viewProduct(id));
  $('#saveTags').onclick=()=>act(()=>api(`/studio/api/products/${encodeURIComponent(id)}/tags`,{version:ver,tags:[...document.querySelectorAll('#tagBox input:checked')].map(i=>i.value)})).then(()=>viewProduct(id));
}

/* ---------------- brands ---------------- */
let bFilter={status:'',evidence:'',category:'',style_led:'',approved:'',q:''};
function researchHtml(items){
  if(!items||!items.length)return '<span class="muted small">None</span>';
  return items.map(it=>typeof it!=='object'?`<div class="small">${esc(it)}</div>`:
    `<div class="small" style="margin-bottom:8px">${Object.entries(it).map(([k,v])=>{
      const val=Array.isArray(v)?v.map(x=>typeof x==='string'&&/^https?:/.test(x)?link(x):esc(typeof x==='object'?JSON.stringify(x):x)).join(', '):
        (typeof v==='string'&&/^https?:/.test(v)?link(v):esc(typeof v==='object'?JSON.stringify(v):v));
      return `<div><span class="muted">${words(k)}:</span> ${val}</div>`;}).join('')}</div>`).join('');
}
async function viewBrands(){
  const qs=new URLSearchParams(bFilter).toString();
  const d=await api('/studio/api/brands?'+qs);const fc=d.facets||{};
  setTimeout(jobsPanel,0);
  const sel=(k,opts)=>`<select data-f="${k}">${opts.map(([v,l])=>`<option value="${esc(v)}" ${bFilter[k]===v?'selected':''}>${esc(l)}</option>`).join('')}</select>`;
  $('#view').innerHTML=`<div id="jobs"></div>
  <p class="muted">${esc(fc.approved)} approved of ${esc(fc.total)} brands.</p>
  <div class="row">
    ${sel('approved',[['','Approved or not'],['yes','Approved'],['no','Not approved']])}
    ${sel('status',[['','Any research status'],['priority_screen','Priority screen'],['discovery_lead','Discovery lead'],['resale_source','Resale source'],['remove','Rejected']])}
    ${sel('evidence',[['','Any evidence level'],...(fc.evidence||[]).map(e=>[e,e])])}
    ${sel('category',[['','Any category'],...(fc.categories||[]).map(c=>[c,c.replace(/_/g,' ')])])}
    ${sel('style_led',[['','Style-led or not'],['yes','Style-led'],['no','Not style-led']])}
    <input type="text" data-f="q" placeholder="Search" value="${esc(bFilter.q)}"></div>
  <div class="panel" style="padding:6px 8px"><table><tr><th>Brand</th><th>Research</th><th>Evidence</th><th></th></tr>
  ${d.brands.map(b=>`<tr class="click" data-b="${esc(b.brand_id)}"><td><b>${esc(b.name)}</b> ${b.approved?'<span class="pill live">Approved</span>':''}${b.unique_find?' <span class="pill">Unique find</span>':''}${b.independent_label?' <span class="pill">Independent</span>':''}
      <div class="small muted">${esc((b.categories||[]).join(', ').replace(/_/g,' '))}${b.region?' · '+esc(b.region):''}${b.style_led?' · style-led':''}</div></td>
    <td>${words(b.research_status==='remove'?'rejected':b.research_status)}</td><td class="small">${esc(b.evidence_level)}</td><td class="small muted">Open</td></tr>
    <tr id="bx-${esc(b.brand_id)}" hidden><td colspan="4"></td></tr>`).join('')}</table></div>`;
  document.querySelectorAll('[data-f]').forEach(el=>el.onchange=()=>{bFilter[el.dataset.f]=el.value;viewBrands();});
  const byId=Object.fromEntries(d.brands.map(b=>[b.brand_id,b]));
  document.querySelectorAll('tr[data-b]').forEach(r=>r.onclick=()=>openBrand(byId[r.dataset.b]));
}
function openBrand(b){
  const row=document.getElementById('bx-'+b.brand_id);if(!row.hidden){row.hidden=true;return;}
  row.hidden=false;const cell=row.firstElementChild;
  cell.innerHTML=`<div class="split"><div>
      <div class="row">${b.official_url?link(b.official_url,'Brand site'):''} ${b.url_verified?'':'<span class="small muted">site link not verified</span>'}</div>
      <div class="row"><button data-a="approve">${b.approved?'Approved':'Approve'}</button><button class="ghost" data-a="reject">Reject</button>
        <select data-k="research_status">${['priority_screen','discovery_lead','resale_source','remove'].map(s=>`<option value="${s}" ${b.research_status===s?'selected':''}>${s==='remove'?'Rejected':words(s)}</option>`).join('')}</select></div>
      <label class="row"><input type="checkbox" data-k="unique_find" ${b.unique_find?'checked':''}> Unique find</label>
      <label class="row"><input type="checkbox" data-k="independent_label" ${b.independent_label?'checked':''}> Independent label</label>
      <div class="row">Style fit <select data-k="style_fit"><option value="">Not set</option>${[0,1,2,3,4].map(n=>`<option ${b.style_fit===n?'selected':''}>${n}</option>`).join('')}</select></div>
      <textarea data-k="studio_note" placeholder="Your note">${esc(b.studio_note||'')}</textarea>
      <div class="row"><button class="ghost" data-a="save">Save</button>${b.approved_on?`<span class="small muted">Approved ${esc(b.approved_on)}</span>`:''}</div>
      ${b.approved?`<h3>Bring in the whole catalogue</h3>
      <p class="small muted">Reads every women's and unisex piece this brand sells. Clear cases go live on their own, borderline ones wait for you in Products.</p>
      <input type="text" data-k="store_url" value="${esc(b.official_url||'')}" placeholder="Store link, e.g. https://brand.com/en-ca" style="width:100%">
      <label class="row small"><input type="checkbox" data-k="women_only"> This brand only makes women's clothes</label>
      <label class="row small"><input type="checkbox" data-k="use_ai"> Let Claude fill gaps the page leaves (a little cost per product)</label>
      <div class="row"><button data-a="catalog">Bring in catalogue</button></div>`:''}
    </div><div><h3>Research notes</h3>${researchHtml(b.research)}<h3>Discovery notes</h3>${researchHtml(b.discovery)}
      ${(b.flags||[]).length?`<h3>Flags</h3>${researchHtml(b.flags)}`:''}</div></div>`;
  const val=k=>{const el=cell.querySelector(`[data-k="${k}"]`);return el.type==='checkbox'?el.checked:el.value;};
  const post=body=>act(()=>api('/studio/api/brands/'+encodeURIComponent(b.brand_id),body)).then(nb=>{Object.assign(b,nb);row.hidden=true;openBrand(b);});
  cell.querySelector('[data-a=approve]').onclick=()=>post({approved:true});
  cell.querySelector('[data-a=reject]').onclick=()=>post({reject:true});
  cell.querySelector('[data-a=save]').onclick=()=>post({research_status:val('research_status'),unique_find:val('unique_find'),independent_label:val('independent_label'),style_fit:val('style_fit'),studio_note:val('studio_note')});
  const cat=cell.querySelector('[data-a=catalog]');
  if(cat)cat.onclick=()=>act(()=>api('/studio/api/catalog/start',{brand_id:b.brand_id,store_url:val('store_url'),women_only:val('women_only'),use_ai:val('use_ai')}),'Started. Progress shows at the top of Brands.').then(()=>viewBrands());
}
let jobTimer=null;
async function jobsPanel(){
  const el=document.getElementById('jobs');if(!el)return;
  let d;try{d=await api('/studio/api/catalog');}catch(e){return;}
  const OUT={live:'Live',hidden_out_of_stock:'Sold out',review:'For you to review',held:'Held back',waiting:'Not enough on the page',spot_check:'Spot checks',kept_brooklyn:'Already yours',skipped_men:"Men's, skipped",skipped_unclear_gender:'Not clearly women\'s, skipped',skipped_not_clothing:'Not clothing, skipped',skipped_other_brand:'Other brands, skipped',invalid:"Didn't pass the checks",failed:"Couldn't read"};
  const running=d.jobs.some(j=>j.status==='running'||j.status==='queued');
  el.innerHTML=d.jobs.length?`<div class="panel"><h3>Catalogues${d.auto_approve?'':' (automatic approval is off)'}</h3>${d.jobs.slice(0,6).map(j=>`<div style="margin-bottom:10px">
     <b>${esc(j.name)}</b> <span class="pill ${j.status==='done'?'live':j.status==='failed'?'hold':'in_review'}">${esc(words(j.status))}</span>
     <span class="small muted">${esc(j.found)} read${j.method?' · '+esc(j.method):''} · ${when(j.started_at)}</span>
     ${j.status==='running'?` <button class="ghost" data-stop="${esc(j.job_id)}" style="padding:2px 10px">Stop</button>`:''}
     <div class="small">${Object.entries(j.counts||{}).filter(([k,v])=>v).map(([k,v])=>`${esc(OUT[k]||k)}: ${esc(v)}`).join(' · ')||'<span class="muted">Starting…</span>'}</div>
     ${j.error?`<div class="small" style="color:var(--ox)">${esc(j.error)}</div>`:''}</div>`).join('')}</div>`:'';
  el.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>act(()=>api('/studio/api/catalog/stop',{job_id:b.dataset.stop}),'Stopped.').then(jobsPanel));
  clearTimeout(jobTimer);if(running&&curTab==='brands')jobTimer=setTimeout(jobsPanel,5000);
}

/* ---------------- test hunt ---------------- */
let lastHunt={text:'',market:'CA'};
async function viewHunt(){
  $('#view').innerHTML=`<div class="panel"><h2>Test hunt</h2>
    <p class="muted small">Type what a shopper might ask. This runs the real search on live products, with no AI, and shows what Filo understood and why each piece was picked.</p>
    <div class="row"><input type="text" id="ht" style="flex:1;min-width:240px" value="${esc(lastHunt.text)}" placeholder="white tee boxy not see through under 100">
      <select id="hm"><option ${lastHunt.market==='CA'?'selected':''}>CA</option><option ${lastHunt.market==='US'?'selected':''}>US</option></select><button id="hgo">Hunt</button></div></div>
    <div id="hout"></div>`;
  const go=async()=>{lastHunt={text:$('#ht').value,market:$('#hm').value};$('#hout').innerHTML='<p class="muted">On the hunt…</p>';
    try{renderHunt(await api('/studio/api/hunt',lastHunt));}catch(e){$('#hout').innerHTML='';say(e.message,true);}};
  $('#hgo').onclick=go;$('#ht').onkeydown=e=>{if(e.key==='Enter')go();};
  if(lastHunt.text)go();
}
const FILTER={show_rule:'not live or not passing Filo rules',category:'other garment types',resale:'resale listings',material:'wrong fabric',exclusion:'has an excluded material',origin:'origin not confirmed there',ethics:'no product-level proof',gated_conflict:'evidence says the opposite',colour:'other colours',size:'size not available',region:"doesn't ship there",stock:'sold out',freshness:'not checked in 30 days',price:'no price',budget:'over budget'};
function renderHunt(r){
  const b=r.brief||{};
  const chips=Object.entries(b).filter(([k,v])=>!['vibe_text','mode','unmatched_phrases','conflicts'].includes(k)&&v!==null&&!(Array.isArray(v)&&!v.length))
    .map(([k,v])=>`<span class="chip" style="padding-right:10px"><span class="muted">${words(k)}</span> ${esc(typeof v==='object'&&!Array.isArray(v)?(v.amount?v.currency+' '+v.amount:JSON.stringify(v)):[].concat(v).join(', ').replace(/_/g,' '))}</span>`).join('');
  const funnel=Object.entries(r.funnel||{}).map(([k,v])=>`${esc(v)} ${esc(FILTER[k]||k)}`).join(' · ');
  $('#hout').innerHTML=`<div class="panel"><h3>What Filo understood</h3><div>${chips||'<span class="muted">Nothing specific.</span>'}</div>
    <p class="small muted">Mode: ${esc(r.mode||b.mode)}${(b.unmatched_phrases||[]).length?` · Words it didn't place: ${esc(b.unmatched_phrases.join(', '))}`:''}</p>
    <p class="small muted">Looked at ${esc(r.considered||0)} live ${(r.considered||0)===1?'product':'products'}${funnel?'. Removed: '+funnel:''}.</p>
    ${(r.conflicts||[]).length?`<div class="note">These can't both be true: ${esc(r.conflicts.map(c=>c.join(' and ').replace(/_/g,' ')).join('; '))}. The Stylist will ask which matters more.</div>`:''}
    ${(r.problems||[]).length?`<div class="problems">${esc(r.problems.join(' '))}</div>`:''}</div>
  ${r.nothing_passed?`<div class="panel"><h3>Nothing passed</h3>${(r.flex_options||[]).map(f=>`<div class="small">If ${esc(words(f.filter))} could change: ${esc(f.would_pass)} would pass${f.lowest_price?` (lowest ${money(f.lowest_price,f.currency)})`:''}${f.colours?` (colours: ${esc(f.colours.join(', '))})`:''}</div>`).join('')||'<p class="small muted">Nothing in the bank comes close yet. This is logged to the Backlog.</p>'}</div>`:''}
  ${r.fewer_than_asked?`<p class="muted">Only ${esc(r.fewer_than_asked)} passed.</p>`:''}
  <div class="grid">${(r.results||[]).map(c=>`<div class="tile">
    <div class="row" style="justify-content:space-between"><span class="pill ${c.is_pick?'live':''}">${esc(c.is_pick?"Stylist's pick":c.slot_label||'')}</span>${c.unique_find?'<span class="pill">Unique find</span>':''}</div>
    <div class="small muted">${esc(c.brand)}</div><div><b>${esc(c.name)}</b></div>
    <div class="small">${esc(c.colour||'')} · ${money(c.price,c.currency)}${c.on_sale?' (sale)':''}${c.price_note?' · '+esc(c.price_note):''}</div>
    <div class="small muted">Match ${esc(Math.round(c.scores.match))} · Quality ${esc(c.scores.quality??'?')} · Evidence ${esc(c.scores.evidence_label)}</div>
    ${c.trade_off_note?`<div class="note small">${esc(c.trade_off_note)}</div>`:''}
    ${c.stylist_note?`<div class="small"><i>${esc(c.stylist_note)}</i></div>`:''}
    <div class="small" style="margin-top:6px">${c.claims.map(cl=>`<div>${esc(words(cl.requirement))}${cl.value?': '+esc(cl.value):''} <span class="muted">${esc(cl.source||words(cl.status))}${cl.quote?' "'+esc(cl.quote.slice(0,90))+'"':''}</span></div>`).join('')}</div>
    ${(c.missing||[]).length?`<div class="small muted">Not shown on the page: ${esc(c.missing.map(m=>m.replace(/_/g,' ')).join(', '))}</div>`:''}
    <div class="row">${link(c.url,'Open page')}</div></div>`).join('')}</div>`;
}

/* ---------------- standards ---------------- */
async function viewStandards(){
  const d=await api('/studio/api/standards');
  const opts=d.choices.map(c=>`<option value="${esc(c.product_id)}">${esc(c.brand)} ${esc(c.name)}${c.status!=='live'?' ('+esc(PSTATUS[c.status]||c.status)+')':''}</option>`).join('');
  const SL={best_overall:'Best overall',best_under:'Best under',best_splurge:'Best splurge',alternate:'Alternate'};
  $('#view').innerHTML=`<p class="muted">The "just tell me" answers. Only products you've confirmed as pass or conditional can be picked; only live ones show to shoppers.</p>
  <div class="grid">${d.standards.map(s=>`<div class="tile"><h2 style="font-size:19px">${esc(s.label)}</h2>
    ${['best_overall','best_under','best_splurge'].map(slot=>{const p=s.picks.find(x=>x.slot===slot);
      return `<div class="small" style="margin:6px 0"><span class="muted">${SL[slot]}${slot==='best_under'&&p&&p.price_line?' C$'+esc(p.price_line):''}:</span> ${p?`${esc(p.brand)} ${esc(p.name)}${p.status!=='live'?' <span class="pill">'+esc(PSTATUS[p.status]||p.status)+'</span>':''} <button class="ghost" style="padding:0 8px" data-rm="${esc(s.basic)}|${slot}|1">×</button>${p.note?`<div class="muted">${esc(p.note)}</div>`:''}`:'<span class="muted">Not set</span>'}</div>`;}).join('')}
    ${s.picks.filter(p=>p.slot==='alternate').map(p=>`<div class="small"><span class="muted">Alternate ${esc(p.rank)}:</span> ${esc(p.brand)} ${esc(p.name)}
      <button class="ghost" style="padding:0 7px" data-mv="${esc(s.basic)}|${esc(p.rank)}|-1">↑</button><button class="ghost" style="padding:0 7px" data-mv="${esc(s.basic)}|${esc(p.rank)}|1">↓</button>
      <button class="ghost" style="padding:0 8px" data-rm="${esc(s.basic)}|alternate|${esc(p.rank)}">×</button></div>`).join('')}
    <details style="margin-top:8px"><summary class="small">Set a pick</summary>
      <select data-slot="${esc(s.basic)}">${Object.entries(SL).map(([k,l])=>`<option value="${k}">${l}</option>`).join('')}</select>
      <select data-prod="${esc(s.basic)}" style="max-width:100%">${opts||'<option value="">Confirm a product first</option>'}</select>
      <input type="number" data-price="${esc(s.basic)}" placeholder="Under C$ (for best under)" style="width:100%;margin:4px 0">
      <input type="text" data-note="${esc(s.basic)}" placeholder="One-line note" style="width:100%">
      <div class="row"><button data-set="${esc(s.basic)}">Save pick</button></div></details></div>`).join('')}</div>`;
  const q=(a,b)=>document.querySelector(`[data-${a}="${b}"]`);
  document.querySelectorAll('[data-set]').forEach(b=>b.onclick=()=>{const k=b.dataset.set;
    act(()=>api('/studio/api/standards/set',{basic:k,slot:q('slot',k).value,product_id:q('prod',k).value,price_line:q('price',k).value||null,note:q('note',k).value})).then(viewStandards);});
  document.querySelectorAll('[data-rm]').forEach(b=>b.onclick=()=>{const [basic,slot,rank]=b.dataset.rm.split('|');
    act(()=>api('/studio/api/standards/remove',{basic,slot,rank:+rank}),'Removed.').then(viewStandards);});
  document.querySelectorAll('[data-mv]').forEach(b=>b.onclick=()=>{const [basic,rank,dir]=b.dataset.mv.split('|');
    act(()=>api('/studio/api/standards/move',{basic,rank:+rank,direction:+dir}),'Moved.').then(viewStandards);});
}

/* ---------------- vocabulary ---------------- */
let vFilter={q:'',dimension:''};
async function viewVocab(){
  const d=await api(`/studio/api/vocabulary?q=${encodeURIComponent(vFilter.q)}&dimension=${encodeURIComponent(vFilter.dimension)}`);
  $('#view').innerHTML=`
  <div class="split"><div class="panel"><h3>Shopper words that matched nothing</h3>
    ${d.unmatched.length?d.unmatched.map(u=>`<div class="small">"${esc(u.phrase)}" <span class="muted">${esc(u.times)} times</span></div>`).join(''):'<p class="small muted">None yet. These fill in once the Stylist is running.</p>'}</div>
  <div class="panel"><h3>Add a concept</h3>
    <div class="row"><input type="text" id="ncid" placeholder="ID, like wide_leg"><select id="ndim">${d.dimensions.map(x=>`<option>${esc(x)}</option>`).join('')}</select></div>
    <input type="text" id="nnote" placeholder="What it means" style="width:100%"><div class="row"><button class="ghost" id="nadd">Add</button></div></div></div>
  <div class="row"><input type="text" id="vq" placeholder="Search words" value="${esc(vFilter.q)}">
    <select id="vd"><option value="">All groups</option>${d.dimensions.map(x=>`<option ${vFilter.dimension===x?'selected':''}>${esc(x)}</option>`).join('')}</select></div>
  <div class="panel" style="padding:6px 8px"><table><tr><th>Concept</th><th>Phrases</th></tr>
  ${d.concepts.map(c=>`<tr><td><b>${words(c.concept_id)}</b><div class="small muted">${words(c.dimension)}${c.note?' · '+esc(c.note):''}</div></td>
    <td>${c.phrases.map(p=>`<span class="chip ${p.removed?'removed':''}"><span class="muted">${esc(p.language)}</span> ${esc(p.phrase)}${p.removed?`<button data-add='${esc(JSON.stringify([c.concept_id,p.language,p.phrase]))}' title="Put back">+</button>`:`<button data-del='${esc(JSON.stringify([c.concept_id,p.language,p.phrase]))}' title="Remove">×</button>`}</span>`).join('')}
      <span class="chip"><select data-lang="${esc(c.concept_id)}" style="border:0;padding:0">${['en','fr','es','it','de','pt','nl','sv','ja','ko'].map(l=>`<option>${l}</option>`).join('')}</select><input type="text" data-new="${esc(c.concept_id)}" placeholder="add" style="border:0;padding:2px;width:110px"></span></td></tr>`).join('')}</table></div>`;
  $('#vq').onchange=e=>{vFilter.q=e.target.value;viewVocab();};
  $('#vd').onchange=e=>{vFilter.dimension=e.target.value;viewVocab();};
  document.querySelectorAll('[data-del]').forEach(b=>b.onclick=()=>{const [concept_id,language,phrase]=JSON.parse(b.dataset.del);
    act(()=>api('/studio/api/vocabulary/remove_phrase',{concept_id,language,phrase}),'Removed.').then(viewVocab);});
  document.querySelectorAll('[data-add]').forEach(b=>b.onclick=()=>{const [concept_id,language,phrase]=JSON.parse(b.dataset.add);
    act(()=>api('/studio/api/vocabulary/add_phrase',{concept_id,language,phrase}),'Put back.').then(viewVocab);});
  document.querySelectorAll('[data-new]').forEach(i=>i.onkeydown=e=>{if(e.key!=='Enter')return;const cid=i.dataset.new;
    act(()=>api('/studio/api/vocabulary/add_phrase',{concept_id:cid,language:document.querySelector(`[data-lang="${cid}"]`).value,phrase:i.value}),'Added.').then(viewVocab);});
  $('#nadd').onclick=()=>act(()=>api('/studio/api/vocabulary/add_concept',{concept_id:$('#ncid').value,dimension:$('#ndim').value,note:$('#nnote').value}),'Added.').then(viewVocab);
}

/* ---------------- backlog, health, sources ---------------- */
const tbl=(rows,cols,empty)=>rows&&rows.length?`<table><tr>${cols.map(c=>`<th>${esc(c[1])}</th>`).join('')}</tr>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[2]?c[2](r):esc(r[c[0]])}</td>`).join('')}</tr>`).join('')}</table>`:`<p class="small muted">${esc(empty||'Nothing yet.')}</p>`;
async function viewBacklog(){
  const d=await api('/studio/api/backlog?days=30');
  const brief=r=>esc(Object.entries(r.brief_structured||{}).filter(([k,v])=>v!==null&&v!==''&&!(Array.isArray(v)&&!v.length)).map(([k,v])=>`${k.replace(/_/g,' ')}: ${Array.isArray(v)?v.join(', '):typeof v==='object'?JSON.stringify(v):v}`).join(' · '));
  $('#view').innerHTML=`<p class="muted">Your research to-do list from the last ${esc(d.days)} days of hunts, most frequent first. This never changes the ranking rules by itself.</p>
  <div class="panel"><h3>Hunts with no results</h3>${tbl(d.no_results,[['brief','Brief',brief],['count','Times']],'None yet. These fill in once the Stylist is running.')}</div>
  <div class="split">
    <div class="panel"><h3>Reworded within 15 minutes</h3>${tbl(d.rewordings,[['category','Category',r=>words(r.category)],['times','Times']])}</div>
    <div class="panel"><h3>Why shoppers passed</h3>${tbl(d.rejection_reasons,[['reason','Reason',r=>words(r.reason)],['times','Times']])}</div>
    <div class="panel"><h3>Saved but never tapped</h3>${tbl(d.saved_not_clicked,[['name','Product',r=>esc((r.brand||'')+' '+(r.name||r.product_id))],['saves','Saves']])}</div>
    <div class="panel"><h3>Brands asked for but not in Filo</h3>${tbl(d.missing_brands,[['brand','Brand'],['asked','Asked']])}</div>
    <div class="panel"><h3>Weakest categories</h3>${tbl(d.weak_categories,[['category','Category',r=>words(r.category)],['hunts','Hunts'],['pct_nothing','% with nothing',r=>esc(r.pct_nothing)+'%']])}</div>
    <div class="panel"><h3>Weakest languages</h3>${tbl(d.weak_languages,[['language','Language'],['hunts','Hunts'],['pct_nothing','% with nothing',r=>esc(r.pct_nothing)+'%']])}</div>
  </div>
  <div class="panel"><h3>Shopper words that matched nothing</h3>${tbl(d.unmatched_phrases,[['phrase','Words'],['times','Times']])}</div>`;
}
async function viewHealth(){
  const d=await api('/studio/api/health?days=30');
  const pct=v=>v==null?'<span class="muted">No data yet</span>':esc(v)+'%';
  const b=d.bank||{};
  $('#view').innerHTML=`<p class="muted">Last ${esc(d.days)} days. ${esc(d.hunts)} hunts.</p><div class="grid">
    <div class="tile"><div class="small muted">Hard-constraint violations</div><div class="n">${pct(d.hard_constraint_violations.pct)}</div><div class="small muted">${esc(d.hard_constraint_violations.count)} of ${esc(d.hard_constraint_violations.results)} results. Target near zero.</div></div>
    <div class="tile"><div class="small muted">Evidence accuracy</div><div class="n">${pct(d.evidence_accuracy.pct)}</div><div class="small muted">${esc(d.evidence_accuracy.disputed)} disputed. ${esc(Object.entries(d.evidence_accuracy.by_status).map(([k,v])=>k.replace(/_/g,' ')+' '+v).join(', '))}</div></div>
    <div class="tile"><div class="small muted">Recovered after no results</div><div class="n">${pct(d.zero_result_recovery.pct)}</div><div class="small muted">${esc(d.zero_result_recovery.empty_hunts)} hunts found nothing</div></div>
    <div class="tile"><div class="small muted">Follow-up questions per hunt</div><div class="n">${d.follow_ups_per_hunt==null?'<span class="muted">No data yet</span>':esc(d.follow_ups_per_hunt)}</div></div>
    <div class="tile"><div class="small muted">Live products checked in 48 hours</div><div class="n">${pct(d.freshness.pct)}</div><div class="small muted">${esc(d.freshness.live)} live</div></div>
    <div class="tile"><div class="small muted">Saves and clicks</div><div class="n">${esc(d.saves_and_clicks.saves)} / ${esc(d.saves_and_clicks.taps)}</div><div class="small muted">saves / taps · ${esc(d.saves_and_clicks.buys)} buy taps · ${esc(d.saves_and_clicks.dismissed)} dismissed</div></div>
    <div class="tile"><div class="small muted">The bank</div><div class="small">${esc(b.bank_brands)} brands · ${esc(b.bank_products)} products · ${esc(b.bank_evidence)} evidence · ${esc(b.bank_concepts)} concepts</div></div>
  </div>`;
}
async function viewSources(){
  const d=await api('/studio/api/sources');
  $('#view').innerHTML=`<div class="panel"><h3>Product pages that are failing</h3>
    ${tbl(d.failing,[['name','Product',r=>`<b>${esc(r.brand)}</b> ${esc(r.name)}<div class="small">${link(r.product_url,'page')}</div>`],['last_error','Last error',r=>esc(r.last_error||'')],
      ['failures_in_a_row','Fails in a row'],['last_ok','Last worked',r=>esc(when(r.last_ok))],
      ['x','',r=>`<button class="ghost" data-retry="${esc(r.product_id)}">Try now</button> <button class="ghost" data-retire="${esc(r.product_id)}">Retire</button>`]],'Nothing failing.')}</div>
  <div class="panel"><h3>Imports that couldn't read the page (14 days)</h3>${tbl(d.failed_imports,[['url','Link',r=>link(r.url)],['error','Error'],['tries','Tries'],['last_tried','Last tried',r=>esc(when(r.last_tried))]],'None.')}</div>`;
  document.querySelectorAll('[data-retry]').forEach(b=>b.onclick=()=>act(()=>api(`/studio/api/products/${encodeURIComponent(b.dataset.retry)}/refresh`,{}),'Checked.').then(viewSources));
  document.querySelectorAll('[data-retire]').forEach(b=>b.onclick=()=>{if(confirm('Retire this product?'))act(()=>api(`/studio/api/products/${encodeURIComponent(b.dataset.retire)}/retire`,{}),'Retired.').then(viewSources);});
}
window.onhashchange=()=>{if(!$('#app').hidden&&location.hash.slice(1)!==curTab)tab(location.hash.slice(1));};
boot();
</script></div></body></html>"""

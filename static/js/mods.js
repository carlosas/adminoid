'use strict';
(()=>{
const {t,$,el,icon,api,run}=window.PZ;
const PZ=window.PZ;
let filter='all',dragging=null;
const strip=token=>token.trim().replace(/^\\+/,'');

// Mods and WorkshopItems as currently edited (falls back to the saved line before the settings load).
function activeTokens(){
  const value=PZ.state.config?PZ.effective('server','Mods'):PZ.state.mods.active.join(';');
  return String(value||'').split(';').filter(x=>x.trim());
}
const workshopItems=()=>String((PZ.state.config?PZ.effective('server','WorkshopItems'):PZ.state.mods.workshop.join(';'))||'').split(';').filter(Boolean);
const editable=()=>PZ.state.mods?.editable&&PZ.state.config&&PZ.field('server','Mods');
function setTokens(tokens){PZ.setEdit('server','Mods',tokens.join(';'),PZ.field('server','Mods').value);}
function addWorkshop(id){
  if(!id||!PZ.field('server','WorkshopItems'))return;
  const items=workshopItems();
  if(!items.includes(id))PZ.setEdit('server','WorkshopItems',[...items,id].join(';'),PZ.field('server','WorkshopItems').value);
}
function tokenFor(id,tokens){return tokens.some(x=>x.trim().startsWith('\\'))?'\\'+id:id;}

function analyze(tokens){
  const ids=tokens.map(strip);const installed=PZ.state.mods.installed;const issues=[];
  ids.forEach((id,position)=>{
    const mod=installed.find(m=>m.id===id);
    if(!mod){issues.push({kind:'not-installed',mod:id});return;}
    for(const dep of mod.requires){
      if(!ids.includes(dep))issues.push({kind:'missing',mod:id,name:mod.name||id,requires:dep,installed:installed.some(m=>m.id===dep)});
      else if(ids.indexOf(dep)>position)issues.push({kind:'order',mod:id,name:mod.name||id,requires:dep});
    }
  });
  return issues;
}
const problemCount=()=>PZ.state.mods?analyze(activeTokens()).filter(i=>i.kind!=='not-installed').length:0;

function versionParts(v){return String(v||'').split(/[^\d]+/).filter(Boolean).map(Number);}
function compare(a,b){a=versionParts(a);b=versionParts(b);for(let i=0;i<Math.max(a.length,b.length);i++){const d=(a[i]||0)-(b[i]||0);if(d)return Math.sign(d);}return 0;}
function versionInfo(mod){
  const {versionMin:min,versionMax:max}=mod;const game=PZ.state.status?.version;
  const text=min&&max?`${min} – ${max}`:min?t('versionFrom',{v:min}):max?t('versionUntil',{v:max}):t('versionUnknown');
  const known=game&&/^\d/.test(game);
  const ok=!known||(!min||compare(game,min)>=0)&&(!max||compare(game,max)<=0);
  return {text,ok,known:Boolean(min||max)};
}

function render(){
  const data=PZ.state.mods;if(!data)return;
  const tokens=activeTokens();const ids=tokens.map(strip);const issues=analyze(tokens);
  const canEdit=editable();
  $('#mods-summary').textContent=t('modsSummary',{installed:data.installed.length,active:ids.length});
  $('#add-workshop').disabled=!canEdit;
  // Problems first: missing dependencies and wrong order block the mods from loading.
  $('#mod-issues').replaceChildren(...issues.map(i=>{
    if(i.kind==='not-installed')return el('div',{class:'banner amber',role:'status'},icon('warning','banner-icon'),el('div',{class:'grow banner-text'},el('strong',{text:i.mod}),' ',t('issueNotInstalled')));
    const text=i.kind==='missing'?[el('strong',{text:t('issueMissingTitle')}),' ',t('issueMissing',{name:i.name}),el('span',{class:'mono',text:i.requires}),t(i.installed?'issueMissingTail':'issueMissingTailNotInstalled')]
      :[el('strong',{text:t('issueOrderTitle')}),' ',el('span',{class:'mono',text:i.requires}),' ',t('issueOrder',{name:i.name})];
    let action=null;
    if(canEdit&&i.kind==='missing'&&i.installed)action=el('button',{class:'btn small',type:'button',text:t('addName',{name:i.requires}),onclick:()=>{const list=activeTokens();const at=list.map(strip).indexOf(i.mod);list.splice(at,0,tokenFor(i.requires,list));setTokens(list);}});
    if(canEdit&&i.kind==='order')action=el('button',{class:'btn small',type:'button',text:t('moveBefore'),onclick:()=>{const list=activeTokens();const from=list.map(strip).indexOf(i.requires);const [dep]=list.splice(from,1);list.splice(list.map(strip).indexOf(i.mod),0,dep);setTokens(list);}});
    return el('div',{class:'banner coral',role:'alert'},icon('warning','banner-icon'),el('div',{class:'grow small'},text),action);
  }));
  const modsEdits=['Mods','WorkshopItems'].filter(k=>Object.hasOwn(PZ.state.edits.server||{},k));
  const pendingBox=$('#mods-pending');pendingBox.hidden=!modsEdits.length;
  if(modsEdits.length)pendingBox.replaceChildren(icon('info','banner-icon'),el('div',{class:'grow'},el('div',{class:'banner-title',text:t('modsUnsaved')}),el('div',{class:'banner-text',text:t('modsUnsavedText')})),el('a',{class:'btn amber-fill',href:'#settings/modified',text:t('reviewSave')}));
  renderTable(ids,issues,canEdit);
  renderOrder(tokens,issues,canEdit);
  PZ.updateBadges();
}

function renderTable(ids,issues,canEdit){
  const q=$('#mod-search').value.trim().toLocaleLowerCase();
  $('#mod-filter').replaceChildren(...[['all','modsAll'],['active','modsActive'],['inactive','modsInactive']].map(([id,key])=>el('button',{type:'button','aria-pressed':String(filter===id),text:t(key),onclick:()=>{filter=id;render();}})));
  const rows=PZ.state.mods.installed.filter(m=>{
    const active=ids.includes(m.id);
    if(filter==='active'&&!active||filter==='inactive'&&active)return false;
    return !q||`${m.name||''} ${m.id} ${m.workshop||''}`.toLocaleLowerCase().includes(q);
  });
  $('#mod-rows').replaceChildren(...(rows.length?rows.map(m=>{
    const active=ids.includes(m.id);
    const missing=issues.filter(i=>i.mod===m.id&&i.kind==='missing');
    const v=versionInfo(m);
    const state=missing.length?el('span',{class:'pill coral',text:t('stateMissingDep')}):active?el('span',{class:'pill green',text:t('stateActive')}):el('span',{class:'pill',text:t('stateInactive')});
    const toggle=canEdit?el('button',{class:'link small',type:'button',text:active?t('deactivate'):t('activate'),onclick:()=>{
      const list=activeTokens();
      if(active)setTokens(list.filter(x=>strip(x)!==m.id));
      else{setTokens([...list,tokenFor(m.id,list)]);addWorkshop(m.workshop);}
    }}):null;
    return el('tr',{class:missing.length?'problem':''},
      el('td',{},el('div',{class:'mod-name',text:m.name||m.id}),el('div',{class:'mod-id',text:m.id})),
      el('td',{class:m.workshop?'mono small':'small muted',text:m.workshop||t('localMod')}),
      el('td',{class:v.ok?(v.known?'':'muted'):'warn-text',text:v.text,title:v.ok?'':t('versionMismatch',{v:PZ.state.status?.version||''})}),
      el('td',{},m.requires.length?m.requires.map((dep,i)=>[i?', ':'',el('span',{class:'mono small'+(ids.includes(dep)?'':' coral-text'),text:dep}),ids.includes(dep)?'':el('span',{class:'small coral-text',text:' · '+t('notActive')})]):el('span',{class:'muted',text:'—'})),
      el('td',{},el('div',{class:'mod-state'},state,toggle)));
  }):[el('tr',{},el('td',{colspan:5,class:'muted',text:PZ.state.mods.installed.length?t('noResults'):t('noInstalledMods')}))]));
}

function renderOrder(tokens,issues,canEdit){
  $('#load-order-help').textContent=canEdit?t('loadOrderHelp',{file:`${PZ.name}.ini`}):t('loadOrderReadonly');
  const list=$('#load-order');
  const move=(from,to)=>{const l=activeTokens();if(to<0||to>=l.length)return;const [x]=l.splice(from,1);l.splice(to,0,x);setTokens(l);};
  list.replaceChildren(...(tokens.length?tokens.map((token,index)=>{
    const id=strip(token);const problem=issues.some(i=>i.mod===id&&i.kind!=='not-installed');
    const item=el('li',{class:'order-item'+(problem?' problem':''),draggable:canEdit?'true':'false'},
      icon('grip','grip'),el('span',{class:'pos',text:index+1}),
      el('span',{class:'id'},el('span',{class:'mono',text:id,title:id}),problem?el('span',{class:'warn',text:t('dependency')}):null),
      canEdit?el('button',{class:'icon-btn small',type:'button','aria-label':t('moveUp',{name:id}),disabled:index===0,onclick:()=>move(index,index-1)},icon('up')):null,
      canEdit?el('button',{class:'icon-btn small',type:'button','aria-label':t('moveDown',{name:id}),disabled:index===tokens.length-1,onclick:()=>move(index,index+1)},icon('down')):null);
    item.addEventListener('dragstart',e=>{dragging=index;item.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',id);});
    item.addEventListener('dragend',()=>{dragging=null;item.classList.remove('dragging');});
    item.addEventListener('dragover',e=>{if(dragging===null)return;e.preventDefault();item.classList.add('drop-target');});
    item.addEventListener('dragleave',()=>item.classList.remove('drop-target'));
    item.addEventListener('drop',e=>{e.preventDefault();item.classList.remove('drop-target');if(dragging!==null&&dragging!==index)move(dragging,index);});
    return item;
  }):[el('li',{class:'small muted',text:t('noActiveMods')})]));
}

async function addByWorkshop(){
  const values=await PZ.dialog({icon:'plus',title:t('addWorkshop'),
    body:[el('label',{class:'stack'},el('span',{text:t('workshopId')}),el('input',{name:'workshop',inputmode:'numeric',autocomplete:'off',pattern:'\\d+'})),
      el('label',{class:'stack'},el('span',{text:t('modIds')}),el('input',{name:'mods',autocomplete:'off',spellcheck:'false'})),
      el('p',{class:'hint',text:t('workshopHint')})],
    confirm:t('add'),validate:f=>/^\d{1,20}$/.test(f.elements.workshop.value.trim())&&!/[";]/.test(f.elements.mods.value)});
  if(!values)return;
  addWorkshop(values.workshop.trim());
  const ids=values.mods.split(/[,\s]+/).map(x=>x.replace(/^\\+/,'')).filter(Boolean);
  if(ids.length){const list=activeTokens();for(const id of ids)if(!list.map(strip).includes(id))list.push(tokenFor(id,list));setTokens(list);}
  PZ.toast(t('workshopAdded'),true);
}

async function load(){
  const tasks=[api('mods').then(r=>{PZ.state.mods=r;})];
  if(!PZ.state.config)tasks.push(PZ.views.settings.load());
  await Promise.all(tasks);
  render();
}

PZ.views.mods={title:'mods',problemCount,load,
  status(){if(dragging===null)render();},
  enter(){if(PZ.state.mods)render();run(load)();}};
PZ.onChange(()=>{if(PZ.current()==='mods')render();});
$('#mod-search').addEventListener('input',render);
$('#add-workshop').addEventListener('click',run(addByWorkshop));
})();

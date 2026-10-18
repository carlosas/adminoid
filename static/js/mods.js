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
function addMods(ids){
  const list=activeTokens();
  for(const id of ids)if(!list.map(strip).includes(id))list.push(tokenFor(id,list));
  setTokens(list);
}
const steamUrl=id=>'https://steamcommunity.com/sharedfiles/filedetails/?id='+id;
// Workshop link or bare number → Workshop ID.
const parseWorkshop=value=>{value=value.trim();const m=/[?&]id=(\d{1,20})\b/.exec(value)||/^(\d{1,20})$/.exec(value);return m?m[1]:null;};

// Steam data per Workshop ID (title, Mod IDs, required items), fetched once per page load.
const workshopInfo={};
async function lookup(ids){
  const items=(await api('workshop?ids='+ids.join(','))).items;
  for(const item of items)workshopInfo[item.id]=item;
  return items;
}
let lookedUp='';
function lookupWorkshopItems(){
  const ids=workshopItems().filter(id=>!workshopInfo[id]).slice(0,20);
  if(!ids.length||ids.join()===lookedUp)return;
  lookedUp=ids.join();
  lookup(ids).then(()=>{if(PZ.current()==='mods')render();}).catch(()=>{});
}
// Mod IDs of a Workshop item: from its mod.info files once downloaded, otherwise from Steam.
function modsOf(id){
  const local=PZ.state.mods.installed.filter(m=>m.workshop===id).map(m=>m.id);
  return local.length?local:workshopInfo[id]?.modIds||[];
}

function analyze(tokens){
  const ids=tokens.map(strip);const installed=PZ.state.mods.installed;const issues=[];
  ids.forEach((id,position)=>{
    const mod=installed.find(m=>m.id===id);
    if(!mod){issues.push({kind:workshopItems().some(w=>modsOf(w).includes(id))?'pending':'not-installed',mod:id});return;}
    for(const dep of mod.requires){
      if(!ids.includes(dep))issues.push({kind:'missing',mod:id,name:mod.name||id,requires:dep,installed:installed.some(m=>m.id===dep)});
      else if(ids.indexOf(dep)>position)issues.push({kind:'order',mod:id,name:mod.name||id,requires:dep});
    }
  });
  return issues;
}
const notLoaded=i=>i.kind==='not-installed'||i.kind==='pending';
const problemCount=()=>PZ.state.mods?analyze(activeTokens()).filter(i=>!notLoaded(i)).length:0;

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
  // Mods waiting for their Workshop download share one note.
  const pending=issues.filter(i=>i.kind==='pending').map(i=>i.mod);
  $('#mod-issues').replaceChildren(...issues.filter(i=>i.kind!=='pending').map(i=>{
    if(i.kind==='not-installed')return el('div',{class:'banner amber',role:'status'},icon('warning','banner-icon'),el('div',{class:'grow banner-text'},el('strong',{text:i.mod}),' ',t('issueNotInstalled')));

    const text=i.kind==='missing'?[el('strong',{text:t('issueMissingTitle')}),' ',t('issueMissing',{name:i.name}),el('span',{class:'mono',text:i.requires}),t(i.installed?'issueMissingTail':'issueMissingTailNotInstalled')]
      :[el('strong',{text:t('issueOrderTitle')}),' ',el('span',{class:'mono',text:i.requires}),' ',t('issueOrder',{name:i.name})];
    let action=null;
    if(canEdit&&i.kind==='missing'&&i.installed)action=el('button',{class:'btn small',type:'button',text:t('addName',{name:i.requires}),onclick:()=>{const list=activeTokens();const at=list.map(strip).indexOf(i.mod);list.splice(at,0,tokenFor(i.requires,list));setTokens(list);}});
    if(canEdit&&i.kind==='order')action=el('button',{class:'btn small',type:'button',text:t('moveBefore'),onclick:()=>{const list=activeTokens();const from=list.map(strip).indexOf(i.requires);const [dep]=list.splice(from,1);list.splice(list.map(strip).indexOf(i.mod),0,dep);setTokens(list);}});
    return el('div',{class:'banner coral',role:'alert'},icon('warning','banner-icon'),el('div',{class:'grow small'},text),action);
  }),pending.length?el('div',{class:'banner info',role:'status'},icon('info','banner-icon'),el('div',{class:'grow banner-text'},
    pending.map((id,n)=>[n?', ':'',el('strong',{text:id})]),' ',t(pending.length===1?'issuePendingDownload_one':'issuePendingDownload'))):'');
  const modsEdits=['Mods','WorkshopItems'].filter(k=>Object.hasOwn(PZ.state.edits.server||{},k));
  const pendingBox=$('#mods-pending');pendingBox.hidden=!modsEdits.length;
  if(modsEdits.length)pendingBox.replaceChildren(icon('info','banner-icon'),el('div',{class:'grow'},el('div',{class:'banner-title',text:t('modsUnsaved')}),el('div',{class:'banner-text',text:t('modsUnsavedText')})),el('a',{class:'btn amber-fill',href:'#settings/modified',text:t('reviewSave')}));
  renderWorkshop(ids,canEdit);
  renderTable(ids,issues,canEdit);
  renderOrder(tokens,issues,canEdit);
  PZ.updateBadges();
}

function renderWorkshop(ids,canEdit){
  const items=workshopItems();
  lookupWorkshopItems();
  $('#workshop-rows').replaceChildren(...(items.length?items.map(id=>{
    const info=workshopInfo[id];const mods=modsOf(id);
    const downloaded=PZ.state.mods.installed.some(m=>m.workshop===id);
    const title=info?.title||PZ.state.mods.installed.find(m=>m.workshop===id)?.name||t('untitled',{id});
    const modCells=mods.length?mods.map(mod=>el('div',{class:'ws-mod'},el('span',{class:'mono small',text:mod}),
      ids.includes(mod)?el('span',{class:'pill green',text:t('stateActive')})
        :canEdit?el('button',{class:'btn small',type:'button',text:t('activate'),'aria-label':t('activateName',{name:mod}),onclick:()=>addMods([mod])}):el('span',{class:'pill',text:t('notActive')})))
      :[el('span',{class:'small muted',text:t('modIdUnknown')+' · '}),el('a',{class:'small',href:steamUrl(id),target:'_blank',rel:'noopener noreferrer',text:t('findModId')})];
    const remove=canEdit?el('button',{class:'link small',type:'button',text:t('removeItem'),'aria-label':t('removeItemName',{name:title}),onclick:()=>{
      PZ.setEdit('server','WorkshopItems',workshopItems().filter(x=>x!==id).join(';'),PZ.field('server','WorkshopItems').value);
      if(mods.some(mod=>ids.includes(mod)))setTokens(activeTokens().filter(x=>!mods.includes(strip(x))));
    }}):null;
    return el('tr',{},
      el('td',{},el('a',{class:'mod-name',href:steamUrl(id),target:'_blank',rel:'noopener noreferrer',text:title}),el('div',{class:'mod-id',text:id})),
      el('td',{},el('div',{class:'ws-mods'},modCells)),
      el('td',{},el('span',{class:downloaded?'pill green':'pill amber',text:downloaded?t('downloaded'):t('pendingDownload')})),
      el('td',{class:'right'},remove));
  }):[el('tr',{},el('td',{colspan:4,class:'muted',text:t('noWorkshopItems')}))]));
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
    // Downloaded files of an item that is no longer in WorkshopItems can be deleted.
    const unused=canEdit&&m.workshop&&!workshopItems().includes(m.workshop)&&!PZ.state.mods.workshop.includes(m.workshop)&&!active&&!PZ.state.mods.active.map(strip).includes(m.id);
    const remove=unused?el('button',{class:'link small coral-text',type:'button',text:t('deleteFiles'),'aria-label':t('deleteFilesName',{name:m.name||m.id}),onclick:run(()=>deleteFiles(m))}):null;
    return el('tr',{class:missing.length?'problem':''},
      el('td',{},el('div',{class:'mod-name',text:m.name||m.id}),el('div',{class:'mod-id',text:m.id})),
      el('td',{class:m.workshop?'mono small':'small muted',text:m.workshop||t('localMod')}),
      el('td',{class:v.ok?(v.known?'':'muted'):'warn-text',text:v.text,title:v.ok?'':t('versionMismatch',{v:PZ.state.status?.version||''})}),
      el('td',{},m.requires.length?m.requires.map((dep,i)=>[i?', ':'',el('span',{class:'mono small'+(ids.includes(dep)?'':' coral-text'),text:dep}),ids.includes(dep)?'':el('span',{class:'small coral-text',text:' · '+t('notActive')})]):el('span',{class:'muted',text:'—'})),
      el('td',{},el('div',{class:'mod-state'},state,toggle,remove)));
  }):[el('tr',{},el('td',{colspan:5,class:'muted',text:PZ.state.mods.installed.length?t('noResults'):t('noInstalledMods')}))]));
}

function renderOrder(tokens,issues,canEdit){
  $('#load-order-help').textContent=canEdit?t('loadOrderHelp',{file:`${PZ.name}.ini`}):t('loadOrderReadonly');
  const list=$('#load-order');
  const move=(from,to)=>{const l=activeTokens();if(to<0||to>=l.length)return;const [x]=l.splice(from,1);l.splice(to,0,x);setTokens(l);};
  list.replaceChildren(...(tokens.length?tokens.map((token,index)=>{
    const id=strip(token);const problem=issues.some(i=>i.mod===id&&!notLoaded(i));
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
  const ids=activeTokens().map(strip);const items=workshopItems();
  const preview=el('div',{class:'ws-preview','aria-live':'polite'});
  let chosen=null,serial=0,timer;
  // Each Workshop item gets a checkbox (required items too) and each of its Mod IDs another one.
  function show(list,id){
    chosen={items:[],mods:[]};
    const main=list.find(x=>x.id===id)||{id,modIds:[],requires:[],found:false};
    // The lookup lists required items first; that order is kept when adding.
    const block=item=>{
      const required=item.id!==id;const order=list.indexOf(item);
      const box=el('input',{type:'checkbox',checked:true,disabled:!required});
      const modBoxes=item.modIds.map(mod=>{
        const active=ids.includes(mod);
        const b=el('input',{type:'checkbox',checked:!active,disabled:active});
        chosen.mods.push({mod,box:b,item:box,order});
        return el('label',{class:'check'},b,el('span',{class:'mono',text:mod}),active?el('span',{class:'small muted',text:' · '+t('alreadyActive')}):null);
      });
      chosen.items.push({id:item.id,box,order});
      const name=item.title||t('untitled',{id:item.id});
      return el('div',{class:'ws-item'},
        el('label',{class:'check ws-title'},box,el('span',{},el('strong',{text:name}),' ',el('span',{class:'mono small muted',text:item.id}),
          items.includes(item.id)?el('span',{class:'small muted',text:' · '+t('alreadyInWorkshop')}):null)),
        modBoxes.length?el('div',{class:'ws-mods'},modBoxes):el('p',{class:'small warn-text',text:item.found?t('noModIdsFound'):t('lookupFailed')}));
    };
    const deps=list.filter(x=>x.id!==id);
    preview.replaceChildren(
      el('p',{class:'small muted',text:t('activateThese')}),block(main),
      deps.length?el('p',{class:'small muted',text:t('alsoRequires')}):null,...deps.map(block),
      el('a',{class:'small',href:steamUrl(id),target:'_blank',rel:'noopener noreferrer',text:t('openInSteam')}));
  }
  const input=el('input',{name:'workshop',autocomplete:'off',spellcheck:'false',placeholder:'https://steamcommunity.com/sharedfiles/filedetails/?id=…',oninput:()=>{
    clearTimeout(timer);const id=parseWorkshop(input.value);const mine=++serial;chosen=null;
    if(!id){preview.replaceChildren();return;}
    preview.replaceChildren(el('p',{class:'small muted',text:t('lookingUp')}));
    timer=setTimeout(()=>lookup([id]).then(list=>{if(mine===serial)show(list,id);}).catch(()=>{if(mine===serial)show([],id);}),350);
  }});
  const values=await PZ.dialog({icon:'plus',title:t('addWorkshop'),wide:true,
    body:[el('label',{class:'stack'},el('span',{text:t('workshopLink')}),input,el('span',{class:'hint',text:t('workshopLinkHint')})),preview,
      el('label',{class:'stack'},el('span',{text:t('otherModIds')}),el('input',{name:'mods',autocomplete:'off',spellcheck:'false'}),el('span',{class:'hint',text:t('otherModIdsHint')}))],
    confirm:t('addSelected'),validate:f=>Boolean(parseWorkshop(f.elements.workshop.value))&&!/[";]/.test(f.elements.mods.value)});
  if(!values)return;
  const id=parseWorkshop(values.workshop);
  // Required items first so their mods load before the ones that need them.
  const byOrder=(a,b)=>a.order-b.order;
  const picked=chosen?chosen.items.filter(x=>x.box.checked).sort(byOrder).map(x=>x.id):[id];
  for(const w of picked)addWorkshop(w);
  const mods=chosen?chosen.mods.filter(x=>x.box.checked&&x.item.checked).sort(byOrder).map(x=>x.mod):[];
  mods.push(...values.mods.split(/[,\s]+/).map(x=>x.replace(/^\\+/,'')).filter(Boolean));
  if(mods.length)addMods(mods);
  PZ.toast(t('workshopAdded'),true);
}

async function deleteFiles(mod){
  const mods=PZ.state.mods.installed.filter(m=>m.workshop===mod.workshop).map(m=>m.id);
  const ok=await PZ.dialog({icon:'warning',tone:'danger',title:t('deleteFilesTitle',{name:mod.name||mod.id}),sub:`Workshop ${mod.workshop}`,
    body:[el('p',{text:t('deleteFilesText')}),el('p',{class:'small muted'},t('deleteFilesMods'),' ',mods.map((id,n)=>[n?', ':'',el('span',{class:'mono',text:id})]))],
    confirm:t('deleteFiles'),confirmClass:'danger'});
  if(!ok)return;
  const r=await api('mods/delete',{workshop:mod.workshop});
  PZ.toast(r.message,true);
  await load();
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
// The ID explanation starts open until it is closed once.
const help=$('#ids-help');
try{help.open=localStorage.getItem('pz-ids-help')!=='closed';}catch{help.open=true;}
help.addEventListener('toggle',()=>{try{localStorage.setItem('pz-ids-help',help.open?'open':'closed');}catch{}});
})();

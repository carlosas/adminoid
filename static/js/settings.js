'use strict';
(()=>{
const {t,$,$$,el,icon,api,run}=window.PZ;
const PZ=window.PZ;
const LISTS=['Mods','WorkshopItems','Map'];
let filter='all',selected=null,categories=[];

const fileName=scope=>({server:`${PZ.name}.ini`,sandbox:`${PZ.name}_SandboxVars.lua`,regions:`${PZ.name}_spawnregions.lua`,points:`${PZ.name}_spawnpoints.lua`})[scope]||scope;
const isList=(scope,f)=>scope==='server'&&LISTS.includes(f.key);
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const edited=(scope,f)=>Object.hasOwn(PZ.state.edits[scope]||{},f.key);
const pending=f=>f.applied_known&&!f.secret&&!same(f.applied,f.value);
const groupOf=(scope,category)=>scope==='regions'||scope==='points'||/^(Advanced|Avanzado)\b/.test(category)?'other':scope;

function display(scope,f,v){
  if(f.secret)return v?t('newPassword'):t('hiddenValue');
  if(v===null||v===undefined||v==='')return t('empty');
  if(Object.keys(f.choices).length&&f.choices[v]!==undefined)return f.choices[v];
  if(typeof v==='boolean')return v?t('yes'):t('no');
  if(isList(scope,f))return String(v).split(';').filter(Boolean).join(', ')||t('empty');
  if(typeof v==='number')return PZ.number(v,Number.isInteger(v)?0:Math.min(4,(String(v).split('.')[1]||'').length));
  return String(v);
}

function buildCategories(){
  const map=new Map();
  for(const [scope,doc] of Object.entries(PZ.state.config)){
    for(const f of doc.fields){
      const id=scope==='regions'||scope==='points'?'spawn':f.category;
      if(!map.has(id))map.set(id,{id,name:id==='spawn'?t('spawnCategory'):f.category,group:groupOf(scope,f.category),fields:[]});
      map.get(id).fields.push([scope,f]);
    }
  }
  const order={server:0,sandbox:1,other:2};
  categories=[...map.values()].sort((a,b)=>order[a.group]-order[b.group]||(a.id==='spawn')-(b.id==='spawn')||a.name.localeCompare(b.name));
}

function matches([scope,f]){
  if(filter==='modified'&&!edited(scope,f))return false;
  if(filter==='pending'&&!pending(f))return false;
  const q=$('#search').value.trim().toLocaleLowerCase();
  return !q||`${f.key} ${f.label} ${f.help} ${scope}`.toLocaleLowerCase().includes(q);
}

function renderFilter(){
  const count=PZ.editCount();
  const pendingCount=categories.reduce((a,c)=>a+c.fields.filter(([,f])=>pending(f)).length,0);
  const items=[['all',t('filterAll')],['modified',count?t('filterModifiedN',{n:count}):t('filterModified')],['pending',pendingCount?t('filterPendingN',{n:pendingCount}):t('filterPending')]];
  $('#settings-filter').replaceChildren(...items.map(([id,label])=>el('button',{type:'button','aria-pressed':String(filter===id),text:label,onclick:()=>{filter=id;render();}})));
}

function renderCategories(visible){
  const nav=$('#categories');let group=null;const nodes=[];
  for(const c of visible){
    if(c.group!==group){group=c.group;nodes.push(el('div',{class:'cat-group',text:t('group_'+group)}));}
    const changed=c.fields.some(([s,f])=>edited(s,f))||(c.id==='spawn'&&Object.keys(PZ.state.structures).length);
    const shown=c.id==='spawn'?'':c.fields.filter(matches).length;
    nodes.push(el('button',{type:'button',class:'cat-link','aria-current':String(c.id===selected),onclick:()=>{selected=c.id;filter='all';$('#search').value='';render();$('#fields').scrollIntoView({block:'start'});}},
      el('span',{text:c.name}),el('span',{class:'count'},changed?el('span',{class:'dot warn'}):null,String(shown))));
  }
  nav.replaceChildren(...nodes);
}

function visibleCategories(){
  const v=categories.filter(c=>c.id==='spawn'?filter==='all':c.fields.length>0);
  if(!v.some(c=>c.id===selected))selected=v[0]?.id;
  return v;
}

// Navigation, filters and review panel; cheap enough to redraw on every keystroke.
function renderChrome(){
  if(!PZ.state.config)return;
  renderFilter();renderCategories(visibleCategories());renderReview();
}

function render(){
  if(!PZ.state.config)return;
  renderChrome();
  const searching=$('#search').value.trim()||['modified','pending'].includes(filter);
  const sections=[];
  if(searching){
    for(const c of categories){
      if(c.id==='spawn')continue;
      const rows=c.fields.filter(matches);
      if(rows.length)sections.push(section(c,rows,true));
    }
    if(!sections.length)sections.push(el('div',{class:'card empty',text:filter==='modified'?t('noUnsaved'):filter==='pending'?t('noPendingRestart'):t('noResults')}));
  }else{
    const c=categories.find(x=>x.id===selected);
    if(c)sections.push(c.id==='spawn'?spawnSection(c):section(c,c.fields.filter(matches),false));
  }
  $('#fields').replaceChildren(...sections);
  const total=categories.reduce((a,c)=>a+c.fields.length,0);
  $('#settings-summary').textContent=t('settingsSummary',{options:total,files:Object.keys(PZ.state.config).length});
}

function section(c,rows,searching){
  const changes=rows.filter(([s,f])=>edited(s,f)).length;
  return el('section',{class:'card flush'},
    el('div',{class:'section-head'},el('h2',{text:c.name}),el('span',{class:'small muted',text:t(changes?'sectionCountChanged':'sectionCount',{n:rows.length,changed:changes})})),
    rows.map(([scope,f])=>row(scope,f)),
    searching&&rows.length<c.fields.length?el('div',{class:'section-foot'},t('showingOf',{n:rows.length,total:c.fields.length}),' · ',el('button',{class:'link',type:'button',text:t('viewAllIn',{category:c.name}),onclick:()=>{selected=c.id;filter='all';$('#search').value='';render();}})):null);
}

// Mods and WorkshopItems are edited from the Mods tab; here they are shown read-only.
const managedByMods=(scope,f)=>scope==='server'&&['Mods','WorkshopItems'].includes(f.key);

function row(scope,f){
  const node=el('div',{class:'row'});
  const id=`f-${scope}-${f.key}`.replace(/[^\w-]/g,'_');
  const text=el('div',{class:'row-text'});
  const update=()=>{
    const changed=edited(scope,f);
    node.classList.toggle('changed',changed);
    const badges=[];
    if(changed)badges.push(el('span',{class:'badge amber',text:t('modified')}));
    if(pending(f))badges.push(el('span',{class:'pill blue',text:t('pendingRestartBadge')}));
    if(f.readonly)badges.push(el('span',{class:'pill',text:t('protected')}));
    if(managedByMods(scope,f))badges.push(el('a',{class:'pill',href:'#mods',text:t('managedInMods')}));
    const help=el('p',{class:'row-help'},helpText(scope,f));
    if(changed){
      help.append(' ',el('br'),t('before'),': ',el('strong',{text:display(scope,f,f.value)}),' · ',el('button',{class:'link',type:'button',text:t('reset'),onclick:()=>{PZ.setEdit(scope,f.key,f.value,f.value);node.replaceWith(row(scope,f));}}));
    }else if(pending(f)){
      help.append(' ',el('br'),t('inUse'),': ',el('strong',{text:display(scope,f,f.applied)}));
    }
    const labelNode=f.kind==='boolean'||Object.keys(f.choices).length&&useSegments(f)?el('span',{class:'label',id:id+'-l',text:f.label}):el('label',{for:id,text:f.label});
    text.replaceChildren(el('div',{class:'row-title'},labelNode,...badges),el('div',{class:'row-key',text:`${scope} · ${f.key}`}),help);
  };
  const set=value=>{PZ.setEdit(scope,f.key,value,f.value);update();};
  node.append(text,el('div',{class:'row-control'},control(scope,f,id,set)));
  update();
  return node;
}

// Game comments repeat the option list and bounds that the control already shows; keep the prose and the default.
function helpText(scope,f){
  const lines=(f.help||'').split('\n');let fallback=null;
  const kept=lines.filter(line=>{
    const bounds=line.match(/^\s*(?:Min:\s*\S+\s+Max:\s*\S+\s*)?Default:\s*(\S+)\s*$/);
    if(bounds){fallback=bounds[1];return false;}
    if(Object.keys(f.choices).length&&/^\s*\d+\s*=/.test(line))return false;
    return line.trim();
  });
  let text=kept.join('\n').replace(/\s*Min:\s*\S+\s+Max:\s*\S+(\s+Default:\s*(\S+))?\s*$/,(m,d,value)=>{if(value)fallback=value;return '';});
  if(fallback!==null){
    const parsed=f.kind==='boolean'?fallback==='true':Number(fallback);
    const value=f.kind==='string'?fallback:Number.isNaN(parsed)?fallback:parsed;
    text+=(text?'\n':'')+t('defaultValue',{value:display(scope,f,value)});
  }
  return text||t('noHelp');
}

const useSegments=f=>{const labels=Object.values(f.choices);return labels.length<=6&&labels.join('').length<=64;};

function control(scope,f,id,set){
  const value=PZ.effective(scope,f.key);
  const disabled=f.readonly||managedByMods(scope,f);
  const choices=Object.entries(f.choices);
  if(choices.length){
    if(useSegments(f)){
      const group=el('div',{class:'segmented choice',role:'radiogroup','aria-labelledby':id+'-l'});
      const paint=()=>{const v=String(PZ.effective(scope,f.key));for(const b of group.children){b.setAttribute('aria-checked',String(b.dataset.value===v));b.tabIndex=b.dataset.value===v?0:-1;b.classList.toggle('saved',b.dataset.value===String(f.value));}};
      for(const [v,label] of choices)group.append(el('button',{type:'button',role:'radio','data-value':v,text:label,disabled,onclick:()=>{set(Number(v));paint();}}));
      group.addEventListener('keydown',e=>{
        if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
        e.preventDefault();const items=[...group.children];const i=items.indexOf(document.activeElement);
        const next=items[(i+(['ArrowRight','ArrowDown'].includes(e.key)?1:items.length-1))%items.length];next.click();next.focus();
      });
      paint();return group;
    }
    const select=el('select',{id,class:'input',disabled});
    for(const [v,label] of choices)select.append(el('option',{value:v,text:label}));
    select.value=String(value);select.onchange=()=>set(Number(select.value));
    return select;
  }
  if(f.kind==='boolean'){
    const label=el('span',{class:'switch-label',text:value?t('enabled'):t('disabled')});
    const sw=el('button',{type:'button',role:'switch',class:'switch',id,'aria-checked':String(!!value),'aria-labelledby':id+'-l',disabled,onclick:()=>{
      const v=sw.getAttribute('aria-checked')!=='true';sw.setAttribute('aria-checked',String(v));label.textContent=v?t('enabled'):t('disabled');set(v);}});
    return [sw,label];
  }
  if(isList(scope,f)){
    const area=el('textarea',{id,class:'input',disabled,spellcheck:'false'});
    area.value=String(value||'').split(';').filter(Boolean).join('\n');
    area.oninput=()=>set(area.value.split('\n').map(s=>s.trim()).filter(Boolean).join(';'));
    return [area,el('span',{class:'small muted',text:t('onePerLine')})];
  }
  if(f.kind==='number'||f.kind==='integer'){
    const hasRange=f.minimum!==null&&f.maximum!==null;
    const num=el('input',{id,class:'num',type:'number',disabled,step:f.kind==='integer'?'1':'any',min:f.minimum??undefined,max:f.maximum??undefined});
    num.value=value??'';
    const parse=v=>v===''?null:Number(v);
    const hint=hasRange?el('span',{class:'range-hint',text:`${PZ.number(f.minimum,f.kind==='integer'?0:undefined)} – ${PZ.number(f.maximum,f.kind==='integer'?0:undefined)}`}):null;
    if(f.kind==='number'&&hasRange&&f.maximum-f.minimum<=10000){
      const range=el('input',{type:'range',min:f.minimum,max:f.maximum,step:(f.maximum-f.minimum)<=10?0.05:(f.maximum-f.minimum)/200,disabled,'aria-label':f.label});
      range.value=value??f.minimum;
      range.oninput=()=>{const v=Number(Number(range.value).toFixed(2));num.value=v;set(v);};
      num.oninput=()=>{range.value=num.value;set(parse(num.value));};
      return [range,num,hint];
    }
    num.oninput=()=>set(parse(num.value));
    if(f.kind==='integer'){
      const step=d=>()=>{let v=(parse(num.value)??0)+d;if(hasRange)v=Math.min(f.maximum,Math.max(f.minimum,v));num.value=v;set(v);};
      return [el('div',{class:'stepper'},el('button',{class:'btn',type:'button',text:'−','aria-label':t('decrease'),disabled,onclick:step(-1)}),num,el('button',{class:'btn',type:'button',text:'+','aria-label':t('increase'),disabled,onclick:step(1)})),hint];
    }
    return [num,hint];
  }
  const input=el('input',{id,class:'input',type:f.secret?'password':'text',disabled,autocomplete:f.secret?'new-password':'off'});
  input.value=f.secret?(edited(scope,f)?value:''):String(value??'');
  if(f.secret)input.placeholder=f.configured?t('configuredKeep'):t('notConfigured');
  input.oninput=()=>{if(f.secret&&input.value==='')PZ.setEdit(scope,f.key,f.value,f.value);else set(input.value);};
  return input;
}

function renderReview(){
  const count=PZ.editCount();
  const list=$('#review-list');const cards=[];
  for(const [scope,items] of Object.entries(PZ.state.edits)){
    for(const [key,value] of Object.entries(items)){
      const f=PZ.field(scope,key);if(!f)continue;
      cards.push(el('div',{class:'change'},
        el('div',{class:'change-title'},el('span',{text:f.label}),el('button',{class:'icon-btn small',type:'button','aria-label':t('resetName',{name:f.label}),onclick:()=>{PZ.setEdit(scope,key,f.value,f.value);render();}},icon('close'))),
        el('div',{class:'row-key',text:`${fileName(scope)} · ${key}`}),
        el('div',{class:'diff'},el('span',{class:'old',text:display(scope,f,f.value)}),el('span',{class:'arrow','aria-hidden':'true',text:'→'}),el('span',{class:'new',text:display(scope,f,value)}))));
    }
  }
  for(const scope of Object.keys(PZ.state.structures))cards.push(el('div',{class:'change'},el('div',{class:'change-title'},el('span',{text:scope==='regions'?t('regions'):t('professionPoints')})),el('div',{class:'row-key',text:fileName(scope)}),el('div',{class:'small muted',text:t('structureChanged')})));
  list.replaceChildren(...(cards.length?cards:[el('p',{class:'small muted',text:t('noUnsaved')})]));
  const counter=$('#review-count');counter.textContent=count;counter.classList.toggle('zero',!count);
  const save=$('#save-changes');save.disabled=!count;save.textContent=count?t('saveN',{n:count}):t('save');
  $('#discard-changes').disabled=!count;
}

async function save(){
  const revisions=Object.fromEntries(Object.entries(PZ.state.config).map(([k,v])=>[k,v.revision]));
  const data=await api('preview',{changes:PZ.state.edits,structures:PZ.state.structures,revisions});
  if(!data.differences.length){PZ.toast(t('noChanges'),true);return;}
  const mine=new Set(Object.entries(PZ.state.edits).flatMap(([s,items])=>Object.keys(items).map(k=>s+'/'+k)));
  const extra=data.differences.filter(d=>!mine.has(d.file+'/'+d.key));
  // Linked values (zombie population, structure rewrites) are shown before writing anything.
  if(extra.length){
    const text=data.differences.map(d=>`${fileName(d.file)} · ${d.key}\n  ${t('before')}: ${JSON.stringify(d.before)}\n  ${t('after')}: ${JSON.stringify(d.after)}`).join('\n\n');
    const ok=await PZ.dialog({icon:'info',title:t('reviewChanges'),body:[el('p',{class:'dlg-text',text:t('linkedChanges')}),el('pre',{class:'dlg-pre',text})],confirm:t('confirmSave')});
    if(!ok)return;
  }
  const r=await api('commit',{id:data.id});
  PZ.toast(r.message,true);
  await load();
  run(PZ.refreshStatus)();
}

async function discard(){
  if(!await PZ.confirmDialog(t('discardTitle'),t('discardConfirm'),t('discard'),true))return;
  await load();
}

async function load(){
  PZ.state.config=await api('config');PZ.state.edits={};PZ.state.structures={};
  buildCategories();
  const errors=Object.entries(PZ.state.config).filter(([,d])=>d.error);
  $('#settings-errors').replaceChildren(...errors.map(([scope,d])=>el('div',{class:'banner amber'},icon('warning','banner-icon'),el('div',{class:'grow'},el('div',{class:'banner-title',text:fileName(scope)}),el('div',{class:'banner-text',text:d.error})))));
  PZ.changed();
  if(PZ.current()==='settings')render();
}

// After a restart the applied values change; refresh them without losing unsaved edits.
async function refreshApplied(){
  if(!PZ.state.config)return;
  const fresh=await api('config');
  for(const [scope,doc] of Object.entries(fresh)){
    const latest=new Map((doc.fields||[]).map(f=>[f.key,f]));
    for(const f of PZ.state.config[scope]?.fields||[]){
      const n=latest.get(f.key);
      if(n){f.applied=n.applied;f.applied_known=n.applied_known;}
    }
  }
  PZ.changed();
  if(PZ.current()==='settings')render();
}

// Spawn regions and points: structural editors kept from the previous panel.
function spawnSection(c){
  const box=el('section',{class:'card flush'},el('div',{class:'section-head'},el('h2',{text:c.name}),el('span',{class:'small muted',text:t('spawnWarning')})));
  for(const scope of ['regions','points']){
    const doc=PZ.state.config[scope];
    if(!doc)continue;
    if(doc.structure==null){box.append(el('div',{class:'spawn-block'},el('h3',{text:fileName(scope)}),el('p',{class:'small muted',text:doc.error||t('spawnReadonly')})));continue;}
    const value=PZ.state.structures[scope]??structuredClone(doc.structure);
    const mark=()=>{PZ.state.structures[scope]=value;delete PZ.state.edits[scope];PZ.changed();};
    const again=()=>{mark();render();};
    const block=el('div',{class:'spawn-block'},el('h3',{text:scope==='regions'?t('regions'):t('professionPoints')}));
    if(scope==='regions'){
      value.forEach((r,index)=>{
        const kind=r.serverfile!==undefined?'serverfile':'file';
        const type=el('select',{'aria-label':t('regionType')},['file','serverfile'].map(k=>el('option',{value:k,text:k})));
        type.value=kind;
        type.onchange=()=>{const old=r.file??r.serverfile;delete r.file;delete r.serverfile;r[type.value]=old;again();};
        block.append(el('div',{class:'spawn-row'},
          ['name',kind].map(key=>el('label',{},key,el('input',{value:r[key]||'',oninput:e=>{r[key]=e.target.value;mark();}}))),
          el('label',{},t('type'),type),
          el('button',{class:'btn small',type:'button',text:t('remove'),onclick:()=>{value.splice(index,1);again();}})));
      });
      block.append(el('div',{class:'actions'},el('button',{class:'btn small',type:'button',text:t('addRegion'),onclick:()=>{value.push({name:t('newRegion'),file:'media/maps/Muldraugh, KY/spawnpoints.lua'});again();}})));
    }else{
      for(const [profession,rows] of Object.entries(value)){
        block.append(el('h4',{text:profession}));
        rows.forEach((r,index)=>block.append(el('div',{class:'spawn-row'},
          ['worldX','worldY','posX','posY','posZ'].map(key=>el('label',{},key,el('input',{type:'number',step:'1',value:r[key]??'',oninput:e=>{if(e.target.value==='')delete r[key];else r[key]=Number(e.target.value);mark();}}))),
          el('button',{class:'btn small',type:'button',text:t('removePoint'),onclick:()=>{rows.splice(index,1);again();}}))));
        block.append(el('div',{class:'actions'},
          el('button',{class:'btn small',type:'button',text:t('addPoint',{profession}),onclick:()=>{rows.push({worldX:0,worldY:0,posX:0,posY:0});again();}}),
          el('button',{class:'btn small danger-text',type:'button',text:t('removeProfession'),onclick:()=>{delete value[profession];again();}})));
      }
      block.append(el('div',{class:'actions'},el('button',{class:'btn small',type:'button',text:t('addProfession'),onclick:run(async()=>{
        const r=await PZ.dialog({icon:'plus',title:t('addProfession'),body:[el('label',{class:'stack'},el('span',{text:t('professionPrompt')}),el('input',{name:'profession',autocomplete:'off',spellcheck:'false'}))],
          confirm:t('add'),validate:f=>/^[A-Za-z_][A-Za-z_0-9]*$/.test(f.elements.profession.value)&&!Object.hasOwn(value,f.elements.profession.value)});
        if(r){value[r.profession]=[];again();}
      })})));
    }
    box.append(block);
  }
  return box;
}

PZ.views.settings={title:'configuration',load,refreshApplied,render,fileName,display,
  enter(arg,entering){
    if(arg==='pending'||arg==='modified'){filter=arg;$('#search').value='';}
    if(!PZ.state.config)run(load)();else render();
  }};
PZ.onChange(()=>{if(PZ.current()==='settings')renderChrome();else if(PZ.state.config)renderReview();});
$('#search').addEventListener('input',()=>render());
$('#save-changes').addEventListener('click',run(save));
$('#discard-changes').addEventListener('click',run(discard));
document.addEventListener('keydown',e=>{
  if(e.key==='/'&&PZ.current()==='settings'&&!e.target.closest('input,textarea,select,[contenteditable]')&&!document.querySelector('dialog[open]')){e.preventDefault();$('#search').focus();}
});
})();

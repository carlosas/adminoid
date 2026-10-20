'use strict';
// Shared state and helpers for every view. Views register in PZ.views and are wired in main.js.
(()=>{
const {t,lang}=window.PZ_I18N;
const PZ=window.PZ={t,lang,views:{},state:{status:null,players:null,config:null,edits:{},structures:{},job:null,mods:null},
  name:document.querySelector('meta[name=server-name]').content};
let csrf=document.querySelector('meta[name=csrf]').content;

const $=(s,root=document)=>root.querySelector(s);
const $$=(s,root=document)=>[...root.querySelectorAll(s)];
// el('div',{class:'x',onclick:fn},child,'text') — attributes starting with "on" become listeners.
function el(tag,attrs,...children){
  const n=document.createElement(tag);
  for(const [k,v] of Object.entries(attrs||{})){
    if(v===undefined||v===null||v===false)continue;
    if(k.startsWith('on'))n.addEventListener(k.slice(2),v);
    else if(k==='class')n.className=v;
    else if(k==='text')n.textContent=v;
    else if(k in n&&!['list','type','form'].includes(k)&&typeof v!=='string')n[k]=v;
    else n.setAttribute(k,v===true?'':v);
  }
  for(const c of children.flat(Infinity)){if(c!==undefined&&c!==null&&c!==false)n.append(c instanceof Node?c:String(c));}
  return n;
}
function icon(name,cls){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('aria-hidden','true');if(cls)svg.setAttribute('class',cls);
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-'+name);
  svg.append(use);return svg;
}
Object.assign(PZ,{$,$$,el,icon});

// Formatting
const locale=lang==='es'?'es-ES':'en-GB';
const number=(v,digits=0)=>new Intl.NumberFormat(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits}).format(v);
function bytes(n){
  if(!Number.isFinite(n))return ['—',''];
  const units=['B','KiB','MiB','GiB','TiB'];let i=0;
  while(n>=1024&&i<units.length-1){n/=1024;i++;}
  return [number(n,i>=3?2:0),units[i]];
}
function duration(seconds){
  seconds=Math.max(0,Math.round(seconds));
  if(seconds<60)return t('durSeconds',{n:seconds});
  const m=Math.floor(seconds/60),h=Math.floor(m/60),d=Math.floor(h/24);
  if(d)return t('durDays',{d,h:h%24});
  if(h)return t('durHours',{h,m:m%60});
  return t('durMinutes',{m});
}
const ago=ts=>t('ago',{time:duration(Date.now()/1000-ts)});
const clock=ts=>new Date(ts*1000).toLocaleTimeString(locale,{hour:'2-digit',minute:'2-digit'});
const clockPrecise=ts=>{const d=new Date(ts*1000);return d.toLocaleTimeString(locale,{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})+'.'+String(d.getMilliseconds()).padStart(3,'0');};
function dayLabel(ts){
  const d=new Date(ts*1000),today=new Date();const y=new Date();y.setDate(today.getDate()-1);
  const same=(a,b)=>a.toDateString()===b.toDateString();
  const day=same(d,today)?t('today'):same(d,y)?t('yesterday'):d.toLocaleDateString(locale,{day:'numeric',month:'short'});
  return `${day}, ${clock(ts)}`;
}
const initial=name=>(name.trim()[0]||'?').toUpperCase();
const tone=name=>{let h=0;for(const c of name)h=(h*31+c.charCodeAt(0))>>>0;return ['','c1','c2','c3'][h%4];};
Object.assign(PZ,{number,bytes,duration,ago,clock,clockPrecise,dayLabel,initial,tone});

// API
async function api(path,body){
  const opts={headers:{}};
  if(body!==undefined){opts.method='POST';opts.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};opts.body=JSON.stringify(body);}
  const r=await fetch('/api/'+path,opts);
  let value={};try{value=await r.json();}catch{}
  if(!r.ok){
    if(r.status===401)PZ.showLogin();
    const error=Error(value.error||t('requestError'));error.status=r.status;throw error;
  }
  return value;
}
PZ.api=api;
PZ.setCsrf=v=>{csrf=v;};

let toastTimer;
function toast(text,ok=false){
  const n=$('#toast');n.textContent=text;n.className=ok?'ok':'';n.hidden=false;
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>n.hidden=true,ok?5000:10000);
}
const run=fn=>async(...args)=>{try{return await fn(...args);}catch(e){toast(e.message);}};
Object.assign(PZ,{toast,run});

// Dialog: resolves with the form values when confirmed, or null when cancelled.
function dialog({icon:name='info',tone:look='',title,sub,body=[],confirm,confirmClass='primary',cancel=t('cancel'),validate,wide=false}){
  const d=$('#dialog');
  return new Promise(resolve=>{
    const ok=el('button',{class:'btn '+confirmClass,value:'ok',text:confirm});
    const form=el('form',{method:'dialog'},
      el('div',{class:'dlg-head'},el('span',{class:'dlg-icon '+look},icon(name)),el('div',{},el('h2',{id:'dlg-title',text:title}),sub?el('div',{class:'dlg-sub',text:sub}):null)),
      ...body,
      el('div',{class:'dlg-actions'},cancel?el('button',{class:'btn ghost',value:'cancel',type:'button',text:cancel,onclick:()=>d.close('cancel')}):null,confirm?ok:null));
    const check=()=>{if(validate)ok.disabled=!validate(form);};
    form.addEventListener('input',check);check();
    d.replaceChildren(form);d.setAttribute('aria-labelledby','dlg-title');
    d.classList.toggle('wide',wide);
    d.returnValue='';
    d.onclose=()=>{
      if(d.returnValue==='ok'){const values={};for(const [k,v] of new FormData(form))values[k]=v;resolve(values);}
      else resolve(null);
    };
    form.onsubmit=e=>{if(validate&&!validate(form)){e.preventDefault();}};
    d.showModal();
    const first=form.querySelector('input,textarea');if(first)first.focus();else ok.focus();
  });
}
PZ.dialog=dialog;
PZ.confirmDialog=(title,text,confirm,danger=false)=>dialog({icon:danger?'warning':'info',tone:danger?'danger':'',title,body:[el('p',{class:'dlg-text',text})],confirm,confirmClass:danger?'danger':'primary'});

// Unsaved settings edits are shared by Settings and Mods.
PZ.editCount=()=>Object.values(PZ.state.edits).reduce((a,d)=>a+Object.keys(d).length,0)+Object.keys(PZ.state.structures).length;
PZ.setEdit=(scope,key,value,saved)=>{
  const edits=PZ.state.edits;edits[scope]??={};
  if(value===saved)delete edits[scope][key];else edits[scope][key]=value;
  if(!Object.keys(edits[scope]).length)delete edits[scope];
  PZ.changed();
};
PZ.field=(scope,key)=>PZ.state.config?.[scope]?.fields.find(f=>f.key===key);
PZ.effective=(scope,key)=>{const e=PZ.state.edits[scope];return e&&Object.hasOwn(e,key)?e[key]:PZ.field(scope,key)?.value;};
const listeners=new Set();
PZ.onChange=fn=>listeners.add(fn);
PZ.changed=()=>{for(const fn of listeners)fn();updateBadges();};

// Router
let current=null;
function route(){
  if($('#application').hidden)return;
  const [name,arg]=(location.hash.slice(1)||'overview').split('/');
  const view=PZ.views[name]?name:'overview';
  if(current&&current!==view)PZ.views[current].leave?.();
  const entering=current!==view;current=view;
  for(const section of $$('.view'))section.hidden=section.id!=='view-'+view;
  for(const link of $$('[data-view]')){
    if(link.dataset.view===view)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  }
  document.title=`${t(PZ.views[view].title)} · Adminoid`;
  PZ.views[view].enter?.(arg,entering);
  if(entering)$('#content').scrollTop=0,window.scrollTo(0,0);
}
PZ.route=route;
PZ.current=()=>current;
PZ.leaveView=()=>{if(current)PZ.views[current].leave?.();current=null;};
PZ.go=hash=>{if(location.hash==='#'+hash)route();else location.hash=hash;};
window.addEventListener('hashchange',route);

// Status and players, shared by the sidebar, overview and dialogs.
PZ.stateLabel=s=>['active','inactive','failed','activating','deactivating'].includes(s)?t(s):(s||t('unknown'));
PZ.stateTone=s=>s==='active'?'ok':s==='failed'?'error':['activating','deactivating','reloading'].includes(s)?'warn':'unknown';
async function refreshStatus(){
  PZ.state.status=await api('status');PZ.state.statusTime=Date.now();
  renderServerCard();updateBadges();renderPending();PZ.views[current]?.status?.();
}
async function refreshPlayers(){
  PZ.state.players=await api('players');renderServerCard();PZ.views[current]?.status?.();
  return PZ.state.players;
}
function renderServerCard(){
  const s=PZ.state.status;if(!s)return;
  const p=PZ.state.players;const job=PZ.state.job;
  let look=PZ.stateTone(s.state),text=PZ.stateLabel(s.state);
  if(job&&job.state==='running'){look='warn';text=t('jobRunning_'+job.action);}
  else if(s.state==='active'&&p)text=p.count===null?t('onlineUnknownPlayers'):t('onlinePlayers',{count:p.count});
  for(const card of $$('.server-card'))card.dataset.tone=look;
  for(const n of $$('.server-summary'))n.textContent=text;
  const dot=$('.server-summary-mobile .dot');if(dot)dot.className='dot '+(look==='unknown'?'':look);
}
// Saved changes that need a restart, shown on the overview and in settings.
function renderPending(){
  const s=PZ.state.status;if(!s)return;
  const files=s.pending||[];
  const show=s.applied_known&&files.length&&s.state==='active';
  const names=files.map(f=>f==='server'?`${PZ.name}.ini`:t('file_'+f));
  const count=Object.values(s.pending_changes||{}).reduce((a,n)=>a+(n||0),0);
  for(const banner of $$('.pending-banner')){
    banner.hidden=!show;
    if(show)banner.querySelector('.pending-text').textContent=t('pendingText',{files:names.join(t('and')),count});
  }
}
Object.assign(PZ,{refreshStatus,refreshPlayers,renderServerCard,renderPending});

function updateBadges(){
  const set=(name,value)=>{const b=$(`[data-badge="${name}"]`);if(!b)return;b.hidden=!value;b.textContent=value||'';};
  set('settings',PZ.editCount());
  set('maintenance',PZ.state.job?.state==='running'?'•':0);
  set('mods',PZ.state.mods?PZ.views.mods.problemCount():0);
}
PZ.updateBadges=updateBadges;

// Operations: confirmation dialog, then progress is followed by watchJob.
const OP_STYLE={start:['play','',''],stop:['stop','danger','danger'],restart:['restart','amber','amber-fill'],backup:['backup','',''],restore:['restart','danger','danger'],update:['download','amber','amber-fill']};
async function operation(action,backup){
  if(PZ.state.job?.state==='running'){toast(t('operationRunning'));return;}
  const [iconName,look,cls]=OP_STYLE[action];
  if(action==='start'){
    if(!await dialog({icon:iconName,title:t('confirmTitle_start'),body:[el('p',{class:'dlg-text',text:t('startText')})],confirm:t('confirmButton_start'),confirmClass:'primary'}))return;
  }else{
    let players=null;
    try{players=await refreshPlayers();}catch{players={count:null,names:[]};}
    const active=PZ.state.status?.state==='active';
    const box=players.count===null
      ?el('div',{class:'dlg-players amber'},el('strong',{text:t('cannotCheckPlayers')}))
      :players.count>0
        ?el('div',{class:'dlg-players amber'},el('strong',{text:t('playersConnectedCount',{count:players.count})}),el('div',{class:'names'},players.names.map(n=>el('span',{text:n}))))
        :el('div',{class:'dlg-players'},el('span',{class:'muted',text:active?t('noPlayersConnected'):t('serverNotRunning')}));
    const bullets=t('consequences_'+action+(active?'':'_stopped')).split('|').filter(Boolean);
    if(players.count>0&&action!=='start')bullets.unshift(t('willWarnPlayers'));
    const body=[box,el('ul',{class:'dlg-list'},bullets.map(b=>el('li',{text:b})))];
    let sub;
    if(action==='restore'){
      const b=PZ.state.backups?.find(x=>x.name===backup);
      sub=b?`${backup} · ${bytes(b.size).join(' ')}`:backup;
      body.push(el('label',{class:'stack'},el('span',{},t('typeToConfirm1'),el('strong',{class:'mono',text:PZ.name}),t('typeToConfirm2')),el('input',{name:'confirm',autocomplete:'off',spellcheck:'false'})));
    }
    const b=action==='restore'&&PZ.state.backups?.find(x=>x.name===backup);
    const title=action==='restore'&&b?t('confirmTitle_restore_date',{date:dayLabel(b.mtime)}):t('confirmTitle_'+action);
    const confirmed=await dialog({icon:iconName,tone:look,title,sub,body,confirm:t('confirmButton_'+action+(action==='restore'&&!active?'_stopped':'')),confirmClass:cls||'primary',
      validate:action==='restore'?form=>form.elements.confirm.value.trim()===PZ.name:undefined});
    if(!confirmed)return;
  }
  const job=await api('operations',{action,backup,confirmed:true});
  try{localStorage.setItem('pz-job',job.id);}catch{}
  watchJob(job.id);
  PZ.go('maintenance');
}
let jobTimer=null;
async function watchJob(id){
  clearTimeout(jobTimer);
  try{
    const job=await api('operations/'+id);
    setJob(job);
    if(job.state==='running'){jobTimer=setTimeout(()=>watchJob(id),1500);return;}
    try{localStorage.removeItem('pz-job');}catch{}
    toast(job.message,job.state==='done');
    await Promise.allSettled([refreshStatus(),PZ.views.maintenance.loadBackups(),PZ.views.maintenance.loadUpdate(),PZ.views.overview.loadActivity(),PZ.views.settings.refreshApplied()]);
  }catch(e){
    if(e.status===404){
      // The panel restarted mid-operation; show the last known record.
      try{localStorage.removeItem('pz-job');}catch{}
      const last=await api('last-operation').catch(()=>({}));
      if(last.id===id&&last.state==='running')last.state='lost';
      setJob(last.id?last:null);
    }else jobTimer=setTimeout(()=>watchJob(id),4000);
  }
}
function setJob(job){PZ.state.job=job;renderServerCard();updateBadges();PZ.views.maintenance.renderJob();PZ.views.overview.renderJob();}
Object.assign(PZ,{operation,watchJob,setJob});
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-op]');
  if(b&&!b.disabled){e.preventDefault();run(operation)(b.dataset.op);}
});
})();

'use strict';
(()=>{
const {t,$,$$,el,icon,api,run,bytes,duration,ago}=window.PZ;
const PZ=window.PZ;
let filter='all',expanded=false,timer=null;
const PAGE=6;
const KIND={automatic:['blue','kindAutomatic'],manual:['green','kindManual'],rescue:['coral','kindRescue'],other:['','kindOther']};

function renderJob(){
  const card=$('#operation-card');const job=PZ.state.job;
  clearInterval(timer);
  const recent=job&&(job.state==='running'||job.state==='lost'||Date.now()/1000-(job.finished||job.started||0)<1800);
  card.hidden=!recent;
  renderService();
  if(!recent)return;
  card.className='card operation'+(job.state==='done'?' done':job.state==='error'||job.state==='lost'?' error':'');
  const done=new Set(job.done||[]);
  const current=(job.steps||[]).indexOf(job.step);
  const steps=(job.steps||[]).map((step,i)=>{
    let state='pending';
    if(done.has(step))state='done';
    else if(step===job.step)state=job.state==='running'?'running':'failed';
    else if(job.state==='done'||i<current)state='skipped';
    const marker=state==='done'?icon('check'):el('span',{class:'marker'});
    return el('li',{class:state},el('div',{class:'bar'}),el('div',{class:'label'},marker,t('step_'+step),state==='skipped'?el('span',{class:'sr-only',text:t('skipped')}):null));
  });
  const who=job.user?t('startedBy',{user:job.user,time:ago(job.started)}):t('startedAt',{time:ago(job.started)});
  const meta=job.state==='running'?t('opRunning')+' · '+who:job.state==='done'?t('opDone')+' · '+who:job.state==='lost'?t('opLost'):t('opFailed')+' · '+who;
  const title=t((job.state==='running'?'jobRunning_':job.state==='done'?'jobDone_':'jobFailed_')+job.action);
  const timerNode=el('span',{class:'op-timer'});
  const elapsed=()=>{const end=job.state==='running'?Date.now()/1000:(job.finished||Date.now()/1000);const s=Math.max(0,Math.round(end-job.started));timerNode.textContent=`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;};
  elapsed();
  if(job.state==='running')timer=setInterval(elapsed,1000);
  card.replaceChildren(
    el('div',{class:'op-head'},el('div',{},el('div',{class:'op-meta',text:meta}),el('h2',{text:title})),
      el('div',{class:'actions'},timerNode,job.state!=='running'?el('button',{class:'icon-btn small',type:'button','aria-label':t('dismiss'),onclick:()=>{PZ.setJob(null);}},icon('close')):null)),
    el('ol',{class:'steps'},steps),
    el('div',{class:'op-detail',text:job.state==='lost'?t('opLostDetail'):job.message||''}));
}

function renderService(){
  const s=PZ.state.status;const running=PZ.state.job?.state==='running';
  const active=s&&['active','activating'].includes(s.state);
  for(const b of $$('#view-maintenance [data-op]')){
    const op=b.dataset.op;
    b.disabled=running||(op==='start'&&active)||((op==='stop'||op==='restart')&&!active);
  }
  for(const b of $$('.pending-banner [data-op], #view-overview [data-op]'))b.disabled=running;
  $('#service-note').textContent=running?t('lockedWhileRunning',{action:t('jobRunning_'+PZ.state.job.action).toLowerCase()}):s?t('serviceState',{state:PZ.stateLabel(s.state)}):'';
  if(s){
    const v=s.version||'';
    $('#version-build').textContent=/^42\./.test(v)?'Build 42':v||t('unknown');
    $('#version-full').textContent=/^42\./.test(v)?v:'';
  }
  renderUpdate();renderModsUpdate();
}

let update=null,checking=false;
async function loadUpdate(refresh){
  checking=true;renderUpdate();
  try{update=await api('game-update'+(refresh===true?'?refresh=1':''));}
  catch{update={available:null};}
  finally{checking=false;renderUpdate();}
}
function renderUpdate(){
  const u=update||{};const running=PZ.state.job?.state==='running';
  const [color,key]=checking?['','updateChecking']:u.available===false?['green','upToDate']:u.available?['amber','updateAvailable']:['','updateUnknown'];
  const state=$('#update-state');state.className='pill '+color;state.textContent=t(key);
  $('#update-detail').textContent=checking?'':u.available?(u.published?t('updatePublished',{date:PZ.dayLabel(u.published)}):''):
    u.available===false?t('updateChecked',{time:ago(u.checked)}):update?t('updateUnknownDetail'):'';
  // The update button only shows when Steam has a newer build, or when it could not be asked.
  $('#update-box').hidden=!update||u.available===false;
  $('#update-refresh').disabled=checking||running;
}

// Workshop items with a newer version on Steam than the one the server downloaded; shared with the Mods view.
let checkingMods=false;
async function loadModsUpdate(refresh){
  checkingMods=true;renderModsUpdate();
  try{PZ.state.modsUpdate=await api('mods-update'+(refresh===true?'?refresh=1':''));}
  catch{PZ.state.modsUpdate={items:PZ.state.modsUpdate?.items||[],available:null};}
  finally{checkingMods=false;renderModsUpdate();}
}
function renderModsUpdate(){
  const u=PZ.state.modsUpdate;const running=PZ.state.job?.state==='running';
  const outdated=(u?.items||[]).filter(i=>i.available);
  // Without Workshop items there is nothing to check.
  $('#mods-update-card').hidden=!u?.items?.length;
  const [color,key]=checkingMods?['','updateChecking']:u?.available===false?['green','upToDate']:u?.available?['amber','modsUpdateAvailable']:['','updateUnknown'];
  const state=$('#mods-update-state');state.className='pill '+color;state.textContent=t(key,{n:outdated.length});
  $('#mods-update-detail').textContent=checkingMods||!u?'':u.available===false?(u.checked?t('modsUpdateChecked',{time:ago(u.checked)}):''):
    u.available?'':t('modsUpdateUnknownDetail');
  $('#mods-update-list').replaceChildren(...outdated.map(i=>el('li',{text:`${i.title||t('untitled',{id:i.id})} · ${t('updatePublished',{date:PZ.dayLabel(i.latest)})}`})));
  $('#mods-update-box').hidden=checkingMods||!outdated.length;
  $('#mods-update-refresh').disabled=checkingMods||running;
}

async function loadBackups(){
  const data=await api('backups');
  PZ.state.backups=data.backups;PZ.state.disk=data.disk;
  renderBackups();
}
function renderBackups(){
  const all=PZ.state.backups||[];
  const counts={all:all.length,automatic:0,manual:0,rescue:0};
  for(const b of all)if(counts[b.kind]!==undefined)counts[b.kind]++;
  $('#backup-filter').replaceChildren(...[['all','filterAllBackups'],['automatic','filterAutomatic'],['manual','filterManual'],['rescue','filterRescue']].map(([id,key])=>
    el('button',{type:'button','aria-pressed':String(filter===id),text:t(key),title:String(counts[id]),onclick:()=>{filter=id;expanded=false;renderBackups();}})));
  const list=all.filter(b=>filter==='all'||b.kind===filter);
  const shown=expanded?list:list.slice(0,PAGE);
  const running=PZ.state.job?.state==='running';
  $('#backup-rows').replaceChildren(...(shown.length?shown.map(b=>{
    const [color,key]=KIND[b.kind]||KIND.other;
    return el('tr',{},el('td',{text:PZ.dayLabel(b.mtime)}),el('td',{},el('span',{class:'pill '+color,text:t(key)})),el('td',{text:bytes(b.size).join(' ')}),
      el('td',{class:'ok-text',text:t('verified')}),el('td',{class:'file',text:b.name}),
      el('td',{class:'right'},el('button',{class:'btn small',type:'button',text:t('restoreEllipsis'),disabled:running,'aria-label':t('restoreName',{name:b.name}),onclick:run(()=>PZ.operation('restore',b.name))})));
  }):[el('tr',{},el('td',{colspan:6,class:'muted',text:t('noBackups')}))]));
  const disk=PZ.state.disk||{};
  const foot=[t('spaceUsed',{used:bytes(disk.used||0).join(' ')})];
  if(disk.free!=null)foot.push(t('spaceFree',{free:bytes(disk.free).join(' ')}));
  const node=$('#backup-foot');node.replaceChildren(foot.join(' · '));
  if(list.length>PAGE)node.append(' · ',el('button',{class:'link',type:'button',text:expanded?t('showLess'):t('showMore',{n:list.length-PAGE}),onclick:()=>{expanded=!expanded;renderBackups();}}));
}

$('#update-refresh').addEventListener('click',()=>loadUpdate(true));
$('#mods-update-refresh').addEventListener('click',()=>loadModsUpdate(true));

PZ.views.maintenance={title:'maintenance',renderJob,loadBackups,loadUpdate,loadModsUpdate,
  status:renderService,
  enter(){renderJob();run(loadBackups)();if(!checking)loadUpdate();if(!checkingMods)loadModsUpdate();}};
})();

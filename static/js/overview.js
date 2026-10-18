'use strict';
(()=>{
const {t,$,el,icon,api,run,bytes,duration,ago,clock}=window.PZ;
const PZ=window.PZ;
let logCursor=null,logTimer=null,tick=null,activityTimer=null,logRun=0;
const logLines=[];

function status(){
  const s=PZ.state.status;if(!s)return;
  const p=PZ.state.players;
  const updated=PZ.state.statusTime?Math.round((Date.now()-PZ.state.statusTime)/1000):0;
  $('#overview-updated').textContent=t('updatedAgo',{time:duration(updated)});
  const look=PZ.stateTone(s.state);
  $('#tile-state').replaceChildren(el('span',{class:'dot '+(look==='unknown'?'':look)}),el('span',{text:s.state==='active'?t('online'):PZ.stateLabel(s.state)}));
  $('#tile-uptime').textContent=s.uptime!=null?t('startedAgo',{time:duration(s.uptime)}):s.state==='active'?'':t('notRunning');
  const max=Number(s.max_players);
  if(p&&p.count!==null){
    $('#tile-players').replaceChildren(String(p.count),Number.isFinite(max)&&max>0?el('span',{class:'unit',text:'/ '+max}):'');
    $('#tile-players-bar').style.width=(Number.isFinite(max)&&max>0?Math.min(100,p.count/max*100):0)+'%';
  }else $('#tile-players').textContent='—';
  const [value,unit]=bytes(Number(s.memory));
  $('#tile-memory').replaceChildren(value,el('span',{class:'unit',text:unit}));
  const b=s.last_backup;
  $('#tile-backup').textContent=b?ago(b.mtime):t('none');
  $('#tile-backup-foot').replaceChildren(...(b?[icon('check'),t('verifiedSize',{size:bytes(b.size).join(' ')})]:[t('noBackupsYet')]));
  const power=$('#overview-power');
  const running=s.state==='active'||s.state==='activating';
  power.dataset.op=running?'restart':'start';
  power.replaceChildren(icon(running?'restart':'play'),el('span',{text:running?t('restartEllipsis'):t('start')}));
  renderPending();renderPlayers();
}

function renderPending(){
  const s=PZ.state.status;const banner=$('#pending-banner');
  const files=s.pending||[];
  banner.hidden=!(s.applied_known&&files.length&&s.state==='active');
  if(banner.hidden)return;
  const names=files.map(f=>f==='server'?`${PZ.name}.ini`:t('file_'+f));
  const count=Object.values(s.pending_changes||{}).reduce((a,n)=>a+(n||0),0);
  $('#pending-text').textContent=t('pendingText',{files:names.join(t('and')),count});
}

function renderPlayers(){
  const p=PZ.state.players;const list=$('#player-list');const s=PZ.state.status;
  $('#message-all').disabled=!(p&&p.count>0);
  if(!p){list.replaceChildren(el('p',{class:'empty',text:t('checkingPlayers')}));return;}
  if(p.count===null){list.replaceChildren(el('p',{class:'empty',text:p.error||t('playersUnknown')}));return;}
  if(!p.names.length){list.replaceChildren(el('p',{class:'empty',text:s?.state==='active'?t('nobodyOnline'):t('serverNotRunning')}));return;}
  list.replaceChildren(...p.names.map(name=>el('div',{class:'item'},
    el('span',{class:'avatar '+PZ.tone(name),text:PZ.initial(name),'aria-hidden':'true'}),
    el('div',{class:'item-main'},el('div',{class:'name',text:name}),el('div',{class:'meta',text:p.since?.[name]?t('connectedAgo',{time:duration(Date.now()/1000-p.since[name])}):''})),
    el('button',{class:'btn small danger-text',text:t('kick'),'aria-label':t('kickName',{name}),onclick:run(()=>kick(name))}))));
}

async function sendMessage(){
  const values=await PZ.dialog({icon:'info',title:t('messageTitleAll'),
    body:[el('label',{class:'stack'},el('span',{text:t('messageLabel')}),el('textarea',{name:'text',maxlength:200,required:true})),
      el('p',{class:'hint',text:t('messageHintAll')})],
    confirm:t('send'),validate:f=>f.elements.text.value.trim().length>0});
  if(!values)return;
  const r=await api('players/message',{text:values.text.trim()});
  PZ.toast(r.message,true);loadActivity();
}
async function kick(name){
  const values=await PZ.dialog({icon:'warning',tone:'danger',title:t('kickTitle',{name}),
    body:[el('p',{class:'dlg-text',text:t('kickText')}),el('label',{class:'stack'},el('span',{text:t('kickReason')}),el('input',{name:'reason',maxlength:120,autocomplete:'off'}))],
    confirm:t('kick'),confirmClass:'danger'});
  if(!values)return;
  const r=await api('players/kick',{name,reason:values.reason.trim()});
  PZ.toast(r.message,true);
  await Promise.allSettled([PZ.refreshPlayers(),loadActivity()]);
}

const ACTIVITY_TONE={ok:'ok',warn:'warn',error:'error',info:'info'};
function activityText(e){
  const p=e.params||{};
  switch(e.kind){
    case 'backup':return [e.state==='ok'?t(p.automatic?'actAutoBackup':'actBackup'):t('actBackupFailed'),p.message||''];
    case 'operation':return [t((e.state==='ok'?'actOk_':'actFail_')+p.action),e.state==='ok'?'':p.message||''];
    case 'config':return [t('actConfig',{count:p.count}),t('actPendingRestart')];
    case 'started':return [t('actStarted'),p.seconds!=null?t('actLoadedIn',{time:duration(p.seconds)}):''];
    case 'crashed':return [t('actCrashed'),t('actExitCode',{code:p.code})];
    case 'message':return [t('actMessage'),p.text||''];
    case 'kick':return [t('actKick',{name:p.name}),p.reason||''];
    default:return [e.kind,''];
  }
}
async function loadActivity(){
  const events=await api('activity?limit=8');
  const list=$('#activity-list');
  if(!events.length){list.replaceChildren(el('li',{class:'empty',text:t('noActivity')}));return;}
  list.replaceChildren(...events.map(e=>{
    const [title,detail]=activityText(e);
    const day=new Date(e.time*1000).toDateString()===new Date().toDateString();
    return el('li',{},el('span',{class:'dot '+(ACTIVITY_TONE[e.state]||'info')}),
      el('div',{class:'grow'},el('div',{text:title}),detail?el('div',{class:'detail',text:detail}):null),
      el('time',{datetime:new Date(e.time*1000).toISOString(),text:day?clock(e.time):PZ.dayLabel(e.time)}));
  }));
}

async function loadHistory(){
  const points=(await api('history')).filter(p=>p.memory!=null);
  const chart=$('#memory-chart');
  if(points.length<2){chart.querySelector('polyline').setAttribute('points','');chart.setAttribute('aria-label',t('memoryNoHistory'));return;}
  const values=points.map(p=>p.memory);
  const min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,max*0.05,1);
  const t0=points[0].time,t1=points.at(-1).time;
  chart.querySelector('polyline').setAttribute('points',points.map(p=>`${((p.time-t0)/Math.max(t1-t0,1)*200).toFixed(1)},${(25-(p.memory-min)/span*22).toFixed(1)}`).join(' '));
  chart.setAttribute('aria-label',t('memoryHistory',{min:bytes(min).join(' '),max:bytes(max).join(' '),time:duration(t1-t0)}));
}

function logRow(e){
  return el('div',{},el('span',{class:'ts',text:clock(e.time)}),'  ',el('span',{class:'lv-'+e.level,text:e.level.toUpperCase().padEnd(5)}),' ',e.category?el('span',{class:'cat',text:e.category.padEnd(8)}):'',' ',e.text);
}
async function pollLog(){
  clearTimeout(logTimer);
  const mine=++logRun;
  try{
    const data=await api('logs?source=game&lines=6'+(logCursor?'&cursor='+encodeURIComponent(logCursor):''));
    logCursor=data.cursor||logCursor;
    logLines.push(...data.entries);logLines.splice(0,Math.max(0,logLines.length-6));
    const box=$('#overview-log');
    box.replaceChildren(...(logLines.length?logLines.map(logRow):[el('div',{class:'muted',text:t('noLogLines')})]));
  }catch{}
  if(mine===logRun&&PZ.current()==='overview')logTimer=setTimeout(pollLog,4000);
}

function renderJob(){
  const job=PZ.state.job;const banner=$('#job-banner');
  banner.hidden=!(job&&job.state==='running');
  if(banner.hidden)return;
  banner.replaceChildren(el('span',{class:'dot warn live'}),el('div',{class:'grow'},el('div',{class:'banner-title',text:t('jobRunning_'+job.action)}),el('div',{class:'banner-text muted',text:job.message})),el('a',{href:'#maintenance',text:t('viewProgress')}));
}

PZ.views.overview={title:'overview',status,renderJob,loadActivity,
  enter(){
    status();renderJob();
    run(loadActivity)();run(loadHistory)();pollLog();
    run(PZ.refreshPlayers)();
    clearInterval(tick);tick=setInterval(()=>{if(PZ.state.status)$('#overview-updated').textContent=t('updatedAgo',{time:duration((Date.now()-PZ.state.statusTime)/1000)});},1000);
    clearInterval(activityTimer);activityTimer=setInterval(()=>{if(!document.hidden){run(loadActivity)();run(loadHistory)();}},60000);
  },
  leave(){logRun++;clearTimeout(logTimer);clearInterval(tick);clearInterval(activityTimer);}};
$('#message-all').addEventListener('click',run(sendMessage));
})();

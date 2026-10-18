'use strict';
(()=>{
const {t,$,el,api,run,clockPrecise}=window.PZ;
const PZ=window.PZ;
const MAX=5000;
const UNITS={game:'zomboid.service',panel:'adminoid.service',backup:'zomboid-backup.service'};
let entries=[],cursor=null,paused=false,level='all',timer=null,loading=false,generation=0;

const box=()=>$('#log-lines');
const query=()=>$('#log-search').value.trim().toLocaleLowerCase();
function visible(e){
  if(level==='error'&&e.level!=='error')return false;
  if(level==='warn'&&e.level!=='warn')return false;
  if(level==='connection'&&!e.connection)return false;
  return true;
}
function highlight(text,q){
  if(!q)return [text];
  const parts=[];const lower=text.toLocaleLowerCase();let at=0,i;
  while((i=lower.indexOf(q,at))!==-1){parts.push(text.slice(at,i),el('mark',{text:text.slice(i,i+q.length)}));at=i+q.length;}
  parts.push(text.slice(at));return parts;
}
function line(e,index,q){
  const hit=q&&e.text.toLocaleLowerCase().includes(q);
  return el('div',{class:'ln'+(e.level==='error'?' lv-row-error':'')+(hit?' hit':'')},
    el('span',{class:'n',text:index+1}),el('span',{class:'t',text:clockPrecise(e.time),title:new Date(e.time*1000).toLocaleString(PZ.lang)}),
    el('span',{class:'lv-'+e.level,text:e.level==='info'?'INFO':e.level.toUpperCase()}),el('span',{class:'cat',text:e.category||'—'}),
    el('span',{class:'msg'},highlight(e.text,q)));
}
function renderChips(){
  const errors=entries.filter(e=>e.level==='error').length,warnings=entries.filter(e=>e.level==='warn').length;
  const chips=[['all',t('levelAll'),null],['error',t('levelErrors',{n:errors}),'error'],['warn',t('levelWarnings',{n:warnings}),'warn'],['connection',t('levelConnections'),'info']];
  $('#log-levels').replaceChildren(...chips.map(([id,label,dot])=>el('button',{class:'chip',type:'button','aria-pressed':String(level===id),onclick:()=>{level=id;render(true);}},dot?el('span',{class:'dot '+dot}):null,label)));
}
function renderMeta(){
  const q=query();
  $('#log-matches').textContent=q?t('matches',{n:entries.filter(e=>visible(e)&&e.text.toLocaleLowerCase().includes(q)).length}):'';
  $('#log-count').textContent=t('showingLines',{n:entries.length});
}
function nearBottom(){const b=box();return b.scrollHeight-b.scrollTop-b.clientHeight<60;}
function render(scroll){
  const q=query();
  box().replaceChildren(...entries.map((e,i)=>visible(e)?line(e,i,q):null).filter(Boolean));
  if(!entries.length)box().append(el('div',{class:'ln'},el('span'),el('span'),el('span'),el('span'),el('span',{class:'muted',text:t('noLogLines')})));
  renderChips();renderMeta();
  if(scroll)box().scrollTop=box().scrollHeight;
}
function append(items){
  const stick=nearBottom();const q=query();
  const start=entries.length;entries.push(...items);
  if(entries.length>MAX){entries.splice(0,entries.length-MAX);render(stick);return;}
  if(start===0){render(true);return;}
  box().append(...items.map((e,i)=>visible(e)?line(e,start+i,q):null).filter(Boolean));
  renderChips();renderMeta();
  if(stick)box().scrollTop=box().scrollHeight;
}

async function load(){
  clearTimeout(timer);
  const mine=++generation;
  const source=$('#log-source').value;
  $('#log-unit').textContent='journalctl · '+UNITS[source];
  $('#log-download').href='/api/logs/download?source='+source;
  const data=await api(`logs?source=${source}&lines=1000`);
  if(mine!==generation)return;
  entries=data.entries;cursor=data.cursor;
  render(true);
  schedule();
}
async function poll(){
  if(loading)return;
  loading=true;
  try{
    const mine=generation;
    const source=$('#log-source').value;
    const data=await api(`logs?source=${source}&lines=1000`+(cursor?'&cursor='+encodeURIComponent(cursor):''));
    if(mine!==generation)return;
    cursor=data.cursor||cursor;
    if(data.entries.length)append(data.entries);
  }catch(e){if(e.status===401)return;}
  finally{loading=false;}
  schedule();
}
function schedule(){clearTimeout(timer);if(!paused&&PZ.current()==='logs')timer=setTimeout(poll,2000);}
async function older(){
  if(!entries.length)return;
  const source=$('#log-source').value;
  const data=await api(`logs?source=${source}&lines=1000&until=${entries[0].time-0.000001}`);
  const fresh=data.entries.filter(e=>e.time<entries[0].time);
  if(!fresh.length){PZ.toast(t('noOlderLines'),true);return;}
  const b=box();const height=b.scrollHeight;
  entries=[...fresh,...entries].slice(0,MAX);
  render(false);b.scrollTop=b.scrollHeight-height;
}
function setPaused(value){
  paused=value;
  const b=$('#log-pause');b.setAttribute('aria-pressed',String(paused));
  b.replaceChildren(PZ.icon(paused?'play':'pause'),el('span',{text:paused?t('resume'):t('pause')}));
  const live=$('#log-live');live.classList.toggle('paused',paused);
  live.lastElementChild.textContent=paused?t('paused'):t('following');
  if(!paused)poll();else clearTimeout(timer);
}

PZ.views.logs={title:'logs',
  enter(arg,entering){if(entering||!entries.length)run(load)();else schedule();},
  leave(){clearTimeout(timer);}};
$('#log-source').addEventListener('change',run(load));
$('#log-search').addEventListener('input',()=>render(false));
$('#log-pause').addEventListener('click',()=>setPaused(!paused));
$('#log-wrap').addEventListener('click',()=>{const on=box().classList.toggle('wrap');$('#log-wrap').setAttribute('aria-pressed',String(on));});
$('#log-older').addEventListener('click',run(older));
})();

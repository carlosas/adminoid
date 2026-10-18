'use strict';
(()=>{
const PZ=window.PZ;
const {t,$,$$,api,run}=PZ;
let statusTimer=null,playersTimer=null;

PZ.showLogin=()=>{
  clearInterval(statusTimer);clearInterval(playersTimer);
  $('#application').hidden=true;$('#login-panel').hidden=false;
};

async function enter(){
  await PZ.refreshStatus();
  $('#application').hidden=false;$('#login-panel').hidden=true;
  PZ.route();
  const last=await api('last-operation').catch(()=>({}));
  let id=null;try{id=localStorage.getItem('pz-job');}catch{}
  if(id||last.state==='running')PZ.watchJob(id||last.id);
  else if(last.id)PZ.setJob(last);
  run(PZ.views.settings.load)();
  run(PZ.views.mods.load)();
  run(PZ.views.maintenance.loadBackups)();
  run(PZ.refreshPlayers)();
  clearInterval(statusTimer);clearInterval(playersTimer);
  // Status is cheap; the player list asks the game console, so it refreshes less often.
  statusTimer=setInterval(()=>{if(!document.hidden)PZ.refreshStatus().catch(()=>{});},10000);
  playersTimer=setInterval(()=>{if(!document.hidden&&PZ.state.status?.state==='active')PZ.refreshPlayers().catch(()=>{});},30000);
}

$('#login').addEventListener('submit',async e=>{
  e.preventDefault();
  try{
    const form=new FormData(e.target);
    const r=await api('login',{username:form.get('username'),password:form.get('password')});
    PZ.setCsrf(r.csrf);e.target.reset();await enter();
  }catch(error){PZ.toast(error.message);}
});

for(const b of $$('[data-action=logout]'))b.addEventListener('click',run(async()=>{
  if(PZ.editCount()&&!await PZ.confirmDialog(t('logoutTitle'),t('discardLogout'),t('logout'),true))return;
  $('#menu-sheet').close();
  await api('logout',{});
  PZ.state.edits={};PZ.state.structures={};
  location.reload();
}));

// Switching language reloads the page; keep the current section.
for(const a of $$('.lang-switch a'))a.addEventListener('click',async e=>{
  e.preventDefault();
  if($('#application').hidden===false&&PZ.editCount()&&!await PZ.confirmDialog(t('languageTitle'),t('discardLanguage'),t('discard'),true))return;
  PZ.state.edits={};PZ.state.structures={};
  location.href=`?lang=${a.dataset.lang}${location.hash}`;
});

$('#menu-button').addEventListener('click',()=>$('#menu-sheet').showModal());
for(const b of $$('#menu-sheet [data-close]'))b.addEventListener('click',()=>$('#menu-sheet').close());

document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!$('#application').hidden)PZ.refreshStatus().catch(()=>{});});
window.addEventListener('beforeunload',e=>{if(PZ.editCount()){e.preventDefault();e.returnValue='';}});

enter().catch(()=>PZ.showLogin());
})();

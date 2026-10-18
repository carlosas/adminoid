import copy
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings as S
import configuration as C
import operations as O
from common import atomic, write_json

INI = '# Players\nPVP=false\n# Min: 1 Max: 32 Default: 8\nMaxPlayers=8\nPassword=old-secret\nResetID=99\nMods=\nWorkshopItems=\n'
LUA = '''SandboxVars = {
    VERSION = 6,
    -- 1 = High
    -- 2 = Low
    -- Min: 1 Max: 2 Default: 1
    Zombies = 1,
    ZombieConfig = {
        -- Min: 0.00 Max: 4.00 Default: 0.65
        PopulationMultiplier = 0.65,
    },
    Custom = { Enabled = true, Label = "unchanged" },
}
'''

class TemporaryServer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.saved = {k:getattr(S,k) for k in ('STATE','DATA','BACKUPS','GAME','SOCKET')}
        S.STATE=self.root/'state'; S.DATA=self.root/'Zomboid'; S.BACKUPS=self.root/'backups'; S.GAME=self.root/'game'; S.SOCKET=S.STATE/'console.sock'
        (S.DATA/'Server').mkdir(parents=True); S.STATE.mkdir(); S.GAME.mkdir()
        atomic(S.DATA/'Server'/S.FILES['server'],INI); atomic(S.DATA/'Server'/S.FILES['sandbox'],LUA)
    def tearDown(self):
        for k,v in self.saved.items(): setattr(S,k,v)
        self.temp.cleanup()

class ParserTests(TemporaryServer):
    def test_semantic_configuration_comparison_ignores_comments(self):
        before=INI.replace('# Players','# Default: 123\n# Players')
        after=INI.replace('# Players','# Default: 456\n# Players')
        self.assertTrue(O.same_configuration('server',before,after))
        self.assertFalse(O.same_configuration('server',before,after.replace('PVP=false','PVP=true')))
        # Keys the game adds after starting (new options, mod settings) are not pending changes
        self.assertTrue(O.same_configuration('server',before,after+'\nModOption=false\n'))
        self.assertTrue(O.same_configuration('sandbox',LUA,LUA.replace('SandboxVars = {','SandboxVars = {\n    Extra = 1,')))

    def test_english_catalog_fallback_categories(self):
        catalog=C.catalog('en')
        categories={f['category'] for doc in catalog.values() for f in doc['fields']}
        self.assertIn('Advanced',categories)
        self.assertNotIn('Avanzado',categories)

    def test_start_year_next_to_start_month(self):
        from types import SimpleNamespace as N
        cats={('sandbox','StartMonth'):'Time'}
        for key in ('StartMonth','StartYear'):
            self.assertEqual(C.metadata('sandbox',N(key=key,help='',value=1,kind='integer'),{},cats)['category'],'Time')
        self.assertEqual(C.metadata('sandbox',N(key='Other',help='',value=1,kind='integer'),{},cats)['category'],'Advanced')
    def test_ini_lossless_and_secret(self):
        text=C.Document('server',INI).edit({'Password':'a&b|$x"','PVP':True})
        self.assertEqual(text,INI.replace('old-secret','a&b|$x"').replace('PVP=false','PVP=true'))
        secret=next(f for f in C.catalog()['server']['fields'] if f['key']=='Password')
        self.assertIsNone(secret['value']); self.assertTrue(secret['configured'])
    def test_lua_roundtrip_comments_unknown(self):
        doc=C.Document('sandbox',LUA)
        self.assertEqual(doc.edit({}),LUA)
        edited=doc.edit({'ZombieConfig.PopulationMultiplier':2.5})
        self.assertEqual(edited,LUA.replace('PopulationMultiplier = 0.65','PopulationMultiplier = 2.5'))
        self.assertTrue(C.Document('sandbox',edited).fields['Custom.Enabled'].value)
    def test_bounds_types_and_ids(self):
        for changes in ({'MaxPlayers':0},{'MaxPlayers':9.2},{'PVP':'true'},{'ResetID':3},{'WorkshopItems':'1;bad'},{'Password':'a\nb'}):
            with self.assertRaises(ValueError): C.Document('server',INI).edit(changes)
    def test_lua_code_not_executed(self):
        with self.assertRaises(ValueError): C.Document('sandbox','SandboxVars = { evil = os.execute("touch hacked") }')
        with self.assertRaises(ValueError): C.Document('sandbox',LUA+' os.execute("touch hacked")')
    def test_spawn_roundtrip(self):
        regions=[{'name':'Muldraugh','file':'media/maps/Muldraugh, KY/spawnpoints.lua'}]
        points={'unemployed':[{'worldX':40,'worldY':22,'posX':67,'posY':201}]}
        for scope,obj in [('regions',regions),('points',points)]:
            self.assertEqual(C.spawn_structure(scope,C.spawn_lua(scope,obj)),obj)
        with self.assertRaises(ValueError): C.spawn_lua('regions',[{'name':'x','file':'../bad'}])

class BackupTests(TemporaryServer):
    def test_backup_restore(self):
        (S.DATA/'Saves').mkdir(); (S.DATA/'Saves/world.bin').write_bytes(b'world-data')
        name=O.backup('manual'); path=O.resolve_backup(name)
        self.assertGreater(O.validate_archive(path),0)
        (S.DATA/'Saves/world.bin').write_bytes(b'changed')
        O.restore(path)
        self.assertEqual((S.DATA/'Saves/world.bin').read_bytes(),b'world-data')
    def test_bad_archives(self):
        S.BACKUPS.mkdir()
        for name,type_ in [('../outside',tarfile.REGTYPE),('Zomboid/link',tarfile.SYMTYPE)]:
            p=S.BACKUPS/'zomboid-bad.tar.gz'
            with tarfile.open(p,'w:gz') as tar:
                item=tarfile.TarInfo(name);item.type=type_;item.linkname='/etc/passwd';tar.addfile(item,io.BytesIO(b''))
            with self.assertRaises(ValueError): O.validate_archive(p)
        p.write_bytes(b'broken')
        with self.assertRaises(tarfile.TarError): O.validate_archive(p)
    def test_retention_and_path(self):
        manual=O.backup('manual')
        for _ in range(9): O.backup('daily')
        self.assertEqual(len(list(S.BACKUPS.glob('zomboid-daily-*'))),7)
        self.assertTrue((S.BACKUPS/manual).exists())
        with self.assertRaises(ValueError): O.resolve_backup('../../etc/passwd')
    def test_automatic_defers(self):
        with patch.object(O,'players',return_value={'count':None}),patch.object(O,'preserve_stop') as stop:
            result=O.execute('backup',{'automatic':True,'confirmed':True},lambda x:None)
            self.assertTrue(result.startswith('Deferred'));stop.assert_not_called()
    def test_pending_survives_stop(self):
        S.SOCKET.touch()
        def fake_systemctl(action):
            if action=='stop': atomic(S.DATA/'Server'/S.FILES['server'],INI.replace('PVP=false','PVP=true'))
            return 'active' if action=='is-active' and not getattr(fake_systemctl,'stopped',False) else 'inactive'
        calls=[0]
        def ctl(action):
            if action=='is-active': calls[0]+=1;return 'active' if calls[0]==1 else 'inactive'
            import time
            atomic(S.DATA/'Server'/S.FILES['server'],'overwritten')
            write_json(S.STATE/'game-exit.json',{'graceful':True,'time':time.time()})
        with patch.object(O,'systemctl',side_effect=ctl): self.assertTrue(O.preserve_stop())
        self.assertEqual((S.DATA/'Server'/S.FILES['server']).read_text(),INI)
    def test_failed_update_stays_stopped(self):
        from subprocess import CompletedProcess
        with patch.object(O,'players',return_value={'count':0}),patch.object(O,'preserve_stop',return_value=True),patch.object(O,'command',return_value=CompletedProcess([],0,'ERROR missing configuration','')),patch.object(O,'start') as start:
            with self.assertRaises(ValueError): O.execute('update',{'confirmed':True},lambda x:None)
            start.assert_not_called()
            self.assertEqual(len(list(S.BACKUPS.glob('zomboid-pre-update-*'))),1)

class WebBase(TemporaryServer):
    def setUp(self):
        super().setUp()
        from app import create_app
        from werkzeug.security import generate_password_hash
        write_json(S.STATE/'account.json',{'username':'admin','hash':generate_password_hash('test-password-long'),'generation':'gen'})
        self.app=create_app();self.app.config['TESTING']=True;self.client=self.app.test_client()
        self.client.get('/')
        with self.client.session_transaction() as s: self.csrf=s['csrf']
    def post(self,path,body): return self.client.post('/api/'+path,json=body,headers={'X-CSRF-Token':self.csrf})
    def login(self):
        result=self.post('login',{'username':'admin','password':'test-password-long'})
        self.assertEqual(result.status_code,200);self.csrf=result.json['csrf']

class WebTests(WebBase):
    def test_auth_csrf_network(self):
        self.assertEqual(self.client.get('/api/config').status_code,401)
        self.login()
        self.assertEqual(self.client.post('/api/logout',json={}).status_code,403)
        self.assertEqual(self.client.get('/api/config',environ_overrides={'REMOTE_ADDR':'8.8.8.8'}).status_code,403)
        self.assertEqual(self.post('logout',{}).status_code,200)
        self.assertEqual(self.client.get('/api/config').status_code,401)
    def test_preview_commit_no_restart_and_conflict(self):
        self.login(); cfg=self.client.get('/api/config').json
        self.assertNotIn('old-secret',json.dumps(cfg))
        payload={'changes':{'server':{'PVP':True}},'revisions':{k:v['revision'] for k,v in cfg.items()}}
        preview=self.post('preview',payload);self.assertEqual(preview.status_code,200)
        with patch.object(O,'systemctl') as ctl:
            self.assertEqual(self.post('commit',{'id':preview.json['id']}).status_code,200);ctl.assert_not_called()
        self.assertIn('PVP=true',(S.DATA/'Server'/S.FILES['server']).read_text())
        self.assertEqual(self.post('preview',payload).status_code,400)
    def test_preview_preserves_secret_and_protected(self):
        self.login();cfg=self.client.get('/api/config').json;revs={k:v['revision'] for k,v in cfg.items()}
        p=self.post('preview',{'changes':{'server':{'Password':'new-secret'}},'revisions':revs})
        self.assertEqual(p.status_code,200);self.assertNotIn('new-secret',p.text);self.assertNotIn('old-secret',p.text)
        p=self.post('preview',{'changes':{'server':{'ResetID':1}},'revisions':revs});self.assertEqual(p.status_code,400)
    def test_limiter(self):
        for _ in range(5): self.assertEqual(self.post('login',{'username':'admin','password':'wrong'}).status_code,401)
        self.assertEqual(self.post('login',{'username':'admin','password':'wrong'}).status_code,429)
    def test_english_session_catalog_and_errors(self):
        response=self.client.get('/?lang=en')
        self.assertIn(b'<html lang="en">',response.data)
        self.login()
        cfg=self.client.get('/api/config').json
        categories={f['category'] for doc in cfg.values() for f in doc['fields']}
        self.assertIn('Advanced',categories)
        error=self.post('operations',{'action':'invalid'})
        self.assertEqual(error.status_code,400)
        self.assertEqual(error.json['error'],'Action not allowed.')
    def test_default_english_and_spanish_session(self):
        self.assertIn(b'<html lang="en">',self.client.get('/').data)
        response=self.client.get('/?lang=es')
        self.assertIn(b'<html lang="es">',response.data)
        self.login()
        cfg=self.client.get('/api/config').json
        categories={f['category'] for doc in cfg.values() for f in doc['fields']}
        self.assertIn('Avanzado',categories)
        self.assertNotIn('Advanced',categories)
        error=self.post('operations',{'action':'invalid'})
        self.assertEqual(error.json['error'],'Acción no permitida.')

class ConsoleTests(TemporaryServer):
    def test_restricted_console_and_clean_shutdown(self):
        import shutil,subprocess,sys,time
        fake=Path(__file__).parent/'fake_game_test.py'
        shutil.copyfile(fake,S.GAME/'start-server.sh');(S.GAME/'start-server.sh').chmod(0o700)
        code='import sys;from pathlib import Path;import settings as s;import classinfo;classinfo.packaged_version=lambda p:"42.20";s.STATE=Path(sys.argv[1]);s.DATA=Path(sys.argv[2]);s.GAME=Path(sys.argv[3]);s.SOCKET=s.STATE/"console.sock";import runner;runner.main()'
        proc=subprocess.Popen([sys.executable,'-c',code,str(S.STATE),str(S.DATA),str(S.GAME)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10
            while not (S.STATE/'applied.json').exists() and time.monotonic()<deadline:time.sleep(.1)
            self.assertTrue((S.STATE/'applied.json').exists(),proc.stderr.read() if proc.poll() is not None else 'runner still active after 10 seconds')
            self.assertEqual(O.console('players'),{'count':2,'names':['Alice','Bob']})
            with self.assertRaises(ValueError):O.console('rm -rf /')
            self.assertEqual(O.console('save'),{'saved':True})
            self.assertEqual(O.console('message',text='Restart in "5" minutes'),{'sent':True})
            with self.assertRaises(ValueError):O.console('kick',user='bad"name')
            self.assertTrue(O.console('quit')['sent'])
            self.assertEqual(proc.wait(timeout=10),0)
            self.assertTrue(json.loads((S.STATE/'game-exit.json').read_text())['graceful'])
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
            proc.stderr.close()

class RunnerTests(unittest.TestCase):
    def test_console_lines_are_quoted_and_limited(self):
        import runner
        self.assertEqual(runner.console_line('message',{'text':'Hi "all"\nnow'}),'servermsg "Hi \'all\' now"')
        self.assertEqual(runner.console_line('kick',{'user':'Kate','reason':'afk'}),'kickuser "Kate" -r "afk"')
        self.assertEqual(runner.console_line('kick',{'user':'Kate'}),'kickuser "Kate"')
        self.assertEqual(runner.console_line('players',{}),'players')
        for args in ({'user':'a"b'},{'user':''},{'user':'x'*65}):
            with self.assertRaises(ValueError): runner.console_line('kick',args)
        for text in ('','   ','x'*201,None):
            with self.assertRaises(ValueError): runner.console_line('message',{'text':text})

class ActivityTests(TemporaryServer):
    def test_feed_is_newest_first_and_capped(self):
        import common
        for i in range(common.ACTIVITY_LIMIT+5): common.record('config','warn',count=i)
        events=common.activity(3)
        self.assertEqual([e['params']['count'] for e in events],[104,103,102])
        self.assertEqual(len(json.loads((S.STATE/'activity.json').read_text())),common.ACTIVITY_LIMIT)

class LogTests(TemporaryServer):
    def test_parse_game_lines(self):
        e=O.parse_log_line('WARN : General      f:0, t:1729258258004> Slow chunk load at 34,21')
        self.assertEqual((e['level'],e['category'],e['text']),('warn','General','Slow chunk load at 34,21'))
        e=O.parse_log_line('LOG  : Network     , 1729258258004> 12,345> ConnectionManager: [fully-connected] username="Kate"')
        self.assertEqual((e['level'],e['category'],e['connection']),('info','Network',True))
        self.assertTrue(e['text'].startswith('ConnectionManager'))
        self.assertEqual(O.parse_log_line('ERROR: Lua f:0, t:1729258258004> boom')['level'],'error')
        self.assertEqual(O.parse_log_line('\tat zombie.Lua.call(Lua.java:12)')['level'],'error')
        self.assertEqual(O.parse_log_line('plain line')['level'],'info')
    def test_journal_entries_redacted_with_cursor(self):
        from subprocess import CompletedProcess
        lines=[json.dumps({'MESSAGE':'LOG  : General f:0, t:1729258258004> password is old-secret','__REALTIME_TIMESTAMP':'1729258258004000','__CURSOR':'s=1;i=1'}),
               json.dumps({'MESSAGE':[104,105],'__REALTIME_TIMESTAMP':'1729258259000000','__CURSOR':'s=1;i=2'}),'not json']
        with patch.object(O,'command',return_value=CompletedProcess([],0,'\n'.join(lines),'')) as cmd:
            result=O.logs('game',cursor='s=1;i=0')
        self.assertIn('--after-cursor',cmd.call_args[0][0])
        self.assertEqual(result['cursor'],'s=1;i=2')
        self.assertNotIn('old-secret',json.dumps(result))
        self.assertEqual(result['entries'][1]['text'],'hi')
        self.assertAlmostEqual(result['entries'][0]['time'],1729258258.004)
        for bad in ({'source':'other'},{'source':'game','cursor':'x --since'}):
            with self.assertRaises(ValueError): O.logs(**bad)

class ModsTests(TemporaryServer):
    def mod(self,root,folder,info):
        atomic(root/folder/'mod.info',info)
    def test_dependencies_order_and_missing(self):
        workshop=S.GAME/'steamapps/workshop/content/108600'
        self.mod(workshop/'111/mods/Lib','common','name=Lib\nid=Lib\n')
        self.mod(workshop/'111/mods/Lib','42','name=Lib B42\nid=Lib\nversionMin=42.0\n')
        self.mod(workshop/'222/mods/Cars','42','name=Cars\nid=Cars\nrequire=\\Lib,\\Extra\n')
        self.mod(S.DATA/'mods','Local','name=Local\nid=Local\nrequire=Cars\n')
        atomic(S.DATA/'Server'/S.FILES['server'],INI.replace('Mods=\n','Mods=\\Local;\\Cars;Ghost\n'))
        report=O.mods_report()
        mods={m['id']:m for m in report['installed']}
        self.assertEqual(mods['Lib']['name'],'Lib B42');self.assertEqual(mods['Lib']['workshop'],'111')
        self.assertIsNone(mods['Local']['workshop']);self.assertEqual(mods['Cars']['requires'],['Lib','Extra'])
        issues={(i['kind'],i['mod'],i.get('requires')) for i in report['issues']}
        self.assertEqual(issues,{('order','Local','Cars'),('missing','Cars','Lib'),('missing','Cars','Extra'),('not-installed','Ghost',None)})
        self.assertEqual(report['active'],['\\Local','\\Cars','Ghost'])
    def test_delete_unused_workshop_files(self):
        workshop=S.GAME/'steamapps/workshop/content/108600'
        self.mod(workshop/'111/mods/Lib','42','name=Lib\nid=Lib\n')
        self.mod(workshop/'222/mods/Cars','42','name=Cars\nid=Cars\n')
        self.mod(workshop/'333/mods/Old','42','name=Old\nid=Old\n')
        atomic(S.DATA/'Server'/S.FILES['server'],INI.replace('Mods=\n','Mods=\\Cars\n').replace('WorkshopItems=\n','WorkshopItems=111\n'))
        for ident,error in (('111','WorkshopItems'),('222','Mods line'),('444','No downloaded'),('../x','Invalid')):
            with self.assertRaisesRegex(ValueError,error): O.delete_workshop(ident)
        self.assertEqual(O.delete_workshop('333'),['Old'])
        self.assertFalse((workshop/'333').exists());self.assertTrue((workshop/'222').exists())
    def test_mod_sandbox_options(self):
        import test_fixture as F
        workshop=S.GAME/'steamapps/workshop/content/108600'
        for folder,mods in F.MODS.items():
            for name,info in mods.items():
                if folder!='local': self.mod(workshop/folder/'mods'/name,'42',info)
        for path,text in F.MOD_FILES.items(): atomic(workshop/path,text)
        atomic(S.DATA/'Server'/S.FILES['sandbox'],F.LUA)
        sandbox=C.catalog('es')['sandbox']
        fields={f['key']:f for f in sandbox['fields']}
        grip,mode,damage=fields['BetterCarPhysics.Grip'],fields['BetterCarPhysics.Mode'],fields['Brita.Damage']
        self.assertEqual((grip['category'],grip['mod'],grip['label'],grip['minimum'],grip['maximum']),('Better Car Physics','BetterCarPhysics','Agarre de los neumáticos',0.5,3.0))
        self.assertEqual(grip['help'],'Multiplier for how well cars hold the road.')
        self.assertEqual(mode['choices'],{1:'Arcade',2:'Realista',3:'Simulación'});self.assertFalse(mode['readonly'])
        # Options of an inactive mod are not in SandboxVars: read-only with their default
        self.assertEqual((damage['value'],damage['readonly'],damage['absent'],damage['category']),(5,True,True,"Brita's Weapon Pack"))
        self.assertEqual({m['id']:m['options'] for m in sandbox['mods']},{'CommonSense':0,'truemusic':0,'tsarslib':0,'BetterCarPhysics':3,'Brita':1})
        # Edits are checked against the bounds the mod declares
        doc=C.Document('sandbox',F.LUA)
        with self.assertRaisesRegex(ValueError,'outside'): doc.edit({'BetterCarPhysics.Grip':5.0})
        with self.assertRaisesRegex(ValueError,'invalid option'): doc.edit({'BetterCarPhysics.Mode':4})
        self.assertIn('Grip = 2.0',doc.edit({'BetterCarPhysics.Grip':2.0}))
        with self.assertRaisesRegex(ValueError,'Read-only'): doc.edit({'Brita.Damage':3})

    def test_workshop_lookup(self):
        from test_fixture import steam_fetch
        self.assertEqual(O.description_mod_ids('[h1]Info[/h1]\r\nWorkshop ID: 1\r\n[b]Mod ID:[/b] A; \\B\nMod IDs: C, A\nmod id = <b>D</b>\nUses the mod ID: no'),['A','B','C','D'])
        self.mod(S.GAME/'steamapps/workshop/content/108600'/'3619862853'/'mods'/'ZB','42','name=ZombieBuddy local\nid=ZombieBuddyLocal\n')
        O.workshop_cache.clear()
        with patch.object(O,'fetch',side_effect=steam_fetch) as fetch:
            items=O.workshop_info(['3815999052','x','3815999052'])['items']
            # Required items come first; a downloaded item reports the Mod IDs of its mod.info files.
            self.assertEqual([i['id'] for i in items],['3619862853','3809306528','3815999052'])
            self.assertEqual(items[0]['modIds'],['ZombieBuddyLocal']);self.assertTrue(items[0]['downloaded'])
            self.assertEqual(items[2],{'id':'3815999052','title':'OldPoint - Rotating Overhead Camera','modIds':['OldPoint'],'downloaded':False,'requires':['3809306528','3619862853'],'found':True})
            calls=fetch.call_count
            O.workshop_info(['3815999052'])
            self.assertEqual(fetch.call_count,calls)
        O.workshop_cache.clear()
        with patch.object(O,'fetch',side_effect=OSError('offline')):
            self.assertEqual(O.workshop_info(['5'])['items'],[{'id':'5','title':'','modIds':[],'downloaded':False,'requires':[],'found':False}])
        O.workshop_cache.clear()

class StatusTests(TemporaryServer):
    def test_status_details(self):
        import time as clock
        from subprocess import CompletedProcess
        write_json(S.STATE/'applied-values.json',{'server':INI.replace('MaxPlayers=8','MaxPlayers=4').replace('PVP=false','PVP=true'),'sandbox':LUA})
        write_json(S.STATE/'applied.json',{'server':'x','sandbox':'y'})
        O.backup('manual')
        show=f'ActiveState=active\nMemoryCurrent=1024\nMainPID=1\nActiveEnterTimestampMonotonic={int(max(1,clock.monotonic()-60)*1e6)}'
        with patch.object(O,'systemctl',return_value=show),patch.object(O,'command',return_value=CompletedProcess([],0,'version=42.12.3','')):
            s=O.status()
        self.assertEqual(s['pending'],['server']);self.assertEqual(s['pending_changes'],{'server':2})
        self.assertEqual(s['max_players'],8);self.assertEqual(s['version'],'42.12.3')
        self.assertGreater(s['uptime'],0);self.assertEqual(s['last_backup']['kind'],'manual')
    def test_game_update_check(self):
        from subprocess import CompletedProcess
        import test_fixture as F
        atomic(S.GAME/'steamapps'/'appmanifest_380870.acf','"AppState"\n{\n\t"buildid"\t\t"25485538"\n}\n')
        steam=lambda build:CompletedProcess([],0,F.STEAM_APP_INFO.replace('{build}',build),'')
        O.game_cache.clear()
        with patch.object(O,'command',return_value=steam('25485538')) as run:
            self.assertEqual(O.game_update(),{'installed':'25485538','latest':'25485538','published':1790178504,'checked':O.game_cache['latest']['checked'],'available':False})
            O.game_update()  # cached: Steam is not asked again within the hour
            self.assertEqual(run.call_count,1)
        with patch.object(O,'command',return_value=steam('25600000')):
            self.assertFalse(O.game_update()['available'])
            self.assertTrue(O.game_update(refresh=True)['available'])
        # Steam unreachable or an operation running: unknown, so the update button stays available
        with patch.object(O,'command',return_value=CompletedProcess([],1,'Connection failed','')):
            self.assertIsNone(O.game_update(refresh=True)['available'])
        from common import locked
        with patch.object(O,'command',return_value=steam('25485538')) as run, locked():
            self.assertIsNone(O.game_update(refresh=True)['available'])
            run.assert_not_called()
        O.game_cache.clear()
    def test_memory_history(self):
        with patch.object(O,'systemctl',return_value='ActiveState=active\nMemoryCurrent=2048'):
            O._samples.clear();O.sample()
        self.assertEqual(O.history()[-1]['memory'],2048)
        O._samples.clear()
        self.assertEqual(O.history()[-1]['memory'],2048)
        O._samples.clear()
        # A panel restart keeps the saved history when the sampler runs before any request
        with patch.object(O,'systemctl',return_value='ActiveState=active\nMemoryCurrent=4096'):
            O.sample()
        self.assertEqual([x['memory'] for x in O.history()[-2:]],[2048,4096])
        O._samples.clear()

class PanelApiTests(WebBase):
    def test_message_kick_and_activity(self):
        self.login()
        calls=[]
        def console(cmd,**args):
            calls.append((cmd,args))
            return {'count':1,'names':['Kate']} if cmd=='players' else {'sent':True}
        with patch.object(O,'console',side_effect=console),patch.object(O,'systemctl',return_value='active'):
            self.assertEqual(self.post('players/message',{'text':'hola'}).status_code,200)
            self.assertEqual(self.post('players/message',{'text':''}).status_code,400)
            self.assertEqual(self.post('players/kick',{'name':'Kate','reason':'afk'}).status_code,200)
            self.assertEqual(self.post('players/kick',{'name':'Nobody'}).json['error'],'That player is not connected.')
        self.assertIn(('message',{'text':'hola'}),calls)
        self.assertIn(('kick',{'user':'Kate','reason':'afk'}),calls)
        kinds=[e['kind'] for e in self.client.get('/api/activity').json]
        self.assertEqual(kinds,['kick','message'])
    def test_operation_steps_and_activity(self):
        import time
        self.login()
        def execute(action,payload,progress):
            progress('Warning connected players');progress('Saving and shutting down gracefully');progress('Starting and checking')
            return 'Server restarted.'
        with patch.object(O,'execute',side_effect=execute):
            ident=self.post('operations',{'action':'restart','confirmed':True}).json['id']
            for _ in range(50):
                job=self.client.get('/api/operations/'+ident).json
                if job['state']!='running':break
                time.sleep(.05)
        self.assertEqual(job['state'],'done');self.assertEqual(job['done'],['warn','stop','start'])
        self.assertEqual(job['steps'],O.STEPS['restart']);self.assertEqual(job['user'],'admin')
        event=self.client.get('/api/activity').json[0]
        self.assertEqual((event['kind'],event['state'],event['params']['action']),('operation','ok','restart'))
    def test_read_endpoints(self):
        self.login()
        O.backup('manual')
        data=self.client.get('/api/backups').json
        self.assertEqual(data['backups'][0]['kind'],'manual');self.assertIn('used',data['disk'])
        self.assertIn('issues',self.client.get('/api/mods').json)
        self.assertIsInstance(self.client.get('/api/history').json,list)
        with patch.object(O,'logs',return_value={'entries':[],'cursor':None}) as logs:
            self.assertEqual(self.client.get('/api/logs?source=panel&cursor=c&lines=10').status_code,200)
        logs.assert_called_with('panel','c',None,10)
        with patch.object(O,'log_text',return_value='line'):
            r=self.client.get('/api/logs/download?source=game')
        self.assertIn('attachment',r.headers['Content-Disposition']);self.assertEqual(r.data,b'line')
    def test_notice_in_session_language(self):
        self.client.get('/?lang=es');self.login()
        with patch.object(O,'execute',return_value='ok') as execute:
            self.post('operations',{'action':'stop','confirmed':True})
            import time;time.sleep(.2)
        self.assertIn('10 segundos',execute.call_args[0][1]['notice'])

if __name__=='__main__': unittest.main()

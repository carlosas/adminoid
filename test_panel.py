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

    def test_english_catalog_fallback_categories(self):
        catalog=C.catalog('en')
        categories={f['category'] for doc in catalog.values() for f in doc['fields']}
        self.assertIn('Advanced · server',categories)
        self.assertNotIn('Avanzado · server',categories)

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

class WebTests(TemporaryServer):
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
        self.assertIn('Advanced · server',categories)
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
        self.assertIn('Avanzado · server',categories)
        self.assertNotIn('Advanced · server',categories)
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
            self.assertTrue(O.console('quit')['sent'])
            self.assertEqual(proc.wait(timeout=10),0)
            self.assertTrue(json.loads((S.STATE/'game-exit.json').read_text())['graceful'])
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
            proc.stderr.close()

if __name__=='__main__': unittest.main()

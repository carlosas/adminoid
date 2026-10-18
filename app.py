import collections
import copy
import datetime as dt
import getpass
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, session
from werkzeug.security import generate_password_hash, check_password_hash
from waitress import serve

import settings as S
import configuration as C
import operations as O
from i18n import translate, notice
from common import atomic, initialize, write_json, locked, record, activity

def create_app():
    initialize()
    secret_file = S.STATE / 'session-key'
    if not secret_file.exists(): atomic(secret_file, secrets.token_hex(32))
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secret_file.read_text(), MAX_CONTENT_LENGTH=1024*1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=False, PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=8),
                      SESSION_REFRESH_EACH_REQUEST=False,
                      TRUSTED_HOSTS=[S.BIND, '127.0.0.1', 'localhost'])
    attempts = collections.defaultdict(list); auth_lock = threading.Lock()
    jobs, proposals = {}, {}
    jobs_lock = threading.Lock()
    dummy = generate_password_hash(secrets.token_hex(20))
    allowed = ipaddress.ip_network(S.ALLOWED_NETWORK) if S.ALLOWED_NETWORK else None

    def account():
        p = S.STATE / 'account.json'
        return json.loads(p.read_text()) if p.exists() else None

    def language(): return session.get('lang','en')

    @app.before_request
    def guard():
        addr = ipaddress.ip_address(request.remote_addr or '0.0.0.0')
        if not (addr.is_loopback or (allowed and addr in allowed)): abort(403)
        if request.path.startswith('/static/') or request.path == '/': return
        user = account()
        if not user: return jsonify(error='Create the panel account first: run .venv/bin/python app.py create-admin on the server.'), 503
        if request.method == 'POST':
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('csrf','!')): abort(403)
        if request.path == '/api/login': return
        if session.get('user') != user['username'] or session.get('generation') != user['generation']: abort(401)

    @app.after_request
    def headers(response):
        if response.is_json and language()=='es':
            data=response.get_json()
            def localize(obj):
                if isinstance(obj,dict):return {k:(translate(v,'es') if k in ('error','message') else localize(v)) for k,v in obj.items()}
                if isinstance(obj,list):return [localize(v) for v in obj]
                return obj
            response.set_data(app.json.dumps(localize(data)))
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(ValueError)
    def invalid(error): return jsonify(error=str(error)), 400

    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def http_error(error): return jsonify(error=error.description), error.code

    @app.route('/')
    def index():
        lang=request.args.get('lang',session.get('lang','en'))
        session['lang']=lang if lang in ('es','en') else 'en'
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return render_template('index.html', csrf=session['csrf'], setup=account() is None, lang=language(),
                               name=S.NAME, base=S.BASE)

    @app.post('/api/login')
    def login():
        data = request.get_json(); user = account(); now = time.monotonic(); ip = request.remote_addr
        with auth_lock:
            attempts[ip] = [t for t in attempts[ip] if now-t<900]
            if len(attempts[ip]) >= 5: return jsonify(error='Too many attempts. Wait 15 minutes.'),429
            attempts[ip].append(now)
        password = data.get('password', '')
        if not isinstance(password,str) or len(password)>1024: abort(400)
        valid = check_password_hash(user['hash'] if user else dummy, password)
        if not valid or data.get('username') != user['username']: abort(401)
        with auth_lock: attempts.pop(ip,None)
        lang=language();session.clear(); session.permanent = True
        session.update(user=user['username'],generation=user['generation'],csrf=secrets.token_urlsafe(32),lang=lang)
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    def logout(): session.clear(); return jsonify(ok=True)

    @app.get('/api/status')
    def status(): return jsonify(O.status())

    @app.get('/api/players')
    def players(): return jsonify(O.players())

    @app.post('/api/players/message')
    def player_message():
        text = request.get_json().get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 200: raise ValueError('Invalid text.')
        # The game console can only broadcast to everyone; there are no private messages.
        O.broadcast(text)
        record('message', 'info', text=text.strip()[:200])
        return jsonify(ok=True, message='Message sent.')

    @app.post('/api/players/kick')
    def player_kick():
        data = request.get_json(); name = data.get('name'); reason = data.get('reason') or ''
        if not isinstance(name, str) or not isinstance(reason, str) or len(reason) > 120: raise ValueError('Invalid player name.')
        O.kick(name, reason.strip())
        record('kick', 'warn', name=name, reason=reason.strip())
        return jsonify(ok=True, message='Player kicked.')

    @app.get('/api/activity')
    def recent_activity(): return jsonify(activity(max(1, min(int(request.args.get('limit', 20)), 100))))

    @app.get('/api/history')
    def history(): return jsonify(O.history())

    @app.get('/api/config')
    def config():
        catalog = C.catalog(language())
        applied_file = S.STATE / 'applied-values.json'
        applied = json.loads(applied_file.read_text()) if applied_file.exists() else {}
        for scope,doc in catalog.items():
            try: running = C.Document(scope,applied[scope]).fields if scope in applied else {}
            except ValueError: running = {}
            for f in doc['fields']:
                f['applied'] = running[f['key']].value if f['key'] in running and not f['secret'] else None
                f['applied_known'] = f['key'] in running and not f['secret']
            if scope in ('regions','points') and not doc['error']:
                try: doc['structure'] = C.spawn_structure(scope,C.read_all()[scope])
                except ValueError: doc['structure'] = None
        return jsonify(catalog)

    @app.post('/api/preview')
    def preview():
        data = request.get_json(); current = C.read_all(); changes = data.get('changes',{}); structural = data.get('structures',{})
        if not isinstance(changes,dict) or not isinstance(structural,dict): raise ValueError('Invalid changes.')
        result = dict(current); differences = []
        for scope,items in changes.items():
            if scope not in current or not isinstance(items,dict): raise ValueError('Invalid file.')
            if data.get('revisions',{}).get(scope) != C.digest(current[scope]): raise ValueError('The file changed outside the panel. Reload before saving.')
            doc = C.Document(scope,current[scope]); result[scope] = doc.edit(items)
            for key,value in items.items():
                old = doc.fields[key].value
                if old != value: differences.append({'file':scope,'key':key,'before':'[hidden]' if C.SECRET.search(key) else old,'after':'[hidden]' if C.SECRET.search(key) else value})
        for scope,value in structural.items():
            if changes.get(scope):raise ValueError('Do not combine structural edits and scalar edits in the same file.')
            if scope not in ('regions','points') or scope not in current: raise ValueError('Invalid structure.')
            if data.get('revisions',{}).get(scope) != C.digest(current[scope]): raise ValueError('The spawn file changed. Reload.')
            C.spawn_structure(scope,current[scope])
            result[scope] = C.spawn_lua(scope,value)
            differences.append({'file':scope,'key':'Structure (file will be regenerated; original comments remain in the backup)', 'before':C.spawn_structure(scope,current[scope]),'after':value})
        # The game's linked population/respawn controls must move together.
        sandbox_changes = changes.get('sandbox',{})
        linked = {}
        if 'Zombies' in sandbox_changes:
            linked['ZombieConfig.PopulationMultiplier'] = [2.5,1.6,1.2,0.65,0.15,0.0][int(sandbox_changes['Zombies'])-1]
        if 'ZombieRespawn' in sandbox_changes:
            i = int(sandbox_changes['ZombieRespawn'])-1
            linked.update({'ZombieConfig.RespawnHours':[16.0,72.0,216.0,0.0][i], 'ZombieConfig.RespawnUnseenHours':[6.0,16.0,48.0,0.0][i], 'ZombieConfig.RespawnMultiplier':[0.5,0.1,0.05,0.0][i]})
        linked = {k:v for k,v in linked.items() if k not in sandbox_changes}
        if linked:
            doc = C.Document('sandbox',result['sandbox'])
            for key,value in linked.items():
                if key in doc.fields: differences.append({'file':'sandbox','key':key,'before':doc.fields[key].value,'after':value})
            result['sandbox'] = doc.edit({k:v for k,v in linked.items() if k in doc.fields})
        now = time.monotonic()
        with jobs_lock:
            for key in list(proposals):
                if now-proposals[key]['time']>600: del proposals[key]
            if len(proposals)>30: raise ValueError('Too many previews; wait or save one.')
            ident = uuid.uuid4().hex
            proposals[ident] = dict(time=now,user=session['generation'],base={k:C.digest(v) for k,v in current.items()},result=result,count=len(differences))
        return jsonify(id=ident,differences=differences)

    @app.post('/api/commit')
    def commit():
        with jobs_lock: proposal = proposals.pop(request.get_json().get('id'),None)
        if not proposal or proposal['user']!=session['generation'] or time.monotonic()-proposal['time']>600: raise ValueError('Preview expired.')
        with locked():
            current = C.read_all()
            if proposal['base'] != {k:C.digest(v) for k,v in current.items()}: raise ValueError('Files changed. Reload and review again.')
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            history = S.STATE / 'config-history' / stamp
            history.mkdir(parents=True,mode=0o700)
            for scope,text in current.items(): atomic(history / S.FILES[scope],text)
            try:
                for scope,text in proposal['result'].items():
                    if current[scope]!=text: atomic(S.DATA / 'Server' / S.FILES[scope],text)
            except Exception:
                for scope,text in current.items(): atomic(S.DATA / 'Server' / S.FILES[scope],text)
                raise
        record('config', 'warn', count=proposal['count'], files=sorted(k for k,v in proposal['result'].items() if current[k]!=v))
        return jsonify(ok=True,message='Saved. The game server has not been restarted.')

    @app.get('/api/logs')
    def logs():
        args = request.args
        until = args.get('until')
        return jsonify(O.logs(args.get('source', 'game'), args.get('cursor') or None,
                              float(until) if until else None, int(args.get('lines', 1000))))

    @app.get('/api/logs/download')
    def logs_download():
        source = request.args.get('source', 'game')
        text = O.log_text(source)
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M')
        return Response(text, mimetype='text/plain', headers={'Content-Disposition': f'attachment; filename="{S.NAME}-{source}-{stamp}.log"'})

    @app.get('/api/backups')
    def backups(): return jsonify(backups=O.backup_list(), disk=O.disk_usage())

    @app.get('/api/mods')
    def mods(): return jsonify(O.mods_report())

    @app.get('/api/workshop')
    def workshop(): return jsonify(O.workshop_info(request.args.get('ids', '').split(',')))

    @app.post('/api/operations')
    def operate():
        data = request.get_json(); action = data.get('action')
        if action not in ('start','stop','restart','backup','restore','update'): raise ValueError('Action not allowed.')
        payload = {'confirmed': data.get('confirmed') is True, 'backup':data.get('backup'), 'notice': notice(action, language())}
        with jobs_lock:
            if any(j['state']=='running' for j in jobs.values()): raise ValueError('An operation is already running.')
            if len(jobs)>50: jobs.clear()
            ident=uuid.uuid4().hex
            jobs[ident]={'id':ident,'state':'running','message':'Preparing','action':action,'backup':payload['backup'],
                         'steps':O.STEPS[action],'step':None,'done':[],'started':time.time(),'user':session.get('user')}
        def progress(message):
            with jobs_lock:
                job=jobs[ident]; job['message']=message; step=O.STEP_OF.get(message)
                if step and step!=job['step']:
                    if job['step']: job['done'].append(job['step'])
                    job['step']=step
                write_json(S.STATE / 'last-operation.json',job)
        def run():
            try: result=O.execute(action,payload,progress); state='done'
            except Exception as e:
                result = str(e) if isinstance(e,ValueError) else 'Operation error; check the panel service log.'
                state='error'
                if not isinstance(e,ValueError): app.logger.exception('Operation %s failed',action)
            with jobs_lock:
                job=jobs[ident]
                if state=='done' and job['step']: job['done'].append(job['step']); job['step']=None
                job.update(state=state,message=result,finished=time.time())
                write_json(S.STATE / 'last-operation.json',job)
            record(action if action=='backup' else 'operation', 'ok' if state=='done' else 'error', action=action, message=result)
        threading.Thread(target=run,daemon=True).start()
        return jsonify(id=ident),202

    @app.get('/api/operations/<ident>')
    def job(ident):
        with jobs_lock: value = copy.deepcopy(jobs.get(ident))
        if value is None: abort(404)
        return jsonify(value)

    @app.get('/api/last-operation')
    def last_operation():
        p=S.STATE / 'last-operation.json'
        return jsonify(json.loads(p.read_text()) if p.exists() else {})

    return app

def create_admin():
    initialize()
    username = input('Panel admin username: ').strip()
    if not re.fullmatch(r'[\w.-]{1,64}',username): raise SystemExit('Invalid username.')
    password = getpass.getpass('Password (at least 12 characters): ')
    if len(password)<12 or len(password)>1024: raise SystemExit('Invalid length.')
    if password != getpass.getpass('Repeat the password: '): raise SystemExit('Passwords do not match.')
    write_json(S.STATE / 'account.json',{'username':username,'hash':generate_password_hash(password,method='scrypt'),'generation':secrets.token_hex(16)})
    print('Account created. Previous sessions are invalidated.')

if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['create-admin']: create_admin()
    elif not sys.argv[1:]:
        def sampler():
            while True:
                try: O.sample()
                except Exception: pass
                time.sleep(60)
        threading.Thread(target=sampler, daemon=True).start()
        serve(create_app(),host=S.BIND,port=S.PORT,threads=8, max_request_body_size=1024*1024)
    else: raise SystemExit('Usage: app.py [create-admin]')

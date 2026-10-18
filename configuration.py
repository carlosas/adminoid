"""Lossless scalar editing. Lua is tokenized and parsed, never evaluated."""
import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import settings as S

SECRET = re.compile(r'password|secret|token', re.I)
PROTECTED = {'ResetID', 'ServerPlayerID', 'PlayerServerID', 'VERSION'}
TOKEN = re.compile(r'(?P<space>\s+)|(?P<comment>--\[\[.*?\]\]|--[^\n]*)|(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|(?P<number>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<id>[A-Za-z_][A-Za-z_0-9]*)|(?P<punct>[{}\[\]=,;()])', re.S)

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

@dataclass
class Field:
    key: str
    value: object
    start: int
    end: int
    kind: str
    help: str = ''

class Lua:
    def __init__(self, text):
        self.text, self.tokens, self.fields, self.i = text, [], {}, 0
        pos = 0
        while pos < len(text):
            m = TOKEN.match(text, pos)
            if not m:
                raise ValueError('Custom Lua: unsupported syntax; preserved without editing.')
            if m.lastgroup not in ('space', 'comment'):
                self.tokens.append((m.group(), m.start(), m.end(), m.lastgroup))
            pos = m.end()
        if self.peek() == 'SandboxVars':
            self.pop(); self.expect('='); self.table('')
        elif self.peek() == 'function':
            self.pop()
            if self.pop()[0] not in ('SpawnRegions', 'SpawnPoints'):
                raise ValueError('Custom Lua function cannot be edited.')
            self.expect('('); self.expect(')'); self.expect('return')
            self.table(''); self.expect('end')
        else:
            raise ValueError('Custom Lua file cannot be edited.')
        if self.peek() == ';': self.pop()
        if self.i != len(self.tokens):
            raise ValueError('Additional Lua code: file preserved as read-only.')

    def peek(self, n=0):
        return self.tokens[self.i+n][0] if self.i+n < len(self.tokens) else None

    def pop(self):
        if self.i >= len(self.tokens): raise ValueError('Incomplete Lua.')
        t = self.tokens[self.i]; self.i += 1
        return t

    def expect(self, value):
        if self.pop()[0] != value: raise ValueError('Unsupported Lua structure.')

    def table(self, prefix):
        self.expect('{'); index = 0
        while self.peek() != '}':
            index += 1
            if self.peek(1) == '=':
                name = self.pop()[0]; self.pop()
            elif self.peek() == '[':
                self.pop(); tok = self.pop()
                name = str(ast.literal_eval(tok[0])) if tok[3] in ('string', 'number') else None
                if name is None: raise ValueError('Dynamic Lua keys are unsupported.')
                self.expect(']'); self.expect('=')
            else:
                name = str(index)
            key = prefix + '.' + name if prefix else name
            if self.peek() == '{':
                self.table(key)
            else:
                tok = self.pop(); raw = tok[0]
                if tok[3] == 'string':
                    try: value = ast.literal_eval(raw)
                    except (ValueError, SyntaxError): raise ValueError('Unsupported Lua escape.')
                    kind = 'string'
                elif raw in ('true', 'false'):
                    value, kind = raw == 'true', 'boolean'
                elif tok[3] == 'number':
                    value = float(raw) if any(c in raw.lower() for c in '.e') else int(raw)
                    kind = 'number' if isinstance(value, float) else 'integer'
                else: raise ValueError('Custom Lua expression cannot be edited.')
                if key in self.fields: raise ValueError('Duplicate key.')
                # Comments immediately preceding the assignment carry vanilla bounds/options.
                line = self.text.rfind('\n', 0, tok[1])
                before = self.text[:line].splitlines()
                comments = []
                for prior in reversed(before):
                    if prior.strip().startswith('--'): comments.insert(0, prior.strip()[2:].strip())
                    else: break
                self.fields[key] = Field(key, value, tok[1], tok[2], kind, '\n'.join(comments))
            if self.peek() in (',', ';'): self.pop()
            elif self.peek() != '}': raise ValueError('Unsupported Lua separator.')
        self.expect('}')

def ini(text):
    fields, comments, offset = {}, [], 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip('\r\n')
        if raw.startswith('#'): comments.append(raw[1:].strip())
        elif '=' in raw:
            key, value = raw.split('=', 1)
            if key in fields: raise ValueError('Duplicate INI key: ' + key)
            kind = 'string'
            if value in ('true', 'false'): value, kind = value == 'true', 'boolean'
            elif re.search(r'\bMin:', '\n'.join(comments)) and re.fullmatch(r'-?\d+(?:\.\d+)?', value):
                value, kind = (float(value), 'number') if '.' in value else (int(value), 'integer')
            fields[key] = Field(key, value, offset + len(key) + 1, offset + len(raw), kind, '\n'.join(comments))
            comments = []
        elif raw.strip(): comments = []
        offset += len(line)
    return fields

def translations(lang='en'):
    out = {}
    for lang in (('EN', 'ES') if lang == 'es' else ('EN',)):
        for name in ('Sandbox', 'UI'):
            p = S.GAME / 'media/lua/shared/Translate' / lang / (name + '.json')
            if p.exists():
                try: out.update(json.loads(p.read_text(encoding='utf-8-sig')))
                except (ValueError, UnicodeError): pass
    return out

def categories(tr):
    p = S.GAME / 'media/lua/client/OptionScreens/ServerSettingsScreen.lua'
    result, page, scope = {}, 'Advanced', 'server'
    if not p.exists(): return result
    source = p.read_text()
    source = source[source.find('SettingsTable ='):]
    for line in source.splitlines():
        m = re.match(r'^\s*name = "([^"]+)"', line)
        if m:
            key = m[1]
            if key == 'Sandbox': scope = 'sandbox'
            if key not in ('Sandbox', 'INI'):
                page = tr.get(('Sandbox_' if scope == 'sandbox' else 'UI_ServerSettingGroup_') + key, key)
        m = re.match(r'^\s*\{ name = "([^"]+)"', line)
        if m and not m[1].startswith(('Sandbox_', 'UI_')): result[(scope, m[1])] = page
    return result

# Options the game's settings screen leaves out but that belong next to one it shows.
SIBLINGS = {
    'StartYear': 'StartMonth',
    'PVPLogToolChat': 'PVP', 'PVPLogToolFile': 'PVP', 'KnockedDownAllowed': 'PVP', 'SafetyDisconnectDelay': 'SafetySystem',
    'DenyLoginOnOverloadedServer': 'MaxPlayers', 'LoginQueueEnabled': 'MaxPlayers', 'LoginQueueConnectTimeout': 'MaxPlayers',
    'SneakModeHideFromOtherPlayers': 'HidePlayersBehindYou',
    'server_browser_announced_ip': 'DefaultPort',
    'SteamVAC': 'UDPPort', 'DisableScoreboard': 'SteamScoreboard',
    'BanKickGlobalSound': 'ClientCommandFilter', 'HideAdminsInPlayerList': 'ClientCommandFilter',
    'MaxSafezoneSize': 'AdminSafehouse',
    'ChatStreams': 'GlobalChat', 'BadWordListFile': 'GlobalChat', 'GoodWordListFile': 'GlobalChat',
    'BadWordPolicy': 'GlobalChat', 'BadWordReplacement': 'GlobalChat',
    'UltraSpeedDoesnotAffectToAnimals': 'FastForwardMultiplier',
    'CarEngineAttractionModifier': 'SpeedLimit', 'DisableVehicleTowing': 'SpeedLimit',
    'DisableTrailerTowing': 'SpeedLimit', 'DisableBurntTowing': 'SpeedLimit',
    # Sandbox
    'NightLength': 'DayLength', 'AlarmDecayModifier': 'AlarmDecay', 'AnimalMetaStatsModifier': 'AnimalStatsModifier',
    'InsaneLootFactor': 'HoursForLootRespawn', 'ExtremeLootFactor': 'HoursForLootRespawn', 'RareLootFactor': 'HoursForLootRespawn',
    'NormalLootFactor': 'HoursForLootRespawn', 'CommonLootFactor': 'HoursForLootRespawn', 'AbundantLootFactor': 'HoursForLootRespawn',
}

def metadata(scope, field, tr, cats):
    key = field.key
    bases = ['Sandbox_' + key, 'Sandbox_' + key.split('.')[-1]] if scope == 'sandbox' else ['UI_ServerOption_' + key]
    if key == 'Zombies': bases.insert(0, 'Sandbox_ZombieCount')
    if key == 'Distribution': bases.insert(0, 'Sandbox_ZombieDistribution')
    base = next((b for b in bases if b in tr), bases[0])
    label = tr.get(base, key)
    help_text = tr.get(base + '_tooltip', field.help).replace('<LINE>', '\n').replace('<BR>', '\n')
    choices = {int(n): label.strip() for n, label in re.findall(r'^\s*(\d+)\s*=\s*(.+)$', field.help, re.M)}
    choices = {n: tr.get(base + '_option' + str(n), label) for n, label in choices.items()}
    bounds = re.search(r'Min:\s*(-?[\d.]+)\s+Max:\s*(-?[\d.]+)', field.help)
    # Options the game's settings screen does not group go to an Advanced section of their own group.
    category = cats.get((scope, key)) or cats.get((scope, SIBLINGS.get(key)), 'Advanced')
    if scope in ('regions', 'points'): category = 'Spawn · ' + ('regions' if scope == 'regions' else 'points')
    if key in ('Mods', 'WorkshopItems', 'Map', 'SpawnPoint'): category = 'Mods and maps'
    locked = key in PROTECTED
    return dict(key=key, label=label, help=help_text, category=category, kind=field.kind,
                value=None if SECRET.search(key) else field.value, secret=bool(SECRET.search(key)),
                configured=bool(field.value) if SECRET.search(key) else None, choices=choices,
                minimum=float(bounds[1]) if bounds else None, maximum=float(bounds[2]) if bounds else None,
                readonly=locked)

# Mod sandbox options: each mod declares them in media/sandbox-options.txt and they live in SandboxVars under their name.
OPTION = re.compile(r'^\s*option\s+([\w.]+)\s*=?\s*\{(.*?)\}', re.S | re.M)

def mod_media(folder):
    """media folders of a mod: its own and, for Build 42 layouts (mods/X/42), the shared common one."""
    return [d / 'media' for d in (folder, folder.parent / 'common') if (d / 'media').is_dir()]

def mod_translations(media, lang):
    out = {}
    for code in (('EN', 'ES') if lang == 'es' else ('EN',)):
        for base in media:
            folder = base / 'lua/shared/Translate' / code
            for p in sorted(folder.glob('Sandbox*')) if folder.is_dir() else []:
                if p.is_symlink() or p.stat().st_size > 1_000_000: continue
                text = p.read_text(encoding='utf-8-sig', errors='replace')
                if p.suffix == '.json':
                    try: out.update({k: v for k, v in json.loads(text).items() if isinstance(v, str)})
                    except ValueError: pass
                else:
                    out.update({k: v.replace('\\"', '"') for k, v in re.findall(r'(\w+)\s*=\s*"((?:[^"\\\n]|\\.)*)"', text)})
    return out

def parse_options(text):
    options = {}
    for name, body in OPTION.findall(text):
        props = dict((k, v.strip().strip('"')) for k, v in re.findall(r'(\w+)\s*=\s*([^,\n]*)', body))
        options[name] = props
    return options

def mod_options(lang='en'):
    """Installed mods with the sandbox options they declare, labelled with the mod's own translations."""
    from operations import installed_mods
    mods = []
    for mod in installed_mods(dirs=True):
        media = mod_media(mod['dir'])
        options = {}
        for base in media:
            p = base / 'sandbox-options.txt'
            if p.is_file() and not p.is_symlink() and p.stat().st_size < 1_000_000:
                options.update(parse_options(p.read_text(encoding='utf-8-sig', errors='replace')))
        tr = mod_translations(media, lang) if options else {}
        defs = {}
        for key, o in options.items():
            kind = o.get('type', 'string')
            name = 'Sandbox_' + (o.get('translation') or key.replace('.', '_'))
            values = 'Sandbox_' + (o.get('valueTranslation') or o.get('translation') or key.replace('.', '_'))
            number = lambda v: float(v) if v not in (None, '') and re.fullmatch(r'-?\d+(\.\d+)?', v) else None
            choices = {n: tr.get(f'{values}_option{n}', str(n)) for n in range(1, int(o['numValues']) + 1)} if kind == 'enum' and str(o.get('numValues', '')).isdigit() else {}
            kind = {'enum': 'integer', 'double': 'number', 'boolean': 'boolean', 'integer': 'integer'}.get(kind, 'string')
            default = o.get('default')
            try: default = {'boolean': lambda v: v == 'true', 'integer': int, 'number': float}.get(kind, str)(default) if default is not None else None
            except ValueError: default = None
            defs[key] = dict(label=tr.get(name, key), help=tr.get(name + '_tooltip', '').replace('<LINE>', '\n').replace('<BR>', '\n'),
                             kind=kind, default=default, choices=choices,
                             minimum=number(o.get('min')), maximum=number(o.get('max')))
        mods.append({'id': mod['id'], 'name': mod.get('name') or mod['id'], 'options': defs})
    return mods

def apply_mod_option(meta, mod, option):
    """Label, help, bounds and choices of a SandboxVars field from the mod that declares it."""
    meta.update(category=mod['name'], mod=mod['id'], label=option['label'], help=option['help'] or meta['help'])
    if option['choices']: meta['choices'] = option['choices']
    if option['minimum'] is not None and option['maximum'] is not None: meta.update(minimum=option['minimum'], maximum=option['maximum'])
    return meta

class Document:
    def __init__(self, scope, text):
        self.scope, self.text = scope, text
        self.fields = ini(text) if scope == 'server' else Lua(text).fields

    def edit(self, changes):
        tr, cats = translations(), None
        cats = categories(tr)
        declared = {k: (mod, o) for mod in mod_options() for k, o in mod['options'].items()} if self.scope == 'sandbox' else {}
        edits = []
        for key, value in changes.items():
            if key not in self.fields or key in PROTECTED: raise ValueError('Read-only option: ' + key)
            f = self.fields[key]; m = metadata(self.scope, f, tr, cats)
            if key in declared: m = apply_mod_option(m, *declared[key])
            if f.kind == 'boolean':
                if type(value) is not bool: raise ValueError(key + ': must be yes/no.')
                raw = 'true' if value else 'false'
            elif f.kind in ('number', 'integer'):
                if type(value) not in (int, float) or not math.isfinite(value): raise ValueError(key + ': invalid number.')
                if f.kind == 'integer' and int(value) != value: raise ValueError(key + ': must be an integer.')
                if m['minimum'] is not None and not m['minimum'] <= value <= m['maximum']: raise ValueError(key + ': outside allowed limits.')
                if m['choices'] and value not in m['choices']: raise ValueError(key + ': invalid option.')
                raw = str(int(value)) if f.kind == 'integer' else str(value)
            else:
                if not isinstance(value, str) or len(value) > 65536 or any(c in value for c in '\r\n\x00'): raise ValueError(key + ': invalid text.')
                if key == 'WorkshopItems' and value and not re.fullmatch(r'\d+(;\d+)*', value): raise ValueError('WorkshopItems: numeric IDs separated by semicolons.')
                raw = value if self.scope == 'server' else json.dumps(value, ensure_ascii=False)
            edits.append((f.start, f.end, raw))
        result = self.text
        for start, end, raw in sorted(edits, reverse=True): result = result[:start] + raw + result[end:]
        Document(self.scope, result)
        return result

def read_all():
    return {scope: (S.DATA / 'Server' / name).read_text() for scope, name in S.FILES.items() if (S.DATA / 'Server' / name).exists()}

def catalog(lang='en'):
    tr = translations(lang); cats = categories(tr); result = {}
    for scope, text in read_all().items():
        try:
            doc = Document(scope, text)
            fields=[metadata(scope, f, tr, cats) for f in doc.fields.values()]
            if scope == 'sandbox':
                # Options declared by installed mods get their own section per mod; the ones not in the file yet
                # (mod inactive or not started since) are listed read-only with their default.
                mods = mod_options(lang)
                declared = {k: (mod, o) for mod in mods for k, o in mod['options'].items()}
                for f in fields:
                    if f['key'] in declared: apply_mod_option(f, *declared[f['key']])
                for mod in mods:
                    for k, o in mod['options'].items():
                        if k in doc.fields: continue
                        f = dict(key=k, label=k, help='', category='', kind=o['kind'], value=o['default'], secret=False, configured=None,
                                 choices={}, minimum=None, maximum=None, readonly=True, absent=True)
                        fields.append(apply_mod_option(f, mod, o))
            
            if lang=='es':
                for f in fields:
                    f['category']=f['category'].replace('Spawn · regions','Aparición · regiones').replace('Spawn · points','Aparición · puntos').replace('Mods and maps','Mods y mapas')
                    if f['category']=='Advanced': f['category']='Avanzado'
            result[scope] = dict(revision=digest(text), fields=fields, error=None)
            if scope == 'sandbox': result[scope]['mods'] = [{'id': m['id'], 'name': m['name'], 'options': len(m['options'])} for m in mods]
        except ValueError as e:
            result[scope] = dict(revision=digest(text), fields=[], error=str(e))
    return result

def spawn_structure(scope, text):
    fields = Document(scope, text).fields
    result = {} if scope == 'points' else []
    if scope == 'regions':
        rows = {}
        for key, field in fields.items():
            parts = key.split('.')
            if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ('name','file','serverfile'):
                raise ValueError('Custom regions: only existing fields can be edited.')
            rows.setdefault(int(parts[0]), {})[parts[1]] = field.value
        result = [v for k,v in sorted(rows.items())]
    else:
        rows = {}
        for key, field in fields.items():
            parts = key.split('.')
            if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ('worldX','worldY','posX','posY','posZ'):
                raise ValueError('Custom spawn points: only existing fields can be edited.')
            rows.setdefault(parts[0], {}).setdefault(int(parts[1]), {})[parts[2]] = field.value
        result = {k:[v for i,v in sorted(rows.items())] for k,rows in rows.items()}
    return result

def spawn_lua(scope, value):
    if scope == 'regions':
        if not isinstance(value, list) or len(value) > 1000: raise ValueError('Invalid region list.')
        for row in value:
            if not isinstance(row, dict) or set(row) not in ({'name','file'}, {'name','serverfile'}): raise ValueError('Each region needs name and either file or serverfile.')
            if not all(isinstance(v,str) and v and not any(c in v for c in '\n\r\x00') for v in row.values()): raise ValueError('Invalid region text.')
            path = row.get('file', row.get('serverfile'))
            if Path(path).is_absolute() or '..' in Path(path).parts: raise ValueError('Region path not allowed.')
    elif scope == 'points':
        if not isinstance(value,dict) or len(value)>100: raise ValueError('Invalid professions.')
        for profession, rows in value.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', profession) or not isinstance(rows,list) or len(rows)>1000: raise ValueError('Invalid profession.')
            for row in rows:
                if not isinstance(row,dict) or not {'worldX','worldY','posX','posY'} <= set(row) <= {'worldX','worldY','posX','posY','posZ'}: raise ValueError('Incomplete coordinates.')
                if any(type(v) is not int for v in row.values()): raise ValueError('Coordinates must be integers.')
    else: raise ValueError('Invalid file.')
    def encode(obj, level=1):
        if isinstance(obj,(list,dict)):
            items = obj.items() if isinstance(obj,dict) else ((None,v) for v in obj)
            return '{\n' + ''.join('    '*level + (k+' = ' if k is not None else '') + encode(v,level+1) + ',\n' for k,v in items) + '    '*(level-1)+'}'
        return json.dumps(obj,ensure_ascii=False)
    result = 'function '+ ('SpawnRegions' if scope=='regions' else 'SpawnPoints')+'()\n    return '+encode(value,2)+'\nend\n'
    Document(scope,result)
    return result

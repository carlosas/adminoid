"""Interactive CLI for legacy maintenance entry points."""
import sys
import operations as O
if __name__=='__main__':
    action=sys.argv[1] if len(sys.argv)==2 else ''
    if action not in ('backup','update'):raise SystemExit('Usage: admin.py backup|update')
    p=O.players()
    print('Connected players:',p['count'] if p['count'] is not None else 'unknown')
    if input('This operation may save and stop the world. Type YES to continue: ')!='YES':raise SystemExit('Cancelled.')
    print(O.execute(action,{'confirmed':True},lambda text:print(text,flush=True)))

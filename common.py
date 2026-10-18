import contextlib
import fcntl
import json
import os
import tempfile
import settings as S

def initialize():
    S.STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(S.STATE, 0o700)

def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(content); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
        dfd = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(name): os.unlink(name)

def write_json(path, obj): atomic(path, json.dumps(obj, ensure_ascii=False, indent=2))

@contextlib.contextmanager
def locked():
    initialize()
    with (S.STATE / 'operation.lock').open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another operation is running. Wait for it to finish.')
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)

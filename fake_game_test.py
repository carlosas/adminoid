#!/usr/bin/python3
"""Test fixture only. Never used by a systemd unit."""
import sys
print('*** SERVER STARTED ***',flush=True)
for line in sys.stdin:
    cmd=line.strip()
    if cmd=='players':
        print('Players connected (2):\n-Alice\n-Bob',flush=True)
    elif cmd=='save':
        print('Saving finished',flush=True)
    elif cmd=='quit':
        print('Saving finished\nShutdown handling finished',flush=True)
        break

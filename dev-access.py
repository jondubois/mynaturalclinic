#!/usr/bin/env python3
"""Toggle write access for local frontend development.

The browser socket is unauthenticated until Keycloak is wired up, so Saasufy
refuses every write and every read of a `restrict` model. This flips the three
onboarding collections to `allow` so the clinician flow can actually be
exercised in a browser, and restores the specification's rules afterwards.

    ./dev-access.py open      # development only
    ./dev-access.py restore   # back to the rules in requirements.md

Never leave a service in `open` state once it holds real data.
"""
import json, sys, urllib.request, os

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()


def call(m, path, body=None, qs=''):
    u = f'https://saasufy.com/api/{path}' + (('?' + qs) if qs else '')
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(u, data=d, method=m)
    r.add_header('Authorization', f'Bearer {KEY}')
    if d:
        r.add_header('Content-Type', 'application/json')
    t = urllib.request.urlopen(r).read().decode().strip()
    return json.loads(t) if t and t[0] in '{[' else t


def models():
    out, off = {}, 0
    while True:
        r = call('GET', 'Model', None,
                 f'view=accountAlphabeticalView&offset={off}&pageSize=100')
        d = r['data']
        for m in d:
            m = m if isinstance(m, dict) else call('GET', f'Model/{m}')
            out[m['name']] = m['id']
        if r.get('isLastPage') or not d:
            break
        off += len(d)
    return out


OPEN = {'accessCreate': 'allow', 'accessRead': 'allow',
        'accessUpdate': 'allow', 'accessDelete': 'allow'}

SPEC = {
    'Clinician':    {'accessCreate': 'restrict', 'accessRead': 'allow',
                     'accessUpdate': 'restrict', 'accessDelete': 'block'},
    'Credential':   {'accessCreate': 'restrict', 'accessRead': 'allow',
                     'accessUpdate': 'restrict', 'accessDelete': 'restrict'},
    'Availability': {'accessCreate': 'restrict', 'accessRead': 'restrict',
                     'accessUpdate': 'restrict', 'accessDelete': 'restrict'},
}

mode = sys.argv[1] if len(sys.argv) > 1 else ''
if mode not in ('open', 'restore'):
    print(__doc__)
    raise SystemExit(1)

mm = models()
for name in SPEC:
    call('PUT', f'Model/{mm[name]}', OPEN if mode == 'open' else SPEC[name])
    print(f'  {name}: {"OPEN (dev)" if mode == "open" else "restored to spec"}')
call('POST', 'service/start')
print('deployed.')
if mode == 'open':
    print('\nWARNING: Clinician, Credential and Availability are now world-writable.')
    print('Run  ./dev-access.py restore  when you are done.')

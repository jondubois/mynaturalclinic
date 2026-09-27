#!/usr/bin/env python3
"""Copy every Credential record into the Document collection, preserving record ids.

Idempotent: a Document which already carries a Credential's id is left alone unless
--force is given. Run it after create-schema.py and a service deploy; it does not
delete anything, so Credential can be dropped separately once this has been checked.
"""
import json, sys, urllib.request, urllib.parse

ROOT = '/home/jon/Work/mynaturalclinic'
KEY = open(f'{ROOT}/.saasufy-api-key').read().strip()
SERVICE = open(f'{ROOT}/.saasufy-service-url').read().strip()
BASE = SERVICE.replace('wss://', 'https://').replace('/socketcluster/', '/api')

FORCE = '--force' in sys.argv
DRY = '--dry-run' in sys.argv

# Credential field -> Document field; the rest are stamped by the service.
COPY = {'accountId': 'accountId', 'clinicianId': 'clinicianId', 'type': 'type',
        'institution': 'institution', 'qualificationName': 'documentName',
        'yearAwarded': 'yearAwarded', 'registrationNumber': 'registrationNumber',
        'document': 'document', 'reviewStatus': 'reviewStatus',
        'reviewNote': 'reviewNote', 'reviewedAt': 'reviewedAt', 'groupId': 'groupId'}

def call(method, path, body=None, allow404=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'{BASE}/{path}', data=data, method=method)
    req.add_header('Authorization', f'Bearer {KEY}')
    if data: req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode().strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if allow404 and e.code == 404: return None
        raise RuntimeError(f'{method} {path} -> {e.code}: {e.read().decode()[:400]}') from None

def all_credentials():
    """No view spans every record, so union the three reviewStatus buckets."""
    out = {}
    for status in ('pending', 'approved', 'rejected'):
        off = 0
        while True:
            q = urllib.parse.urlencode({'view': 'reviewQueueView',
                                        'viewParams[reviewStatus]': status,
                                        'offset': off, 'pageSize': 100})
            r = call('GET', f'Credential?{q}')
            d = r.get('data', [])
            for item in d:
                rec = item if isinstance(item, dict) else call('GET', f'Credential/{item}')
                out[rec['id']] = rec
            if r.get('isLastPage') or not d: break
            off += len(d)
    return sorted(out.values(), key=lambda r: r.get('createdAt') or 0)

creds = all_credentials()
print(f'{len(creds)} Credential records')

copied = skipped = 0
for c in creds:
    cid = c['id']
    existing = call('GET', f'Document/{cid}', allow404=True)
    if existing and not FORCE:
        print(f'  = {cid} already migrated')
        skipped += 1
        continue
    body = {'id': cid}
    body.update({dst: c[src] for src, dst in COPY.items() if c.get(src) not in (None, '')})
    if DRY:
        print(f'  ~ {cid} would copy {sorted(body)}')
        continue
    if existing:
        call('PUT', f'Document/{cid}', {dst: body.get(dst, '') for dst in COPY.values()})
    else:
        call('POST', 'Document', body)
    # createdAt is stamped on write and both views order by it, so restore it after.
    if c.get('createdAt'):
        call('PUT', f'Document/{cid}', {'createdAt': c['createdAt']})
    print(f'  + {cid} {c.get("qualificationName") or "(untitled)"}')  # Credential's name
    copied += 1

if DRY:
    sys.exit(0)

print('\nverifying...')
bad = 0
for c in creds:
    d = call('GET', f'Document/{c["id"]}', allow404=True)
    if d is None:
        print(f'  MISSING {c["id"]}'); bad += 1; continue
    for src, dst in list(COPY.items()) + [('createdAt', 'createdAt')]:
        if (c.get(src) or '') != (d.get(dst) or ''):
            print(f'  MISMATCH {c["id"]}.{src} -> {dst}'); bad += 1
print(f'{copied} copied, {skipped} already present, {bad} problems')
sys.exit(1 if bad else 0)

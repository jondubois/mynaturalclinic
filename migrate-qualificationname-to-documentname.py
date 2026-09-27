#!/usr/bin/env python3
"""Copy Document.qualificationName into Document.documentName.

Run after create-schema.py has added documentName and the service has been
deployed. Idempotent; --dry-run reports without writing. The qualificationName
ModelField has since been deleted, so a re-run finds nothing to do; the orphaned
key survives in the stored records and is what this still reads from.
"""
import json, sys, urllib.request, urllib.parse

ROOT = '/home/jon/Work/mynaturalclinic'
KEY = open(f'{ROOT}/.saasufy-api-key').read().strip()
SERVICE = open(f'{ROOT}/.saasufy-service-url').read().strip()
BASE = SERVICE.replace('wss://', 'https://').replace('/socketcluster/', '/api')
DRY = '--dry-run' in sys.argv

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'{BASE}/{path}', data=data, method=method)
    req.add_header('Authorization', f'Bearer {KEY}')
    if data: req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode().strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {path} -> {e.code}: {e.read().decode()[:400]}') from None

def all_documents():
    """No view spans every record, so union the three reviewStatus buckets."""
    out = {}
    for status in ('pending', 'approved', 'rejected'):
        off = 0
        while True:
            q = urllib.parse.urlencode({'view': 'reviewQueueView',
                                        'viewParams[reviewStatus]': status,
                                        'offset': off, 'pageSize': 100})
            r = call('GET', f'Document?{q}')
            d = r.get('data', [])
            for item in d:
                rec = item if isinstance(item, dict) else call('GET', f'Document/{item}')
                out[rec['id']] = rec
            if r.get('isLastPage') or not d: break
            off += len(d)
    return list(out.values())

docs = all_documents()
print(f'{len(docs)} Document records')

moved = skipped = 0
for d in docs:
    old, new = d.get('qualificationName'), d.get('documentName')
    if not old or new == old:
        print(f'  = {d["id"]} nothing to do')
        skipped += 1
        continue
    if DRY:
        print(f'  ~ {d["id"]} {old!r} -> documentName')
        continue
    call('PUT', f'Document/{d["id"]}', {'documentName': old})
    print(f'  + {d["id"]} {old}')
    moved += 1

if DRY:
    sys.exit(0)

print('\nverifying...')
bad = 0
for d in all_documents():
    if (d.get('qualificationName') or '') != (d.get('documentName') or ''):
        print(f'  MISMATCH {d["id"]}: {d.get("qualificationName")!r} vs {d.get("documentName")!r}')
        bad += 1
print(f'{moved} copied, {skipped} skipped, {bad} problems')
sys.exit(1 if bad else 0)

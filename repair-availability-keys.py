#!/usr/bin/env python3
"""Recompute utcHourKeys on weekly Availability rows from each clinician's timezone.

Run after changing a clinician's timezone, or to repair rows written while the field
was unavailable to the browser. Idempotent. Rows that lost their timing data in the
minute-of-week migration cannot be recovered and are only reported.
"""
import json, urllib.request, os, sys
from tzkeys import weekly_keys

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()
SVC = 'https://saasufy.com/sid8016/api'
APPLY = '--apply' in sys.argv


def call(method, path, body=None, qs=''):
    url = f'{SVC}/{path}' + (('?' + qs) if qs else '')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {KEY}')
    if data: req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode().strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {path} -> {e.code}: {e.read().decode()[:300]}') from None


rows, offset = [], 0
while True:
    page = call('GET', 'Availability', None, f'offset={offset}&pageSize=100')
    data = page.get('data', [])
    rows += [call('GET', f'Availability/{r}') if isinstance(r, str) else r for r in data]
    if page.get('isLastPage') or not data: break
    offset += len(data)

clinicians = {}
fixed = already = orphaned = 0
for row in rows:
    if row.get('kind') != 'weekly': continue
    start, end = row.get('startMinuteOfWeek'), row.get('endMinuteOfWeek')
    if start is None or end is None:
        orphaned += 1
        continue
    cid = row.get('clinicianId')
    if cid not in clinicians:
        clinicians[cid] = call('GET', f'Clinician/{cid}')
    clinician = clinicians[cid]
    tz = clinician.get('timezone')
    name = clinician.get('displayName') or cid
    if not tz:
        print(f'  {name}: no timezone on profile, skipped')
        continue
    want = weekly_keys(start // 1440, start % 1440, end - (start // 1440) * 1440, tz)
    if row.get('utcHourKeys') == want:
        already += 1
        continue
    print(f'  {name} [{tz}] mow={start}-{end}')
    print(f'    was:  {row.get("utcHourKeys")}')
    print(f'    now:  {want}')
    if APPLY:
        call('PUT', f'Availability/{row["id"]}', {'utcHourKeys': want})
    fixed += 1

print(f'\n{fixed} row(s) {"updated" if APPLY else "would be updated"}, '
      f'{already} already correct, {orphaned} unrecoverable.')
if orphaned:
    print('Unrecoverable rows predate the minute-of-week migration and hold no hours.\n'
          'Delete them and re-add, or re-run the seed scripts.')
if not APPLY and fixed:
    print('\nRe-run with --apply to write. Then rebuild the availabilityHours aggregation.')

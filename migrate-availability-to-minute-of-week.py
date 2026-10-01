#!/usr/bin/env python3
"""Convert Availability rows still on the (dayOfWeek, startMinute, endMinute) triple
to the single minute-of-week axis, and fill in their utcHourKeys.

Those three fields were dropped from the schema when the axis changed, so they are
no longer declared and a frontend component cannot ask for them. The rows kept their
values, which is why nothing had broken: every consumer so far was a Python script
reading the raw record. The /book calendar is the first consumer in the browser, and
it sees nothing on a row which has no startMinuteOfWeek.

    ./migrate-availability-to-minute-of-week.py --dry-run
    ./migrate-availability-to-minute-of-week.py

Idempotent: rows which already carry startMinuteOfWeek are left alone. The legacy
values stay on the record, undeclared and unread.
"""
import json, os, sys, urllib.error, urllib.request
from tzkeys import weekly_keys

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()
SVC = 'https://saasufy.com/sid8016/api'
DRY = '--dry-run' in sys.argv[1:]


def call(method, path, body=None, qs=''):
    url = f'{SVC}/{path}' + (('?' + qs) if qs else '')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {KEY}')
    if data:
        req.add_header('Content-Type', 'application/json')
    try:
        text = urllib.request.urlopen(req).read().decode().strip()
        return json.loads(text) if text and text[0] in '{[' else text.strip('"')
    except urllib.error.HTTPError as e:
        return f'ERR {e.code}: {e.read().decode()[:200]}'


clinicians = {c['id']: c for c in call('GET', 'Clinician', None, 'pageSize=200')['data']}
rows = call('GET', 'Availability', None, 'pageSize=200')['data']

done = skipped = failed = 0
for row in rows:
    if row.get('startMinuteOfWeek') is not None:
        skipped += 1
        continue
    day, start, end = row.get('dayOfWeek'), row.get('startMinute'), row.get('endMinute')
    name = clinicians.get(row['clinicianId'], {}).get('displayName', row['clinicianId'][:8])
    if day is None or start is None or end is None:
        print(f'  {name}: row {row["id"][:8]} has neither shape, left alone')
        failed += 1
        continue
    # An end at or before the start ran into the next day; the one axis holds that
    # without splitting the block, which is the whole point of it.
    if end <= start:
        end += 1440
    zone = clinicians.get(row['clinicianId'], {}).get('timezone')
    patch = {'startMinuteOfWeek': day * 1440 + start,
             'endMinuteOfWeek': day * 1440 + end}
    if zone:
        patch['utcHourKeys'] = weekly_keys(day, start, end, zone)
    print(f'  {name:<22} day {day} {start}-{end} -> '
          f'{patch["startMinuteOfWeek"]}-{patch["endMinuteOfWeek"]}'
          f'{"" if zone else "  (no timezone, keys left empty)"}')
    if DRY:
        done += 1
        continue
    result = call('PUT', f'Availability/{row["id"]}', patch)
    if str(result).startswith('ERR'):
        print(f'    {result}')
        failed += 1
    else:
        done += 1

print(f'\n{done} converted, {skipped} already current, {failed} failed'
      + (' (dry run — nothing written)' if DRY else ''))

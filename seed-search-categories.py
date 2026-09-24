#!/usr/bin/env python3
"""Seed the SearchCategory table that populates the /browse dropdowns.

Categories are levelled: level 1 is where the practitioner is, level 2 is what
they treat. Each level drives its own select, and the two are combined into a
single filter query. Each row carries the label a patient sees and the phase-2
query fragment it contributes, so adding a category is a data change, not a code
change.
"""
import json, urllib.request, os

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()
SVC = 'https://saasufy.com/sid8016/api'

def call(m, path, body=None, qs=''):
    u = f'{SVC}/{path}' + (('?' + qs) if qs else '')
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(u, data=d, method=m)
    r.add_header('Authorization', f'Bearer {KEY}')
    if d: r.add_header('Content-Type', 'application/json')
    try:
        t = urllib.request.urlopen(r).read().decode().strip()
        return json.loads(t) if t and t[0] in '{[' else t.strip('"')
    except urllib.error.HTTPError as e:
        return f'ERR {e.code}: {e.read().decode()[:160]}'

# Labels must not contain a comma or a colon: the options string is comma
# separated and the part before the first '=' is split on ':' for a type hint.
REGIONS = [('New South Wales','nsw'), ('Victoria','vic'), ('Queensland','qld'),
           ('Western Australia','wa'), ('South Australia','sa'), ('Tasmania','tas'),
           ('Australian Capital Territory','act'), ('Northern Territory','nt')]
SPECIALISATIONS = [('Naturopathy','naturopathy'), ('Nutrition','nutrition'),
                   ('Acupuncture','acupuncture'), ('Herbal Medicine','herbal-medicine'),
                   ('Homeopathy','homeopathy'), ('Remedial Massage','remedial-massage'),
                   ('Chinese Medicine','chinese-medicine'), ('Kinesiology','kinesiology')]
AILMENTS = [('Anxiety','anxiety'), ('Digestive Health','digestive-health'),
            ('Fatigue','fatigue'), ('Sleep Problems','sleep-problems'),
            ('Hormonal Health','hormonal-health'), ('Chronic Pain','chronic-pain'),
            ('Skin Conditions','skin-conditions'), ('Immune Support','immune-support')]

rows = []   # (label, query, kind, position, level)
for i, (label, slug) in enumerate(REGIONS):
    rows.append((label, f'region = {slug}', 'region', 10 + i, 1))
for i, (label, slug) in enumerate(SPECIALISATIONS):
    rows.append((label, f'topics contains (?i){slug}', 'specialisation', 100 + i, 2))
for i, (label, slug) in enumerate(AILMENTS):
    rows.append((label, f'topics contains (?i){slug}', 'ailment', 200 + i, 2))

removed = 0
while True:
    batch = call('GET', 'SearchCategory', None, 'pageSize=100').get('data', [])
    if not batch: break
    for x in batch:
        call('DELETE', f"SearchCategory/{x['id'] if isinstance(x, dict) else x}")
        removed += 1
print(f'removed {removed} existing categor(ies)')

for label, query, kind, position, level in rows:
    assert ',' not in label and ':' not in label, f'bad label: {label}'
    r = call('POST', 'SearchCategory',
             {'label': label, 'query': query, 'kind': kind,
              'position': position, 'level': level})
    if str(r).startswith('ERR'):
        print(' ', label, r)
print(f'seeded {len(rows)} categories: '
      f'level 1 = {len(REGIONS)} regions, '
      f'level 2 = {len(SPECIALISATIONS)} specialisations + {len(AILMENTS)} ailments')

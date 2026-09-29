#!/usr/bin/env python3
"""Seed one demo clinician for local frontend development.
Writes go through the Admin/Service HTTP API, which associates them with the account
in .saasufy-dev-account (set as serviceAccountId on the API credential)."""
import json, urllib.request, os
from tzkeys import weekly_keys

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()
ACCOUNT = open(os.path.join(ROOT, '.saasufy-dev-account')).read().strip()
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
        return f'ERR {e.code}: {e.read().decode()[:200]}'

def wipe(model):
    n = 0
    while True:
        rows = call('GET', model, None, 'pageSize=100').get('data', [])
        if not rows: break
        for x in rows:
            call('DELETE', f"{model}/{x['id'] if isinstance(x,dict) else x}"); n += 1
    return n


print('wiping existing dev data...')
for m in ('Document','Availability','TimeSlot','Clinician'):
    print(f'  {m}: removed {wipe(m)}')

print('\nseeding demo clinician...')
clin = call('POST', 'Clinician', dict(
    accountId=ACCOUNT,
    displayName='Dr Sarah Chen',
    professionalTitle='Naturopath, BHSc (Naturopathy)',
    bio=('Sarah is a degree-qualified naturopath with over twelve years of clinical experience, '
         'working primarily with digestive health, fatigue and hormonal concerns. She takes an '
         'evidence-informed approach, combining nutritional medicine and western herbal medicine '
         'with careful attention to the whole picture of a person’s health. Sarah consults '
         'online and works closely with each client to build a realistic, sustainable plan.'),
    topics='naturopathy,nutrition,digestive-health,fatigue',
    country='au', region='nsw', city='Sydney', timezone='Australia/Sydney',
    languages='English,Mandarin',
    consultationMinutes=45, priceAmount=13500, priceCurrency='AUD',
    contactEmail='sarah.chen@example.com',
    listingStatus='listed', emailVerified=True, payoutStatus='none'))
if str(clin).startswith('ERR'): raise SystemExit(f'clinician seed failed: {clin}')
print('  clinician id:', clin)

print('\nseeding documents...')
for t, inst, qual, yr, st, note in [
    ('degree','Southern Cross University','Bachelor of Health Science (Naturopathy)',2011,'approved',''),
    ('registration','Australian Natural Therapists Association','ANTA Member #47182',2012,'approved',''),
    ('certification','Monash University','FODMAP-Trained Practitioner',2019,'pending',''),
]:
    r = call('POST','Document', dict(accountId=ACCOUNT, clinicianId=clin, type=t, institution=inst,
             documentName=qual, yearAwarded=yr, reviewStatus=st, reviewNote=note))
    print(f'  {qual[:44]:46s} {st}' if not str(r).startswith('ERR') else f'  {r}')

print('\nseeding availability...')
TZ = 'Australia/Sydney'
for day, start, end in [(d, 9*60, 17*60) for d in (1,2,3,4)] + [(5, 9*60, 13*60)]:
    call('POST','Availability', dict(accountId=ACCOUNT, clinicianId=clin, kind='weekly',
         startMinuteOfWeek=day*1440+start, endMinuteOfWeek=day*1440+end,
         utcHourKeys=weekly_keys(day, start, end, TZ), active=True))
print('  5 weekly rules (Mon-Thu 9-5, Fri 9-1)')

print('\nverify accountId association on a seeded record:')
rec = call('GET', f'Clinician/{clin}')
print('  Clinician.accountId =', rec.get('accountId'), '(expected', ACCOUNT + ')')
print('\nDEV_ACCOUNT_ID for index.html:', ACCOUNT)
print('CLINICIAN_ID          :', clin)

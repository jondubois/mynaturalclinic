#!/usr/bin/env python3
"""Seed sample practitioner records for development.

Spread across every region and most specialisations so the two-level browse
filter and the free-text search have something to work against. Idempotent by
display name: re-running updates the existing rows rather than duplicating them.

These are fictitious profiles with generated account IDs — they have no matching
Account record, so nobody can log in as them. Real sign-ups create their own
Clinician row through the app.
"""
import json, urllib.request, os, uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()
SVC = 'https://saasufy.com/sid8016/api'

TZ = {'nsw': 'Australia/Sydney', 'vic': 'Australia/Melbourne',
      'qld': 'Australia/Brisbane', 'wa': 'Australia/Perth',
      'sa': 'Australia/Adelaide', 'tas': 'Australia/Hobart',
      'act': 'Australia/Sydney', 'nt': 'Australia/Darwin'}


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
        return f'ERR {e.code}: {e.read().decode()[:180]}'


PRACTITIONERS = [
    ('Dr Amara Okafor', 'Naturopath, BHSc (Naturopathy)', 'Sydney', 'nsw',
     'naturopathy,digestive-health', 12000, 45, 'English,Igbo',
     'Amara has worked in clinical naturopathy for fourteen years, with most of her practice '
     'focused on digestive complaints that have not responded to standard care. She works '
     'methodically through diet, stress and sleep before reaching for supplements, and is '
     'candid when something falls outside her scope and belongs with a GP.'),
    ('James Whitfield', 'Acupuncturist, AACMA Registered', 'Melbourne', 'vic',
     'acupuncture,chronic-pain', 11000, 60, 'English',
     'James treats persistent musculoskeletal pain, particularly lower back and neck problems '
     'that have become chronic after injury. He combines acupuncture with practical movement '
     'advice, and sets clear expectations early: if six sessions have not shifted anything, he '
     'will say so rather than keep booking you in.'),
    ('Dr Priya Nair', 'Clinical Nutritionist, MSc', 'Brisbane', 'qld',
     'nutrition,hormonal-health', 9500, 45, 'English,Hindi,Malayalam',
     'Priya specialises in nutritional support for hormonal conditions including PCOS, thyroid '
     'disorders and perimenopause. Her approach is deliberately unglamorous: she works with '
     'pathology results, builds plans around what someone will actually eat, and reviews them '
     'properly rather than handing over a printout.'),
    ('Hannah Lindqvist', 'Medical Herbalist, BHSc', 'Perth', 'wa',
     'herbal-medicine,immune-support', 13000, 60, 'English,Swedish',
     'Hannah practises western herbal medicine with a focus on recurrent infections and immune '
     'resilience, often in people run down by long working patterns. She dispenses her own '
     'formulations, explains what each herb is doing and why, and checks every prescription '
     'against existing medications before anything is made up.'),
    ('Dr Tomas Ricci', 'Homeopath, DHom', 'Adelaide', 'sa',
     'homeopathy,skin-conditions', 9000, 45, 'English,Italian',
     'Tomas has a long-standing interest in chronic skin presentations, particularly eczema and '
     'adult acne where conventional treatment has stalled or flared on withdrawal. Consultations '
     'are unhurried because case-taking is most of the work, and he is straightforward about '
     'what homeopathy can and cannot offer.'),
    ('Sophie Tran', 'Remedial Massage Therapist, Dip RM', 'Hobart', 'tas',
     'remedial-massage,chronic-pain', 10000, 60, 'English,Vietnamese',
     'Sophie works with desk-bound clients carrying long-term shoulder and hip tension, and with '
     'runners managing recurring niggles. Online consultations cover assessment, self-treatment '
     'technique and a realistic loading plan; she will tell you plainly when what you need is '
     'hands-on work rather than a screen.'),
    ('Dr Wei Zhang', 'Chinese Medicine Practitioner, MHSc', 'Canberra', 'act',
     'chinese-medicine,acupuncture,fatigue', 11500, 45, 'English,Mandarin',
     'Wei practises Chinese herbal medicine and acupuncture, most often for persistent fatigue '
     'and post-viral recovery. He takes a slow, staged approach and is careful about herb and '
     'drug interactions, coordinating with a patient GP wherever someone is on ongoing '
     'medication.'),
    ('Grace Mbeki', 'Kinesiologist, Dip Kin', 'Darwin', 'nt',
     'kinesiology,anxiety', 10500, 60, 'English,Swahili',
     'Grace uses kinesiology alongside breathing and grounding work for people managing anxiety '
     'and stress responses that show up physically. She is explicit that this sits beside, not '
     'instead of, psychological care, and refers on readily where someone would be better served '
     'by a psychologist.'),
    ('Dr Elena Petrova', 'Naturopath, ND', 'Newcastle', 'nsw',
     'naturopathy,sleep-problems,anxiety', 8500, 30, 'English,Russian',
     'Elena focuses on sleep: onset insomnia, early waking and the circadian disruption that '
     'follows shift work. She starts with sleep timing and light exposure because those change '
     'the most for the least cost, and keeps consultations short and frequent rather than long '
     'and occasional.'),
    ('Daniel Ferreira', 'Nutritionist & Herbalist, BHSc', 'Geelong', 'vic',
     'nutrition,herbal-medicine,digestive-health', 10000, 45, 'English,Portuguese',
     'Daniel combines nutritional and herbal medicine for gut conditions including IBS and '
     'reflux, and has a particular interest in reintroduction after restrictive elimination '
     'diets. He works in small, testable steps so it stays clear what actually helped, and '
     'writes everything down so you are not relying on memory.'),
]

# Weekly hours by display name, varied so the browse filters separate the list.
# Minutes from midnight (540 = 9am); day 0 is Sunday.
HOURS = {
    'Dr Amara Okafor':   [((1, 2, 3, 4, 5), 540, 1020)],            # weekdays 9-5
    'James Whitfield':   [((1, 3, 5), 420, 780)],                   # early mornings
    'Dr Priya Nair':     [((2, 4), 780, 1200), ((6,), 540, 720)],   # afternoons + Sat am
    'Hannah Lindqvist':  [((1, 2, 3), 600, 960)],                   # late mornings
    'Dr Tomas Ricci':    [((3, 4, 5), 960, 1320)],                  # evenings
    'Sophie Tran':       [((1, 2, 3, 4), 540, 780)],                # mornings
    'Dr Wei Zhang':      [((2, 4, 6), 480, 1020)],                  # long days, incl. Sat
    'Grace Mbeki':       [((1, 5), 720, 1260)],                     # afternoon into evening
    'Dr Elena Petrova':  [((0, 6), 540, 900)],                      # weekends only
    'Daniel Ferreira':   [((1, 2, 3, 4, 5), 1020, 1290)],           # after-work only
}

existing = {c['displayName']: c for c in call('GET', 'Clinician', None, 'pageSize=200').get('data', [])}
created = updated = 0

for (name, title, city, region, topics, price, minutes, langs, bio) in PRACTITIONERS:
    assert len(bio) >= 200, f'{name}: bio only {len(bio)} chars'
    record = {
        'displayName': name, 'professionalTitle': title, 'bio': bio,
        'topics': topics, 'country': 'au', 'region': region, 'city': city,
        'timezone': TZ[region], 'languages': langs,
        'consultationMinutes': minutes, 'priceAmount': price, 'priceCurrency': 'AUD',
        'contactEmail': name.split()[-1].lower() + '@example.com',
        'listingStatus': 'listed', 'emailVerified': True, 'payoutStatus': 'none',
    }
    if name in existing:
        r = call('PUT', f"Clinician/{existing[name]['id']}", record)
        updated += 1
    else:
        record['accountId'] = str(uuid.uuid4())
        r = call('POST', 'Clinician', record)
        created += 1
    if str(r).startswith('ERR'):
        print(f'  {name}: {r}')

print(f'{created} created, {updated} updated')

# Replaced rather than merged each run, so the rows always match HOURS above.
print('\nseeding weekly availability...')
clinicians = {c['displayName']: c for c in call('GET', 'Clinician', None, 'pageSize=200').get('data', [])}
rows = 0
for name, blocks in HOURS.items():
    clin = clinicians.get(name)
    if not clin:
        print(f'  {name}: no clinician record, skipped')
        continue
    for old_row in call('GET', 'Availability', None,
                        f"view=clinicianView&viewParams[clinicianId]={clin['id']}"
                        '&pageSize=100').get('data', []):
        call('DELETE', f"Availability/{old_row['id'] if isinstance(old_row, dict) else old_row}")
    for days, start, end in blocks:
        for day in days:
            r = call('POST', 'Availability', {
                'accountId': clin['accountId'], 'clinicianId': clin['id'],
                'kind': 'weekly', 'dayOfWeek': day,
                'startMinute': start, 'endMinute': end, 'active': True})
            if str(r).startswith('ERR'): print(f'  {name} day {day}: {r}')
            else: rows += 1
print(f'{rows} weekly availability rows')
print('\nThe aggregations fill in within ~60s, or rebuild them to apply immediately.')

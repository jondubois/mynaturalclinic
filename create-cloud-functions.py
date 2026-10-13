#!/usr/bin/env python3
"""Create/update the Saasufy cloud functions. Idempotent: re-running updates the code.

A cloud function's `name` is immutable and its record id is derived from it, so this
script matches on name and PUTs the code rather than POSTing a second time.

Saasufy reads its cloud functions only when the service starts, so a change here
needs a deploy to take effect:

    curl -H "Authorization:Bearer $(cat .saasufy-api-key)" \\
      -XPOST 'https://saasufy.com/api/service/start'
"""
import json, os, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()
BASE = 'https://saasufy.com/api'


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'{BASE}/{path}', data=data, method=method)
    req.add_header('Authorization', f'Bearer {KEY}')
    if data:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode().strip()
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return raw
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{method} {path} -> {e.code}: {e.read().decode()[:400]}')


def existing():
    out, off = {}, 0
    while True:
        r = call('GET', 'CloudFunction?' + urllib.parse.urlencode(
            {'view': 'accountAlphabeticalView', 'offset': off, 'pageSize': 100}))
        d = r.get('data', [])
        for fid in d:
            f = call('GET', f'CloudFunction/{fid}') if isinstance(fid, str) else fid
            out[f['name']] = f['id']
        if r.get('isLastPage') or not d:
            break
        off += len(d)
    return out


def function(name, code, **opts):
    have = existing()
    if name in have:
        call('PUT', f'CloudFunction/{have[name]}', {'code': code, 'isDisabled': False, **opts})
        print(f'  = CloudFunction {name}')
    else:
        call('POST', 'CloudFunction', {'name': name, 'code': code, **opts})
        print(f'  + CloudFunction {name}')


# ---------------------------------------------------------------------------
# appointmentInvite
#
# Called by the booking success page once the Appointment row exists. It is what will
# eventually send the clinician their invitation and the patient their receipt; for now
# it only logs what it would send, so the shape of the call and its access control can
# be settled before an email provider is wired in.
#
# The hook is unauthenticated and `crud` runs as the account admin, so the caller's
# JWT is verified first and every read is scoped to the appointment that token's
# account actually owns.
# ---------------------------------------------------------------------------
APPOINTMENT_INVITE = r'''
let signedToken = (request.headers.authorization || '').replace(/^Bearer /i, '');
let token;
try {
  token = await auth.verifyToken(signedToken);
} catch (error) {
  response.status(401).json({ error: 'Unauthorized' });
  return;
}

let { appointmentId } = params;
if (!appointmentId) {
  response.status(400).json({ error: 'appointmentId is required' });
  return;
}

let appointment = await crud.read({ type: 'Appointment', id: appointmentId });
if (!appointment) {
  response.status(404).json({ error: 'No such appointment' });
  return;
}

// The row names both parties; only they may trigger its email.
let owners = String(appointment.accountId || '').split(',');
if (!owners.includes(token.accountId)) {
  response.status(403).json({ error: 'Not your appointment' });
  return;
}

let [clinician, patientAccount, clinicianAccount] = await Promise.all([
  crud.read({ type: 'Clinician', id: appointment.clinicianId }),
  crud.read({ type: 'Account', id: appointment.patientAccountId }),
  crud.read({ type: 'Account', id: appointment.clinicianAccountId })
]);

// Stands in for the Email queue row and the provider call. The patient's address
// comes from the Keycloak-verified Account, never from the booking form.
console.log('would send appointment invite', JSON.stringify({
  appointmentId,
  template: 'clinician-invite',
  to: clinicianAccount && clinicianAccount.email,
  replyToPatient: patientAccount && patientAccount.email,
  clinician: clinician && clinician.displayName,
  patientName: appointment.patientName,
  startAt: appointment.startAt,
  endAt: appointment.endAt,
  patientTimezone: appointment.patientTimezone,
  clinicianTimezone: clinician && clinician.timezone,
  intakeReason: appointment.intakeReason,
  amount: appointment.amount,
  clinicianPayout: appointment.clinicianPayout,
  currency: appointment.currency
}));

return { queued: true, appointmentId };
'''.strip()

print('== Cloud functions ==')
function('appointmentInvite', APPOINTMENT_INVITE, timeout=10000)

print('\nDeploy for the service to pick this up:')
print("  curl -H \"Authorization:Bearer $(cat .saasufy-api-key)\" -XPOST "
      "'https://saasufy.com/api/service/start'")

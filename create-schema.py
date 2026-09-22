#!/usr/bin/env python3
"""Create the MyNaturalClinic schema in Saasufy. Idempotent: re-running updates in place."""
import json, urllib.request, urllib.parse, sys, os

KEY = open('/home/jon/Work/mynaturalclinic/.saasufy-api-key').read().strip()
BASE = 'https://saasufy.com/api'

def call(method, path, body=None):
    url = f'{BASE}/{path}'
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {KEY}')
    if data: req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode().strip()
            if not raw: return {}
            try: return json.loads(raw)
            except json.JSONDecodeError: return raw
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()
        raise RuntimeError(f'{method} {path} -> {e.code}: {body_txt}') from None

def q(path, params):
    return f'{path}?{urllib.parse.urlencode(params)}'

# ---- caches of existing objects -------------------------------------------
def existing_models():
    out, off = {}, 0
    while True:
        r = call('GET', q('Model', {'view': 'accountAlphabeticalView',
                                    'offset': off, 'pageSize': 100}))
        d = r.get('data', [])
        for mid in d:
            m = call('GET', f'Model/{mid}') if isinstance(mid, str) else mid
            out[m['name']] = m['id']
        if r.get('isLastPage') or not d: break
        off += len(d)
    return out

def existing_children(kind, model_id):
    out, off = {}, 0
    while True:
        r = call('GET', q(kind, {'view': 'accountModelAlphabeticalView',
                                 'viewParams[modelId]': model_id,
                                 'offset': off, 'pageSize': 100}))
        d = r.get('data', [])
        for cid in d:
            c = call('GET', f'{kind}/{cid}') if isinstance(cid, str) else cid
            out[c['name']] = c['id']
        if r.get('isLastPage') or not d: break
        off += len(d)
    return out

MODELS = existing_models()

def model(name, position, **access):
    if name in MODELS:
        mid = MODELS[name]
        if access: call('PUT', f'Model/{mid}', access)
        print(f'  = Model {name}')
    else:
        body = {'name': name, 'position': position}
        body.update(access)
        r = call('POST', 'Model', body)
        mid = r['id'] if isinstance(r, dict) else r
        MODELS[name] = mid
        print(f'  + Model {name}')
    return mid

def fields(model_id, model_name, specs):
    have = existing_children('ModelField', model_id)
    for i, spec in enumerate(specs, start=1):
        spec = dict(spec); nm = spec.pop('name'); ty = spec.pop('type')
        if nm in have:
            call('PUT', f'ModelField/{have[nm]}', spec)
        else:
            body = {'modelId': model_id, 'name': nm, 'type': ty, 'position': i}
            body.update(spec)
            call('POST', 'ModelField', body)
    print(f'    fields: {len(specs)}')

def indexes(model_id, specs):
    have = existing_children('ModelIndex', model_id)
    for spec in specs:
        spec = dict(spec); nm = spec.pop('name')
        if nm in have:
            call('PUT', f'ModelIndex/{have[nm]}', spec)
        else:
            call('POST', 'ModelIndex', {'modelId': model_id, 'name': nm, **spec})
    print(f'    indexes: {len(specs)}')

def views(model_id, specs):
    have = existing_children('ModelView', model_id)
    for spec in specs:
        spec = dict(spec); nm = spec.pop('name')
        if nm in have:
            call('PUT', f'ModelView/{have[nm]}', spec)
        else:
            call('POST', 'ModelView', {'modelId': model_id, 'name': nm, **spec})
    print(f'    views: {len(specs)}')

def field_access(model_id, mapping):
    have = existing_children('ModelField', model_id)
    for nm, acc in mapping.items():
        if nm in have:
            call('PUT', f'ModelField/{have[nm]}', acc)
    print(f'    field-access: {len(mapping)}')

S, N, B = 'string', 'number', 'boolean'
OWNER = {'accessTokenAuthField': 'accountId', 'accessModelAuthField': 'accountId'}

print('== Topic ==')
m = model('Topic', 1, accessCreate='block', accessRead='allow',
          accessUpdate='block', accessDelete='block')
fields(m, 'Topic', [
    {'name': 'name', 'type': S, 'required': True, 'max': 100},
    {'name': 'slug', 'type': S, 'required': True, 'max': 100, 'lowercase': True},
    {'name': 'kind', 'type': S, 'enum': 'specialisation,ailment', 'required': True},
    {'name': 'synonyms', 'type': S, 'multi': True, 'maxCardinality': 20},
    {'name': 'active', 'type': B, 'defaultValue': 'true'},
])
indexes(m, [{'name': 'slug', 'fields': 'slug'},
            {'name': 'kindName', 'fields': 'kind,name'}])
views(m, [
    {'name': 'alphabeticalView', 'paramFields': '', 'primaryFields': '',
     'transformOrderByField': 'name'},
    {'name': 'kindView', 'paramFields': 'kind', 'primaryFields': 'kind',
     'transformIndex': 'kind', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.kind',
     'transformOrderByField': 'name'},
])

print('== Clinician ==')
m = model('Clinician', 2, accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='block', **OWNER)
fields(m, 'Clinician', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'displayName', 'type': S, 'max': 120},
    {'name': 'searchName', 'type': S, 'max': 120, 'lowercase': True},
    {'name': 'professionalTitle', 'type': S, 'max': 120},
    {'name': 'bio', 'type': S, 'max': 5000},
    {'name': 'photo', 'type': S, 'blob': True},
    {'name': 'topics', 'type': S, 'multi': True, 'maxCardinality': 8},
    {'name': 'searchTags', 'type': S, 'max': 2000, 'lowercase': True},
    {'name': 'searchKeys', 'type': S, 'multi': True, 'maxCardinality': 17},
    {'name': 'country', 'type': S, 'max': 2, 'lowercase': True},
    {'name': 'region', 'type': S, 'max': 60, 'lowercase': True},
    {'name': 'city', 'type': S, 'max': 120},
    {'name': 'timezone', 'type': S, 'max': 60},
    {'name': 'languages', 'type': S, 'multi': True, 'maxCardinality': 10},
    {'name': 'consultationMinutes', 'type': N, 'integer': True, 'min': 15, 'max': 180},
    {'name': 'priceAmount', 'type': N, 'integer': True, 'min': 0},
    {'name': 'priceCurrency', 'type': S, 'max': 3, 'uppercase': True, 'defaultValue': 'AUD'},
    {'name': 'nextAvailableAt', 'type': N, 'defaultValue': '0'},
    {'name': 'listingStatus', 'type': S, 'enum': 'draft,pending_review,listed,suspended',
     'defaultValue': 'draft'},
    {'name': 'contactEmail', 'type': S, 'email': True},
    {'name': 'pinRecipientToken', 'type': S, 'max': 200},
    {'name': 'payoutAccountLast4', 'type': S, 'max': 4},
    {'name': 'payoutStatus', 'type': S, 'enum': 'none,pending,active,restricted',
     'defaultValue': 'none'},
    {'name': 'emailVerified', 'type': B, 'defaultValue': 'false'},
    {'name': 'ratingAverage', 'type': N, 'defaultValue': '0'},
    {'name': 'ratingCount', 'type': N, 'integer': True, 'defaultValue': '0'},
])
indexes(m, [
    {'name': 'searchKeys', 'fields': 'searchKeys'},
    {'name': 'nextAvailableAt', 'fields': 'nextAvailableAt'},
    {'name': 'accountId', 'fields': 'accountId'},
])
views(m, [
    {'name': 'searchView', 'paramFields': 'searchKey,query,sortBy',
     'primaryFields': 'searchKey',
     'transformIndex': 'searchKeys', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.searchKey',
     'transformFilterType': 'advanced', 'transformFilterQuery': '$paramFields.query',
     'transformOrderByField': '$paramFields.sortBy', 'maxOffset': 500,
     'affectingFields': 'nextAvailableAt,priceAmount,ratingAverage,searchTags'},
    {'name': 'browseView', 'paramFields': 'query', 'primaryFields': '',
     'transformFilterType': 'advanced', 'transformFilterQuery': '$paramFields.query',
     'transformOrderByField': 'nextAvailableAt', 'maxOffset': 500},
    {'name': 'accountView', 'paramFields': 'accountId', 'primaryFields': 'accountId',
     'transformIndex': 'accountId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.accountId'},
])
field_access(m, {
    'contactEmail':       {'accessRead': 'restrict', 'accessUpdate': 'restrict'},
    'pinRecipientToken':  {'accessRead': 'restrict'},
    'payoutAccountLast4': {'accessRead': 'restrict'},
    'payoutStatus':       {'accessRead': 'restrict'},
})

print('== Credential ==')
m = model('Credential', 3, accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='restrict', **OWNER)
fields(m, 'Credential', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'clinicianId', 'type': S, 'required': True},
    {'name': 'type', 'type': S, 'enum': 'degree,diploma,certification,registration,licence'},
    {'name': 'institution', 'type': S, 'max': 200},
    {'name': 'qualificationName', 'type': S, 'max': 200},
    {'name': 'yearAwarded', 'type': N, 'integer': True, 'min': 1900, 'max': 2100},
    {'name': 'registrationNumber', 'type': S, 'max': 100},
    {'name': 'document', 'type': S, 'blob': True},
    {'name': 'reviewStatus', 'type': S, 'enum': 'pending,approved,rejected',
     'defaultValue': 'pending'},
    {'name': 'reviewNote', 'type': S, 'max': 2000},
    {'name': 'reviewedAt', 'type': N},
])
indexes(m, [{'name': 'clinicianId', 'fields': 'clinicianId'},
            {'name': 'accountId', 'fields': 'accountId'}])
views(m, [
    {'name': 'clinicianView', 'paramFields': 'clinicianId', 'primaryFields': 'clinicianId',
     'transformIndex': 'clinicianId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.clinicianId',
     'transformOrderByField': 'yearAwarded', 'transformOrderByDesc': True},
    {'name': 'accountView', 'paramFields': 'accountId', 'primaryFields': 'accountId',
     'transformIndex': 'accountId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.accountId',
     'transformOrderByField': 'createdAt', 'transformOrderByDesc': True,
     'affectingFields': 'reviewStatus,reviewNote'},
    {'name': 'reviewQueueView', 'paramFields': 'reviewStatus', 'primaryFields': 'reviewStatus',
     'transformIndex': 'reviewStatus', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.reviewStatus',
     'transformOrderByField': 'createdAt'},
])
field_access(m, {
    'document':           {'accessRead': 'restrict'},
    'registrationNumber': {'accessRead': 'restrict'},
    'reviewNote':         {'accessRead': 'restrict'},
})

print('== Availability ==')
m = model('Availability', 4, accessCreate='restrict', accessRead='restrict',
          accessUpdate='restrict', accessDelete='restrict', **OWNER)
fields(m, 'Availability', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'clinicianId', 'type': S, 'required': True},
    {'name': 'kind', 'type': S, 'enum': 'weekly,dayOff,extra', 'required': True},
    {'name': 'dayOfWeek', 'type': N, 'integer': True, 'min': 0, 'max': 6},
    {'name': 'date', 'type': N},
    {'name': 'startMinute', 'type': N, 'integer': True, 'min': 0, 'max': 1440},
    {'name': 'endMinute', 'type': N, 'integer': True, 'min': 0, 'max': 1440},
    {'name': 'effectiveFrom', 'type': N},
    {'name': 'effectiveUntil', 'type': N},
    {'name': 'active', 'type': B, 'defaultValue': 'true'},
])
indexes(m, [{'name': 'clinicianId', 'fields': 'clinicianId'},
            {'name': 'accountId', 'fields': 'accountId'}])
views(m, [
    {'name': 'clinicianView', 'paramFields': 'clinicianId', 'primaryFields': 'clinicianId',
     'transformIndex': 'clinicianId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.clinicianId',
     'transformOrderByField': 'dayOfWeek'},
    {'name': 'accountView', 'paramFields': 'accountId', 'primaryFields': 'accountId',
     'transformIndex': 'accountId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.accountId',
     'transformOrderByField': 'dayOfWeek', 'affectingFields': 'kind,startMinute,endMinute'},
])

print('== TimeSlot ==')
m = model('TimeSlot', 5, accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='restrict', **OWNER)
fields(m, 'TimeSlot', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'clinicianId', 'type': S, 'required': True},
    {'name': 'startAt', 'type': N, 'required': True},
    {'name': 'endAt', 'type': N, 'required': True},
    {'name': 'status', 'type': S, 'enum': 'open,held,booked,expired,cancelled',
     'defaultValue': 'open'},
    {'name': 'holdExpiresAt', 'type': N, 'defaultValue': '0'},
    {'name': 'appointmentId', 'type': S},
    {'name': 'priceAmount', 'type': N, 'integer': True, 'min': 0},
    {'name': 'consultationMinutes', 'type': N, 'integer': True},
])
indexes(m, [
    {'name': 'clinicianIdStartAt', 'fields': 'clinicianId,startAt'},
    {'name': 'appointmentId', 'fields': 'appointmentId'},
])
views(m, [
    {'name': 'clinicianRangeView', 'paramFields': 'clinicianId,fromAt,toAt,query',
     'primaryFields': 'clinicianId',
     'transformIndex': 'clinicianIdStartAt', 'transformIndexOperation': 'between',
     'transformIndexOperationInputA': '$paramFields.clinicianId,$paramFields.fromAt',
     'transformIndexOperationInputB': '$paramFields.clinicianId,$paramFields.toAt',
     'transformFilterType': 'advanced', 'transformFilterQuery': '$paramFields.query',
     'transformOrderByField': 'startAt', 'affectingFields': 'status'},
    {'name': 'appointmentView', 'paramFields': 'appointmentId', 'primaryFields': 'appointmentId',
     'transformIndex': 'appointmentId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.appointmentId'},
])

print('== Appointment ==')
m = model('Appointment', 6, accessCreate='restrict', accessRead='restrict',
          accessUpdate='restrict', accessDelete='block',
          accessEnableMultipleOwners=True, **OWNER)
fields(m, 'Appointment', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'patientAccountId', 'type': S, 'required': True},
    {'name': 'clinicianAccountId', 'type': S, 'required': True},
    {'name': 'clinicianId', 'type': S, 'required': True},
    {'name': 'timeSlotId', 'type': S, 'required': True},
    {'name': 'startAt', 'type': N, 'required': True},
    {'name': 'endAt', 'type': N, 'required': True},
    {'name': 'status', 'type': S,
     'enum': ('pending_payment,pending_clinician,confirmed,declined,expired,'
              'cancelled_patient,cancelled_clinician,completed,no_show_patient,'
              'no_show_clinician,disputed'),
     'defaultValue': 'pending_payment'},
    {'name': 'intakeReason', 'type': S, 'max': 500},
    {'name': 'intakeNotes', 'type': S, 'max': 4000},
    {'name': 'amount', 'type': N, 'integer': True, 'min': 0},
    {'name': 'platformFee', 'type': N, 'integer': True, 'min': 0},
    {'name': 'clinicianPayout', 'type': N, 'integer': True, 'min': 0},
    {'name': 'currency', 'type': S, 'max': 3, 'uppercase': True, 'defaultValue': 'AUD'},
    {'name': 'paymentStatus', 'type': S,
     'enum': 'none,paid,refunded,partially_refunded,failed', 'defaultValue': 'none'},
    {'name': 'pinChargeToken', 'type': S, 'max': 200},
    {'name': 'pinRefundToken', 'type': S, 'max': 200},
    {'name': 'pinTransferToken', 'type': S, 'max': 200},
    {'name': 'inviteToken', 'type': S, 'max': 200},
    {'name': 'inviteRespondedAt', 'type': N, 'defaultValue': '0'},
    {'name': 'inviteExpiresAt', 'type': N, 'defaultValue': '0'},
    {'name': 'meetingTokenPatient', 'type': S, 'max': 200},
    {'name': 'meetingTokenClinician', 'type': S, 'max': 200},
    {'name': 'zoomMeetingId', 'type': S, 'max': 100},
    {'name': 'zoomJoinUrl', 'type': S, 'max': 500},
    {'name': 'zoomStartUrl', 'type': S, 'max': 1000},
    {'name': 'patientAttendedAt', 'type': N, 'defaultValue': '0'},
    {'name': 'clinicianAttendedAt', 'type': N, 'defaultValue': '0'},
    {'name': 'attendanceSource', 'type': S, 'enum': 'link,zoom_report,manual'},
    {'name': 'payoutStatus', 'type': S, 'enum': 'pending,scheduled,paid,withheld',
     'defaultValue': 'pending'},
])
indexes(m, [
    {'name': 'patientAccountIdStartAt', 'fields': 'patientAccountId,startAt'},
    {'name': 'clinicianAccountIdStartAt', 'fields': 'clinicianAccountId,startAt'},
    {'name': 'statusStartAt', 'fields': 'status,startAt'},
])
views(m, [
    {'name': 'patientView', 'paramFields': 'patientAccountId,fromAt,toAt,query',
     'primaryFields': 'patientAccountId',
     'transformIndex': 'patientAccountIdStartAt', 'transformIndexOperation': 'between',
     'transformIndexOperationInputA': '$paramFields.patientAccountId,$paramFields.fromAt',
     'transformIndexOperationInputB': '$paramFields.patientAccountId,$paramFields.toAt',
     'transformFilterType': 'advanced', 'transformFilterQuery': '$paramFields.query',
     'transformOrderByField': 'startAt', 'affectingFields': 'status,paymentStatus'},
    {'name': 'clinicianView', 'paramFields': 'clinicianAccountId,fromAt,toAt,query',
     'primaryFields': 'clinicianAccountId',
     'transformIndex': 'clinicianAccountIdStartAt', 'transformIndexOperation': 'between',
     'transformIndexOperationInputA': '$paramFields.clinicianAccountId,$paramFields.fromAt',
     'transformIndexOperationInputB': '$paramFields.clinicianAccountId,$paramFields.toAt',
     'transformFilterType': 'advanced', 'transformFilterQuery': '$paramFields.query',
     'transformOrderByField': 'startAt', 'affectingFields': 'status,payoutStatus'},
    {'name': 'statusDueView', 'paramFields': 'status,fromAt,toAt,query',
     'primaryFields': 'status',
     'transformIndex': 'statusStartAt', 'transformIndexOperation': 'between',
     'transformIndexOperationInputA': '$paramFields.status,$paramFields.fromAt',
     'transformIndexOperationInputB': '$paramFields.status,$paramFields.toAt',
     'transformFilterType': 'advanced', 'transformFilterQuery': '$paramFields.query',
     'transformOrderByField': 'startAt'},
])
field_access(m, {
    'pinChargeToken':        {'accessRead': 'block'},
    'pinRefundToken':        {'accessRead': 'block'},
    'pinTransferToken':      {'accessRead': 'block'},
    'inviteToken':           {'accessRead': 'block'},
    'meetingTokenPatient':   {'accessRead': 'block'},
    'meetingTokenClinician': {'accessRead': 'block'},
    'zoomStartUrl':          {'accessRead': 'block'},
})

print('== Attendance ==')
m = model('Attendance', 7, accessCreate='block', accessRead='restrict',
          accessUpdate='block', accessDelete='block',
          accessEnableMultipleOwners=True, **OWNER)
fields(m, 'Attendance', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'appointmentId', 'type': S, 'required': True},
    {'name': 'party', 'type': S, 'enum': 'patient,clinician', 'required': True},
    {'name': 'source', 'type': S, 'enum': 'link,zoom_report', 'required': True},
    {'name': 'occurredAt', 'type': N, 'required': True},
    {'name': 'ipHash', 'type': S, 'max': 64},
    {'name': 'userAgent', 'type': S, 'max': 500},
    {'name': 'zoomParticipantId', 'type': S, 'max': 100},
    {'name': 'durationSeconds', 'type': N, 'integer': True, 'defaultValue': '0'},
])
indexes(m, [{'name': 'appointmentId', 'fields': 'appointmentId'}])
views(m, [
    {'name': 'appointmentView', 'paramFields': 'appointmentId', 'primaryFields': 'appointmentId',
     'transformIndex': 'appointmentId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.appointmentId',
     'transformOrderByField': 'occurredAt'},
])

print('== Review ==')
m = model('Review', 8, accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='restrict', **OWNER)
fields(m, 'Review', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'clinicianId', 'type': S, 'required': True},
    {'name': 'appointmentId', 'type': S, 'required': True},
    {'name': 'rating', 'type': N, 'integer': True, 'min': 1, 'max': 5, 'required': True},
    {'name': 'comment', 'type': S, 'max': 2000},
    {'name': 'published', 'type': B, 'defaultValue': 'true'},
])
indexes(m, [{'name': 'clinicianId', 'fields': 'clinicianId'},
            {'name': 'appointmentId', 'fields': 'appointmentId'}])
views(m, [
    {'name': 'clinicianView', 'paramFields': 'clinicianId', 'primaryFields': 'clinicianId',
     'transformIndex': 'clinicianId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.clinicianId',
     'transformOrderByField': 'createdAt', 'transformOrderByDesc': True},
])

print('== Email ==')
m = model('Email', 9, accessCreate='block', accessRead='block',
          accessUpdate='block', accessDelete='block')
fields(m, 'Email', [
    {'name': 'toAccountId', 'type': S},
    {'name': 'toEmail', 'type': S, 'required': True},
    {'name': 'template', 'type': S, 'required': True, 'max': 100},
    {'name': 'payload', 'type': S, 'max': 8000},
    {'name': 'status', 'type': S, 'enum': 'queued,sent,failed', 'defaultValue': 'queued'},
    {'name': 'sentAt', 'type': N, 'defaultValue': '0'},
    {'name': 'error', 'type': S, 'max': 2000},
    {'name': 'dedupeKey', 'type': S, 'max': 200},
])
indexes(m, [{'name': 'statusCreatedAt', 'fields': 'status,createdAt'},
            {'name': 'dedupeKey', 'fields': 'dedupeKey'}])
views(m, [
    {'name': 'queueView', 'paramFields': 'status', 'primaryFields': 'status',
     'transformIndex': 'status', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.status',
     'transformOrderByField': 'createdAt'},
    {'name': 'dedupeView', 'paramFields': 'dedupeKey', 'primaryFields': 'dedupeKey',
     'transformIndex': 'dedupeKey', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.dedupeKey'},
])

print('\nAll models created/updated.')

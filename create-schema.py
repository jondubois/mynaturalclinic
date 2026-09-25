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

def _page(kind, params):
    out, off = [], 0
    while True:
        r = call('GET', q(kind, {**params, 'offset': off, 'pageSize': 100}))
        d = r.get('data', [])
        out += [call('GET', f'{kind}/{i}') if isinstance(i, str) else i for i in d]
        if r.get('isLastPage') or not d: break
        off += len(d)
    return out

def aggregation(name, target_model_id, source_model_id, **opts):
    have = {a['aggregationName']: a['id'] for a in _page(
        'Aggregation', {'view': 'accountModelAlphabeticalView',
                        'viewParams[modelId]': target_model_id})}
    if name in have:
        aid = have[name]
        # aggregationName, modelId and sourceModelId are frozen after creation.
        call('PUT', f'Aggregation/{aid}', opts)
        print(f'  = Aggregation {name}')
    else:
        r = call('POST', 'Aggregation', {'aggregationName': name,
                                         'modelId': target_model_id,
                                         'sourceModelId': source_model_id, **opts})
        aid = r['id'] if isinstance(r, dict) else r
        print(f'  + Aggregation {name}')
    return aid

def agg_rules(agg_id, kind, key_fields, specs):
    have = {tuple(r.get(k) for k in key_fields): r['id'] for r in _page(
        kind, {'view': 'accountAggregationView', 'viewParams[aggregationId]': agg_id})}
    for spec in specs:
        key = tuple(spec.get(k) for k in key_fields)
        rest = {k: v for k, v in spec.items() if k not in key_fields}
        if key in have:
            if rest: call('PUT', f'{kind}/{have[key]}', rest)
        else:
            call('POST', kind, {'aggregationId': agg_id, **spec})
    print(f'    {kind}: {len(specs)}')

def rebuild(agg_id):
    import time
    call('PUT', f'Aggregation/{agg_id}', {'rebuildRequestedAt': int(time.time() * 1000)})

S, N, B = 'string', 'number', 'boolean'
ADMIN_GROUP_ID = '620af869-cb09-4a20-bcf4-ed10510b3367'   # the 'admin' Group record; see README
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

print('== Account (Saasufy auth table) ==')
# The Account table is created and populated by Saasufy's auth layer on every
# successful login — clients can never create records in it. Declaring the Model,
# its fields and its indexes only *exposes* the existing table to this service and
# to frontend components. Saasufy applies safer defaults because of the name:
# accessModelAuthField="id" (there is no accountId field — ownership is the record's
# own id) and accessRead="restrict". Do not override those, and do not put
# constraints on the standard fields: the service validates them already and a
# stricter constraint (e.g. required on email) can break logins.
m = model('Account', -1)
fields(m, 'Account', [
    {'name': 'id', 'type': S},
    {'name': 'username', 'type': S},
    {'name': 'email', 'type': S},
    {'name': 'authSource', 'type': S},
    {'name': 'lastWalletBalance', 'type': N},
    {'name': 'lastIpAddress', 'type': S},
    {'name': 'isDeactivated', 'type': B},
    {'name': 'createdAt', 'type': N},
    {'name': 'updatedAt', 'type': N},
])
indexes(m, [{'name': 'username', 'fields': 'username'},
            {'name': 'email', 'fields': 'email'}])

print('== SearchCategory ==')
# Drives the /browse dropdown. Each row pairs the label a patient sees with the
# phase-2 filter query it applies to searchView, so adding a category is a data
# change rather than a code change. Seeded by seed-search-categories.py.
m = model('SearchCategory', 10, accessCreate='block', accessRead='allow',
          accessUpdate='block', accessDelete='block')
fields(m, 'SearchCategory', [
    {'name': 'label', 'type': S, 'max': 100},
    {'name': 'query', 'type': S, 'max': 300},
    {'name': 'kind', 'type': S, 'enum': 'region,specialisation,ailment'},
    {'name': 'position', 'type': N, 'integer': True},
    # 1 = where the practitioner is, 2 = what they treat. Each level drives its
    # own select; the two fragments are combined with ~AND~ in the frontend.
    {'name': 'level', 'type': N, 'integer': True},
])
views(m, [
    {'name': 'orderedView', 'paramFields': '', 'primaryFields': '',
     'transformOrderByField': 'position'},
    {'name': 'levelView', 'paramFields': 'level', 'primaryFields': 'level',
     'transformIndex': 'level', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.level',
     'transformOrderByField': 'position'},
])

print('== Clinician ==')
# An action-specific auth pair is an ALTERNATIVE to the general pair, not a
# replacement: for a given action access is granted if either matches. So read and
# update succeed when the token's accountId matches the record owner OR the token's
# groupMemberships contains the record's groupId. Practitioners keep editing their
# own profile; admin-group members can edit any profile and can see the fields that
# are field-level `restrict` (contactEmail, payout details), because field-level
# restrict reuses the same check.
m = model('Clinician', 2, accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='block',
          accessReadTokenAuthField='groupMemberships', accessReadModelAuthField='groupId',
          accessUpdateTokenAuthField='groupMemberships', accessUpdateModelAuthField='groupId',
          **OWNER)
fields(m, 'Clinician', [
    {'name': 'accountId', 'type': S, 'required': True},
    {'name': 'displayName', 'type': S, 'max': 120},
    {'name': 'professionalTitle', 'type': S, 'max': 120},
    {'name': 'bio', 'type': S, 'max': 5000},
    # max on a string field is a maximum length, so this caps the stored base64
    # data URI at ~150 KB of image. Saasufy rejects anything larger on write.
    {'name': 'photo', 'type': S, 'blob': True, 'max': 200000},
    {'name': 'topics', 'type': S, 'multi': True, 'maxCardinality': 8},
    {'name': 'country', 'type': S, 'max': 2, 'lowercase': True},
    {'name': 'region', 'type': S, 'max': 60, 'lowercase': True},
    {'name': 'city', 'type': S, 'max': 120},
    {'name': 'timezone', 'type': S, 'max': 60},
    {'name': 'languages', 'type': S, 'multi': True, 'maxCardinality': 10},
    {'name': 'consultationMinutes', 'type': N, 'integer': True, 'min': 15, 'max': 180},
    {'name': 'priceAmount', 'type': N, 'integer': True, 'min': 0},
    {'name': 'priceCurrency', 'type': S, 'max': 3, 'uppercase': True, 'defaultValue': 'AUD'},
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
    {'name': 'availableDays', 'type': S, 'max': 200},
    {'name': 'morningDays', 'type': S, 'max': 200},
    {'name': 'afternoonDays', 'type': S, 'max': 200},
    {'name': 'eveningDays', 'type': S, 'max': 200},
    {'name': 'earliestStartMinute', 'type': N, 'integer': True},
    {'name': 'latestEndMinute', 'type': N, 'integer': True},
    {'name': 'availabilityCount', 'type': N, 'integer': True},
    {'name': 'groupId', 'type': S, 'defaultValue': ADMIN_GROUP_ID,
     'accessCreate': 'block', 'accessUpdate': 'block'},
])
indexes(m, [
    {'name': 'listingStatus', 'fields': 'listingStatus'},
    {'name': 'accountId', 'fields': 'accountId'},
])
# Search is keyed on listingStatus itself. Flipping that one field puts a
# practitioner into or out of the view immediately, with nothing derived to keep
# in sync. Region/topic/price filtering happens in the second-phase query over
# ordinary fields.
views(m, [
    {'name': 'searchView', 'paramFields': 'listingStatus,query,sortBy',
     'primaryFields': 'listingStatus',
     'transformIndex': 'listingStatus', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.listingStatus',
     'transformFilterType': 'advanced', 'transformFilterQuery': '$paramFields.query',
     'transformOrderByField': '$paramFields.sortBy', 'maxOffset': 500,
     'affectingFields': 'displayName,region,country,topics,priceAmount,listingStatus,'
                        'availableDays,morningDays,afternoonDays,eveningDays,'
                        'earliestStartMinute,latestEndMinute'},
    {'name': 'accountView', 'paramFields': 'accountId', 'primaryFields': 'accountId',
     'transformIndex': 'accountId', 'transformIndexOperation': 'equals',
     'transformIndexOperationInputA': '$paramFields.accountId'},
])
field_access(m, {
    'contactEmail':       {'accessRead': 'restrict', 'accessUpdate': 'restrict'},
    'pinRecipientToken':  {'accessRead': 'restrict'},
    'payoutAccountLast4': {'accessRead': 'restrict'},
    'payoutStatus':       {'accessRead': 'restrict'},
    **{f: {'accessCreate': 'block', 'accessUpdate': 'block'} for f in (
        'availableDays', 'morningDays', 'afternoonDays', 'eveningDays',
        'earliestStartMinute', 'latestEndMinute', 'availabilityCount')},
})

print('== Group / GroupMembership ==')
# Built-in models. Their schema is auto-extended on deploy; the access settings
# below are the ones the control panel applies. Only a group's owner may add
# members to it (enforced by the service, not by these flags).
m = model('Group', -2, accessTokenAuthField='accountId', accessModelAuthField='accountId',
          accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='restrict')
fields(m, 'Group', [
    {'name': 'name', 'type': S},
    {'name': 'accountId', 'type': S},
    {'name': 'isDeactivated', 'type': B},
])
m = model('GroupMembership', -3, accessTokenAuthField='groupOwnerships',
          accessModelAuthField='groupId', accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='restrict')
fields(m, 'GroupMembership', [
    {'name': 'groupId', 'type': S},
    {'name': 'accountId', 'type': S},
])

print('== Credential ==')
# Read and update of the sensitive parts are gated on membership of the admin
# group; create and delete stay with the owning practitioner. groupId carries the
# admin group id via defaultValue and is create/update-blocked for clients, so a
# practitioner cannot repoint it at a group they own and approve themselves.
m = model('Credential', 3, accessCreate='restrict', accessRead='allow',
          accessUpdate='restrict', accessDelete='restrict',
          accessReadTokenAuthField='groupMemberships', accessReadModelAuthField='groupId',
          accessUpdateTokenAuthField='groupMemberships', accessUpdateModelAuthField='groupId',
          accessCreateTokenAuthField='accountId', accessCreateModelAuthField='accountId',
          accessDeleteTokenAuthField='accountId', accessDeleteModelAuthField='accountId',
          **OWNER)
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
    {'name': 'groupId', 'type': S, 'defaultValue': ADMIN_GROUP_ID,
     'accessCreate': 'block', 'accessUpdate': 'block'},
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
    # Documents are world-readable by design: the app only surfaces a link once a
    # credential is approved, but the URL is derivable from the credential id,
    # which Credential.accessRead='allow' already exposes. Do not put anything in
    # here that must stay private.
    'document':           {'accessRead': 'allow'},
    'registrationNumber': {'accessRead': 'restrict'},   # admin group only
    'reviewNote':         {'accessRead': 'allow'},      # addressed to the practitioner
    'reviewStatus':       {'accessRead': 'allow'},
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

print('== Availability -> Clinician aggregations ==')
# Rolls weekly blocks onto the Clinician record so searchView can filter on them;
# Availability itself is accessRead='restrict' and views cannot join. See README.
ONTO_CLINICIAN = {'useGroupAsId': True, 'updateOnly': True, 'disablePurge': True}
BY_CLINICIAN = [{'sourceField': 'clinicianId', 'operation': 'exact', 'targetField': 'id'}]

# dayOff and extra rows carry no dayOfWeek to group by.
WEEKLY = 'kind = weekly ~AND~ active = true'

a = aggregation('availabilityDays', MODELS['Clinician'], MODELS['Availability'],
                sourceFilterQuery=WEEKLY, **ONTO_CLINICIAN)
agg_rules(a, 'AggregationGroupRule', ['sourceField'], BY_CLINICIAN)
agg_rules(a, 'AggregationAggregateRule', ['sourceField', 'operation'], [
    {'sourceField': 'dayOfWeek', 'operation': 'join', 'stringOperand': ',',
     'targetField': 'availableDays'},
    {'sourceField': 'startMinute', 'operation': 'min', 'targetField': 'earliestStartMinute'},
    {'sourceField': 'endMinute', 'operation': 'max', 'targetField': 'latestEndMinute'},
    {'sourceField': 'id', 'operation': 'count', 'targetField': 'availabilityCount'},
])

for name, target, window in [
    ('availabilityMorning',   'morningDays',   'startMinute < 720 ~AND~ endMinute > 360'),
    ('availabilityAfternoon', 'afternoonDays', 'startMinute < 1020 ~AND~ endMinute > 720'),
    ('availabilityEvening',   'eveningDays',   'startMinute < 1320 ~AND~ endMinute > 1020'),
]:
    a = aggregation(name, MODELS['Clinician'], MODELS['Availability'],
                    sourceFilterQuery=f'{WEEKLY} ~AND~ {window}', **ONTO_CLINICIAN)
    agg_rules(a, 'AggregationGroupRule', ['sourceField'], BY_CLINICIAN)
    agg_rules(a, 'AggregationAggregateRule', ['sourceField', 'operation'], [
        {'sourceField': 'dayOfWeek', 'operation': 'join', 'stringOperand': ',',
         'targetField': target},
    ])

print('\nAll models created/updated.')
print('Deploy, then rebuild the aggregations to fill in existing Clinician records:')
print("  curl -H \"Authorization:Bearer $(cat .saasufy-api-key)\" -XPOST "
      "'https://saasufy.com/api/service/start'")

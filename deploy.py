#!/usr/bin/env python3
"""Deploy the frontend to Saasufy's file hosting.

Rewrites the source from the development environment to the production one and
uploads the result to the record which serves it. The source file on disk is
never modified, so local development keeps its dev URLs and `keycloak:dev`
OAuth provider.

    ./deploy.py             # rewrite and upload
    ./deploy.py --dry-run   # report the rewrites, upload nothing

`deployURL` in config.json is both the replacement text and the upload target:
its path names what to write, as `/:serviceId/files/:model/:recordId/:field`.

Saasufy's HTTP API rejects request bodies over 100 KiB with "Payload Too Large",
and base64 inflates the file by a third, so the source is whitespace-minified
until it fits. This is a transport limit on the API, not the field's `max`.

Each Saasufy OAuth environment is its own provider with its own Keycloak client
and registered redirect URIs, so `rewrites` in config.json must swap the
provider name and client ID over to production alongside the URL. Deploying with
the dev provider still in place authenticates the live app against the dev
client, whose only registered redirect URI is localhost.
"""
import base64, json, mimetypes, os, re, sys, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, 'config.json')))
KEY = open(os.path.join(ROOT, '.saasufy-api-key')).read().strip()

DEPLOY = CFG['deployURL'].rstrip('/')
DEV = CFG['devURL'].rstrip('/')
SOURCE = os.path.join(ROOT, CFG['source'])
DRY = '--dry-run' in sys.argv[1:]
BODY_LIMIT = 102400  # measured; the API's cap on the whole request body

scheme, _, rest = DEPLOY.partition('://')
parts = rest.split('/')
if not scheme or len(parts) != 6 or parts[2] != 'files':
    raise SystemExit(
        f'deployURL must look like https://host/:serviceId/files/:model/:recordId/:field\n'
        f'  got: {DEPLOY}')
host, service, _, model, record, field = parts
API = f'{scheme}://{host}/{service}/api/{model}/{record}'


def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {KEY}')
    if data:
        req.add_header('Content-Type', 'application/json')
    try:
        return urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{method} {url}\n  HTTP {e.code}: {e.read().decode()[:400]}')


# Longest pattern first: the bare origin would otherwise match inside the full URL.
dev_origin = DEV[:DEV.index('/', len(scheme) + 3)] if '/' in DEV[8:] else DEV
REWRITES = [(DEV, DEPLOY), (dev_origin + '/', DEPLOY), (dev_origin, DEPLOY)]
REWRITES += list(CFG.get('rewrites', {}).items())

source = open(SOURCE, encoding='utf-8').read()
out, total = source, 0
for old, new in REWRITES:
    n = out.count(old)
    out = out.replace(old, new)
    total += n
    print(f'  {n:>3} x  {old}\n         -> {new}')

print(f'\n{CFG["source"]}: {total} replacement(s)')
if not total:
    print('  nothing matched — check devURL and rewrites in config.json')
# Every dev token must be gone; anything left would reach production.
for token in [dev_origin] + list(CFG.get('rewrites', {})):
    lines = [i + 1 for i, l in enumerate(out.split('\n')) if token in l]
    if lines:
        print(f'  WARNING: "{token}" still present on line(s) {lines}')

mime = mimetypes.guess_type(SOURCE)[0] or 'application/octet-stream'


def squeeze(text):
    """Leading indentation only. Newlines survive, so HTML still sees the
    whitespace it would have collapsed to a single space anyway."""
    return '\n'.join(l for l in (x.strip() for x in text.split('\n')) if l)


def decomment(text):
    return re.sub(r'<!--.*?-->', '', text, flags=re.S)


def body_for(text):
    uri = f'data:{mime};base64,' + base64.b64encode(text.encode()).decode()
    return json.dumps({field: uri}).encode()


STEPS = [('as authored', lambda t: t),
         ('indentation stripped', squeeze),
         ('indentation and comments stripped', lambda t: squeeze(decomment(t)))]

body = None
for label, fn in STEPS:
    candidate, size = fn(out), len(body_for(fn(out)))
    fits = size <= BODY_LIMIT
    print(f'  {label:<34} {len(candidate):>6} bytes -> {size:>6} body  '
          f'{"ok" if fits else "over limit"}')
    if fits:
        out, body = candidate, body_for(candidate)
        break

if body is None:
    raise SystemExit(f'\nCannot fit {CFG["source"]} under the {BODY_LIMIT} byte '
                     f'request limit even minified — split the file or upload it '
                     f'over the WebSocket API instead.')

print(f'  {BODY_LIMIT - len(body)} bytes of headroom')

if DRY:
    print('\n--dry-run: nothing uploaded.')
    raise SystemExit(0)

call('PUT', API, json.loads(body))
print(f'\nuploaded to {model}/{record}.{field}')

live = call('GET', DEPLOY).decode()
print('verified: served copy matches' if live == out
      else f'WARNING: served copy differs ({len(live)} bytes vs {len(out)})')
print(f'\n{DEPLOY}')
print('Keycloak must have this URL registered as a redirect URI, or login will '
      'fail after sign-in.')

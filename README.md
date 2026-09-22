# MyNaturalClinic

A marketplace for booking online consultations with natural therapists and clinicians.

Patients search for practitioners by name, location, ailment or specialisation, and — most importantly — by when they are actually free. Browsing and searching require no account; a patient only signs up at the moment they commit to a booking. Clinicians go through a deliberately higher-friction path: they register, verify their email, submit academic and professional credentials for review, and publish their availability as a recurring weekly calendar with date-specific exceptions.

The platform intermediates the relationship rather than just introducing the two parties. It issues the appointment invitation to the clinician, hosts the meeting link under its own domain, records attendance for both sides, takes the patient's payment up front, and releases funds to the clinician only once the consultation has actually taken place. That attendance record is what settles disputes.

## How it's built

The frontend is a static site built almost entirely from [Saasufy](https://saasufy.com) components — `app-router`, `collection-viewer`, `model-input` and friends — talking directly to a Saasufy service over WebSocket, with realtime sync and access control enforced at the data layer. There is no build step and no framework. Accounts are handled by the shared Saasufy Keycloak instance via Saasufy's OAuth components.

Saasufy has no server-side code, so everything with an external side effect — sending email, charging cards through Pin Payments, creating Zoom meetings, expiring holds, materialising availability slots, releasing payouts — lives in a companion self-hosted Node.js server. It is written as a reconciler rather than an API: the frontend writes intent into Saasufy, and the worker observes the change and acts on it. The browser never calls the worker for booking logic.

## Authentication

Accounts are handled by the shared Saasufy Keycloak instance. The `OAuthProvider` record
(`providerName: keycloak`) is created through the Admin HTTP API; Saasufy generates the client
ID and secret and provisions the client inside Keycloak automatically.

| | |
| --- | --- |
| Realm | **`tenant`** — *not* `master`, which is only Keycloak's admin realm |
| Authorize URL | `https://auth.saasufy.com/realms/tenant/protocol/openid-connect/auth` |
| Client ID | `saasufy-<your-saasufy-accountId>` (generated) |
| Registration | append `&prompt=create` to the authorize URL |
| Redirect URI | `http://localhost:8100/*` registered; the app sends `http://localhost:8100/index.html` |

The canonical endpoints live in the `keycloak` entry of <https://saasufy.com/oauth-settings.js>
(the `keycloak-master` entry is the Saasufy admin control panel — not for app use). The
`OAuthProvider` record does not carry the realm; Saasufy applies these defaults server-side.
If the authorize endpoint returns *"Client not found"*, check the realm before anything else.

**Changing where the app is served** means updating three things together:

1. `redirectURI` on the `OAuthProvider` record (Saasufy pushes the change into Keycloak), then deploy.
2. The `redirect-uri` attribute on both `oauth-link` elements and on `oauth-handler` in `index.html`.
3. Nothing else — the authorize URLs use `{{oauth.redirectURI}}`, which `oauth-link` fills in
   from its own `redirect-uri` attribute.

A mismatch here fails *after* a successful Keycloak sign-up: Keycloak redirects to whatever
`redirect_uri` the app sent, so pointing at a port nothing is serving lands the user on a blank
page with no error. The registered value uses a `*` wildcard so both `/` and `/index.html` are
accepted, but the value the app **sends** must be a concrete URL.

## Repository

| Path | |
| --- | --- |
| `requirements.md` | Full MVP specification — data model, booking state machine, access control, open decisions |
| `index.html` | The whole frontend — a single-page app built from Saasufy components, no build step |
| `create-schema.py` | Creates/updates the nine Saasufy collections, indexes, views and access rules. Idempotent — safe to re-run, then deploy |
| `seed-dev-data.py` | Seeds the topic vocabulary and a demo practitioner for local development |
| `dev-access.py` | Temporarily relaxes write access for local development; `restore` puts the spec's rules back |
| `.saasufy-api-key` | Saasufy admin credential (gitignored) |
| `.saasufy-service-url` | Deployed Saasufy service endpoint |

Start with [`requirements.md`](requirements.md).

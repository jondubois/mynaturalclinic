# MyNaturalClinic

A marketplace for booking online consultations with natural therapists and clinicians.

Patients search for practitioners by name, location, ailment or specialisation, and — most importantly — by when they are actually free. Browsing and searching require no account; a patient only signs up at the moment they commit to a booking. Clinicians go through a deliberately higher-friction path: they register, verify their email, submit academic and professional documents for review, and publish their availability as a recurring weekly calendar with date-specific exceptions.

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
| Registration | append `&prompt=create` to the authorize URL |
| Environments | `keycloak` (prod) and `keycloak:dev`, one `OAuthProvider` record each, each with its own Keycloak client |
| Client ID | prod `saasufy-<accountId>`; dev is suffixed, `saasufy-<accountId>-dev` — both auto-generated |
| Redirect URI | dev `http://localhost:8100/*`; prod `https://saasufy.com/sid8016/files/App/<recordId>/file/*` |
| Logout URL | `https://auth.saasufy.com/realms/tenant/protocol/openid-connect/logout` |

The canonical endpoints live in the `keycloak` entry of <https://saasufy.com/oauth-settings.js>
(the `keycloak-master` entry is the Saasufy admin control panel — not for app use). The
`OAuthProvider` record does not carry the realm; Saasufy applies these defaults server-side.
If the authorize endpoint returns *"Client not found"*, check the realm before anything else.

### Logging out

Deauthenticating the Saasufy socket does not end the Keycloak SSO session — its cookie outlives
the socket, so the next **Log in** is waved straight through without a credentials prompt. The
`log-out` element therefore carries a `logout-url` (plus `provider`, `client-id` and
`post-logout-redirect-uri`), which makes it follow the deauthentication with a redirect to
Keycloak's RP-initiated logout endpoint. No custom JavaScript is involved.

`post_logout_redirect_uri` is validated against the client's *valid post logout redirect URIs*,
which Keycloak defaults to `+` — meaning the client's valid redirect URIs — so it has to stay
equal to the redirect URI the app sends. The `id_token_hint` is supplied by the component from
the auth token (the default `keycloak` provider config sets `idTokenField`), which is what keeps
Keycloak from showing a *"Do you want to log out?"* confirmation page.

`index.html` is the **development** copy throughout: localhost URLs, `provider="keycloak:dev"`
and the `-dev` client ID. `deploy.py` swaps all three over to production on the way up, driven by
`devURL` and `rewrites` in `config.json` — so the two environments never need separate source files,
and it warns if any dev token survives the rewrite.

**Changing where either environment is served** means updating two things together:

1. `redirectURI` on that environment's `OAuthProvider` record (Saasufy pushes the change into
   Keycloak, per environment), then deploy the service.
2. The matching value in `config.json` — `devURL` for local, `deployURL` for production. The
   `redirect-uri` and `post-logout-redirect-uri` attributes in `index.html` hold the dev URL and
   are rewritten from there.

Nothing else — the authorize URLs use `{{oauth.redirectURI}}`, which `oauth-link` fills in from its
own `redirect-uri` attribute.

A mismatch here fails *after* a successful Keycloak sign-up: Keycloak redirects to whatever
`redirect_uri` the app sent, so pointing at a port nothing is serving lands the user on a blank
page with no error. The registered value uses a `*` wildcard so both `/` and `/index.html` are
accepted, but the value the app **sends** must be a concrete URL.

## Searching by availability

Every stored *instant* is UTC (`TimeSlot.startAt`), but a weekly rule is not an instant — "every
Monday 9am" in Sydney is 23:00 UTC for half the year and 22:00 for the other half, so there is no
single UTC number to store. `Availability` therefore stays in the practitioner's local time, as
minutes from local Sunday midnight, and UTC is *derived* for search only.

| Field | Holds |
| --- | --- |
| `Availability.startMinuteOfWeek` / `endMinuteOfWeek` | The block in local time, 0–10080 from Sunday 00:00, end exclusive |
| `Availability.utcHourKeys` | The UTC day-hours it covers, as `D-HH` keys — `0-22,0-23,1-00,…` |
| `Clinician.utcHours` | Every key of every weekly block, joined |
| `Clinician.availabilityCount` | How many weekly blocks they have |

One axis rather than `(dayOfWeek, minuteOfDay)` means a block may run past local midnight into the
next day without being split in two.

`Availability` is `accessRead: restrict`, so a patient browsing the site cannot read it, and a
Saasufy view cannot join across models anyway — hence the rollup onto `Clinician`, where
`searchView`'s second-phase query can reach it. Both `Clinician` fields are written **only** by
the `availabilityHours` aggregation and are blocked for user writes.

### Why hour keys rather than bands

An earlier design bucketed each practitioner's hours into fixed `morningDays` / `afternoonDays` /
`eveningDays` fields. That only works if the bucket boundaries are fixed in the frame the data is
stored in: a viewer's 6am is a different UTC hour for every offset, so a pre-bucketed band cannot
be queried from another timezone. Per-hour keys can — any local band from any zone maps onto a set
of UTC hours.

`contains` compiles to a regex match, so the whole set is still **one** term:

```
utcHours contains 0-19|0-20|0-21|0-22|0-23|1-00|1-01
```

A key is four characters wide and the `,` delimiter never appears in the pattern, so no match can
straddle two keys.

Keep that alternation **flat and unparenthesised**. `collection-view-params` is split client-side
by `fieldPartsRegExp` (`saasufy-components/utils.js`), whose `\([^)]*\)` branch treats a
parenthesised run as atomic and therefore cannot nest. Group the pattern as
`(0-(06|07)|1-(06|07))` and each inner group is consumed on its own, leaving a trailing `)` that
matches no branch — the scan stops one character short and the server receives an unbalanced
regex. The symptom is a RethinkDB `missing ): ...` error naming a pattern that is visibly your
own, minus its last character.

### The DST margin

The keys are computed by the browser, which is where the IANA database is — `Intl` with
`timeZoneName: 'longOffset'` gives the offset for a zone on a given date. A single number still
cannot hold both of a DST zone's answers, so both sides emit keys for **both** offsets their zone
uses across the year:

- **the practitioner's**, when a block is saved (`weeklyKeys` in `index.html`, `tzkeys.py` for the
  seed scripts — the two must stay in step);
- **the viewer's**, when the browse filter is built, because the 60-day booking horizon crosses a
  transition and the query is built once, today.

For a contiguous block that widens the match by one hour on one side, and not at all in a fixed
zone — Perth, Brisbane and Darwin come out exact. Search therefore over-matches slightly and never
under-matches, which is the right direction: the profile page shows real bookable instants anyway.
Measured across seven practitioner zones and six viewer zones over a full year, including the
half-hour zones: **no missed matches, 1.6% spurious**, against 2.5% missed and 6.2% spurious under
the band scheme.

A template can only read fields its `collection-viewer` actually fetched. `{{Clinician.timezone}}`
resolves to empty — not an error — if `collection-fields` omits `timezone`, and an unrecognised
zone name means the keys are silently computed at UTC. `weeklyKeys` therefore falls back to the
browser's own zone rather than UTC, so the worst case is a practitioner who has travelled, not one
filed ten hours out. `repair-availability-keys.py` recomputes the keys on every weekly row from
each clinician's profile timezone; run it after a timezone change, and with `--apply` to write.

Slot materialisation does **not** get this margin and must not — an hour of drift there is a
patient at 9am and a practitioner at 10am. The worker resolves the offset at each concrete date.

Each pipeline sets `useGroupAsId` (the group value, `clinicianId`, becomes the target record id,
which is what lands the result on the matching `Clinician`), `updateOnly` (a dangling
`clinicianId` cannot create a bogus `Clinician`) and `disablePurge` (clearing your hours must not
delete your profile).

**This needs a Saasufy build carrying the four aggregator fixes** (see the `saasufy` repo):
concurrent writes to a shared target record are merged rather than replaced; the generation stamp
is only claimed by an aggregation which purges; the rebuild queues are keyed by aggregation as
well as target record; and an `updateOnly` aggregation follows its source records out of a group,
so a deleted block clears the fields it fed. Collapsing the four pipelines into one removes the
shared-target race that made the first three visible here, but the last still bites: on an older
build, deleting your only weekly block leaves `utcHours` behind and you stay in search results you
no longer belong in, with `lastError` null because nothing actually fails.

To fill in existing records after a schema change, deploy and then rebuild:

```bash
curl -H "Authorization:Bearer $(cat .saasufy-api-key)" -H "Content-Type: application/json" \
  -XPUT "https://saasufy.com/api/Aggregation/{AGGREGATION_ID}" \
  -d "{\"rebuildRequestedAt\": $(date +%s%3N)}"
```

## Repository

| Path | |
| --- | --- |
| `requirements.md` | Full MVP specification — data model, booking state machine, access control, open decisions |
| `index.html` | The whole frontend — a single-page app built from Saasufy components, no build step |
| `create-schema.py` | Creates/updates the Saasufy collections, indexes, views and access rules. Idempotent — safe to re-run, then deploy |
| `migrate-credentials-to-documents.py` | Copies every `Credential` record into `Document`, preserving ids. Idempotent; `--dry-run` reports without writing. Kept until `Credential` is dropped |
| `migrate-qualificationname-to-documentname.py` | Copies `Document.qualificationName` into `documentName`. Idempotent; `--dry-run` reports without writing |
| `seed-dev-data.py` | Seeds the topic vocabulary and a demo practitioner for local development |
| `seed-categories.py` | Seeds the `Category` rows behind the `/browse` dropdowns and the profile topic list. Wipes and reseeds, so it is the source of truth for that table |
| `seed-practitioners.py` | Seeds ten sample practitioners and their weekly availability, spread across regions, specialisations and times of day |
| `dev-access.py` | Temporarily relaxes write access for local development; `restore` puts the spec's rules back |
| `deploy.py` | Rewrites the dev URLs in `index.html` to the deployed URL and uploads it to Saasufy's file hosting. Strips indentation to stay under the API's 100 KiB request-body limit, which base64 hits well before the field's own `max`; `--dry-run` reports without uploading |
| `config.json` | Deployment target — the source file, the dev URL to replace and the `files/` URL the app is served from |
| `.saasufy-api-key` | Saasufy admin credential (gitignored) |
| `.saasufy-service-url` | Deployed Saasufy service endpoint |

Start with [`requirements.md`](requirements.md).

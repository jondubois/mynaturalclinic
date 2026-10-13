# MyNaturalClinic

A marketplace for booking online consultations with natural therapists and clinicians.

Patients search for practitioners by name, location, ailment or specialisation, and — most importantly — by when they are actually free. Browsing and searching require no account; a patient only signs up at the moment they commit to a booking. Clinicians go through a deliberately higher-friction path: they register, verify their email, submit academic and professional documents for review, and publish their availability as a recurring weekly calendar with date-specific exceptions.

The platform intermediates the relationship rather than just introducing the two parties. It issues the appointment invitation to the clinician, hosts the meeting link itself, records attendance for both sides, takes the patient's payment up front, and releases funds to the clinician only once the consultation has actually taken place. That attendance record is what settles disputes.

## How it's built

The frontend is a static site built almost entirely from [Saasufy](https://saasufy.com) components — `app-router`, `collection-viewer`, `model-input` and friends — talking directly to a Saasufy service over WebSocket, with realtime sync and access control enforced at the data layer. There is no build step and no framework. Accounts are handled by the shared Saasufy Keycloak instance via Saasufy's OAuth components.

Everything with an external side effect — sending email, charging cards through Pin Payments, creating Zoom meetings, expiring holds, releasing payouts — runs as a **Saasufy cloud function**: sandboxed JavaScript inside the service itself, reachable over HTTP at `{SERVICE_URL}/functions/{name}`, with secrets supplied through `process.env` from `Constant` records so none of them reaches a browser. Recurring work (hold expiry, invite expiry, reminders, payout passes) is a `ScheduledTask` pointing at one of those functions.

Cloud functions are a last resort, not the default. Anything a view, an aggregation or an access-control rule can do belongs there instead, because those are the parts of Saasufy built so that an unscalable pattern is hard to express. A function earns its place only where there is genuinely no declarative equivalent: an outbound API call, a webhook, work needing a credential the frontend must not hold.

Two consequences shape the design, and both are covered below:

- **Nothing is a single writer.** Functions run on a pool of VMs, several per Saasufy service process, so anything that reads a record and then writes it has to be safe under concurrency. The booking flow does this with a derived primary key rather than a lock.
- **The sandbox is standard ECMAScript only** — no Node APIs, no timers, no crypto. That last one decides how a payment webhook gets trusted.

Scheduled tasks are the exception to the first point: each task is handed to exactly one process, so a sweep is never racing a copy of itself.

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
| Redirect URI | dev `http://localhost:8100/*`; prod `https://sid8016.saasufy.com/files/App/<recordId>/file/*` |
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

`Availability` is `accessRead: allow` — the booking calendar renders it directly in an
anonymous patient's browser (see below) — but a Saasufy view cannot join across models, so
search still needs the rollup onto `Clinician`, where `searchView`'s second-phase query can
reach it. Both `Clinician` fields are written **only** by
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

Deriving the bookable times themselves does **not** get this margin and must not — an hour of
drift there is a patient at 9am and a practitioner at 10am. The booking calendar resolves the
offset at each concrete instant (see below), and so must anything which later re-checks a booking
server-side.

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

## Booking a time

The public profile's **Choose a time** leads to `#/book/:clinicianId`, which renders a week of
the clinician's open times at a time.

**The times are computed, not stored.** The page reads the clinician's `Availability` blocks
and expands them into concrete instants in the browser, which is where the IANA database is —
the same reasoning that puts `utcHourKeys` there. A `TimeSlot` row is written only when a time
is actually taken, so the collection holds bookings rather than a pre-materialised grid of
every bookable minute for every practitioner. The alternative — a row per visible slot — cost
roughly 300 rows per clinician per 60 days, all of it rewritten whenever anyone edited their
hours, and nearly all of it never looked at.

This is why `Availability` is `accessRead: allow`. A patient has no account when they pick a
time, and nothing in those blocks is private: it is the same "when I work" the calendar then
draws. The field-level restrictions that matter are on `Clinician`, not here.

The page therefore binds two collections:

| | |
| --- | --- |
| `Availability` via `clinicianView` | The weekly pattern, blackouts and one-off openings. A handful of rows, fetched once |
| `TimeSlot` via `clinicianRangeView`, `status contains held\|booked` | The times already taken, for the displayed week only. Subtracted from the computed set |

Expansion is DST-correct: each block is walked over the clinician's own calendar dates and each
local time is converted with the offset *at that instant*, re-read once because the first lookup
can land the wrong side of a transition. Blocks may run past local midnight — the single
minute-of-week axis holds that without splitting them. The two-hour lead time and the 60-day
horizon are applied to the computed set.

**A week, not a month, because a Saasufy view page is capped at 200 records.** That cap no longer
binds on the slots themselves, but it still binds on the taken-slot query, and a week keeps the
calendar legible. The week arrows rewrite `collection-view-params` on the inner reducer, which is
what asks for the next window of taken times; the available times for that week are recomputed
locally.

Neither reducer slots a `no-item` template: a clinician with no hours, or an empty week, must
still render the week arrows or there is no way back out, so the item template draws those cases
itself.

## From a chosen time to an appointment

Everything up to **Request this time** happens anonymously. The form asks for a name and,
optionally, what the patient would like help with — and deliberately **not** for an email
address: Keycloak collects and verifies one on the way to payment, and asking twice only invites
the two to disagree about who the invitation should reach.

The button is therefore where the account is asked for, and not before — browsing, picking a time
and saying what you need all happen without one:

| | |
| --- | --- |
| **1 · Request this time** | The selection is written to `sessionStorage` and, if the patient is already signed in, the page goes straight to step 3 |
| **2 · Log in or register** | Two `oauth-link`s revealed under the form, the same pair as clinician onboarding, the register one carrying `&prompt=create` |
| **3 · `#/booking/payment/:clinicianId`** | What it costs and who for, and a link out to `PIN_PAYMENTS_URL` |
| **4 · `#/booking/confirmed/:clinicianId`** | Writes the `TimeSlot` and the `Appointment`, and calls the `appointmentInvite` cloud function |

**The selection has to survive leaving the origin.** Keycloak redirects to the bare page URL, so
the round trip is a fresh page load and no component state outlives it; `sessionStorage` does,
within the tab. Two keys, and never both: `mnc.pendingBooking` while the appointment is only
intended, `mnc.bookedAppointment` once it exists. Making a new hold drops any receipt — otherwise
booking the same practitioner twice in a visit reads as already paid.

**The OAuth callback needs two landing pages**, because `oauth-handler`'s `success-url` is a fixed
attribute and cannot name the clinician the patient was booking. So `/auth/callback` lands on the
dashboard and `/auth/callback/booking` lands on `#/booking/resume`, which is not a page so much as
a redirect: a script reads the hold and rewrites the hash to the payment step. Which of the two the
redirect arrives at is decided before `app-router` first renders, by whether a hold exists.

### What is a stand-in, and what is real

`PIN_PAYMENTS_URL` (a constant at the top of `index.html`) points at a hosted Pin Payments page
which knows nothing about the booking and does not redirect back. Neither the Pin.js hosted-field
checkout nor the webhook which confirms a charge is built, so the payment page carries a link
onwards to the success page in place of Pin's redirect, and the `Appointment` is written with
`paymentStatus: 'none'` — not `'paid'`. Nothing has been verified as paid, and the record should
not claim otherwise.

When that checkout does land, a note for whoever writes it: **the sandbox has no crypto
primitive**, so a cloud function cannot compute the `HMAC-SHA256` that verifies Pin's
`Pin-Signature` header. Treat the webhook as an untrusted nudge and confirm the charge by fetching
it back from Pin's API with the secret key. That is stronger than trusting a signature anyway, and
it survives the fact that Pin keeps webhook records for only 30 days.

The two rows themselves are real. The `TimeSlot` is what takes the time out of every other
patient's calendar, and `status: 'pending_clinician'` is what puts the appointment on the
practitioner's dashboard. Both are written by the patient's browser, which raises the obvious
question.

### The double-booking race, without a single writer

Two patients can want one instant. The usual answer is to funnel the write through a single
process and have it create the `TimeSlot` keyed on `(clinicianId, startAt)`, so the key collides
and the loser is told the time was taken. Nothing here is a single writer — not the browsers, and
not cloud functions either, which run several at a time. There is also no unique index to declare
on that pair. But there *is* a unique constraint already, on every table: the primary key.

So the slot's id is **derived** from the pair rather than random:

```
id = uuidv5("6f8c1e5a-3b47-4d2e-9a10-5c7e2d9f4b83", clinicianId + ":" + startAt)
```

Two browsers racing for the same instant compute the same id, both attempt the create, and the
database lets exactly one through — the other comes back with *Duplicate primary key `id`*. The
constraint is enforced by the store rather than by whoever happens to be writing, which is why it
keeps holding however many browsers and cloud functions are in flight at once.

**The create is the lock.** The losing browser reads the row only to decide what to *say* — ours
already (a reload mid-flight), held or booked by someone else ("taken while you were paying"), or
released by an earlier cancellation and not this browser's to reuse. Reading first to decide
whether to write would reopen the exact window the derived id closes.

This is a protocol, not an implementation detail: anything else which creates a slot must derive
the id identically or the constraint is silently gone. The name is exactly
`<clinicianId>:<startAt>`, `startAt` in epoch milliseconds, unpadded, and the namespace above is
fixed forever — changing it orphans every slot ever written. It is plain RFC 4122 v5, verified
against Python's `uuid.uuid5`, so any off-the-shelf implementation agrees with it.

Cloud functions are the exception which proves the rule: the sandbox has no SHA-1, so a function
*cannot* derive one of these ids. It does not need to — slots are only ever created by the
booking page, and everything server-side reaches a slot through `Appointment.timeSlotId` or the
`clinicianIdStartAt` index. Keep it that way; a function that needs to create a slot is a function
that needs this derivation hand-written first.

Saasufy validates record ids as UUIDs but no longer pins the version, which is what makes this
possible; an earlier v4-only check is why the first cut of this flow had no race protection at all.

A derived id is of course guessable, but nothing rests on it being secret: `TimeSlot` is
`accessRead: allow`, so the rows are public anyway, and what lets a patient block a time is being
allowed to create the row at all, not the shape of its id.

Writing from the browser also means both parties own both rows — `accountId` holds the patient's
and the clinician's ids, comma-separated, with `accessEnableMultipleOwners` on `TimeSlot` as well
as `Appointment`. The patient has to be an owner to create the slot at all; the clinician still
needs update rights to cancel it.

This is also why a released slot is **deleted** rather than set back to `open`. An `open` row
survives still owned by its original two parties, so the next patient's create collides on the
derived id and they can neither take the row nor update it: the time would show as free and refuse
to be booked. Removing it is what actually frees the instant, and nothing is lost — the
`Appointment` keeps its own `startAt` and `endAt`. Declining does exactly this (see below).

### Retrying rather than unwinding

Nothing is written until the patient has been through payment, and then the two rows go in
together — slot first, because claiming the time is what the race turns on. Which leaves one
window: the slot is claimed and the `Appointment` write fails.

That failure is **not** compensated by deleting the slot. A delete-on-failure means a sequence
whose correctness depends on the cleanup also succeeding, and the cleanup runs in exactly the
conditions that just proved unreliable — a closing tab, a dropped socket. So the page keeps the
time and offers **Try again**, and every write in the sequence is built to be run twice:

| | |
| --- | --- |
| The slot | Derived id. A second attempt collides, reads the row, finds it is ours, and continues |
| The appointment | Its id is written onto the slot *before* it exists, so a retry reuses it rather than minting a second one |
| A lost answer | A failed create is followed by a read of that id: if it actually landed, the booking is simply finished |

The `Appointment` id being a forward reference on the slot row looks odd and is the point: it is
what makes the retry land on the booking already in progress.

The one failure this cannot absorb is the patient closing the tab and never coming back, which
leaves a `booked` slot with no appointment against it. The schema makes that visible — a
`TimeSlot` whose `appointmentId` names nothing — so a scheduled sweep can reclaim it, and until
then it is one stale row rather than a wrong answer to anybody. That sweep is the clearest first
job for a `ScheduledTask`.

Validation is also pulled forward for this reason: `intakeReason` is capped at 500 characters by
the schema, so the form caps and checks it too. The only likely non-race failure should not be
discovered halfway through writing a booking.

The patient's first booking also seeds their `Patient` record, so their dashboard has something to
edit. It reads before it writes: a name they have since corrected there is theirs, and this
booking's must not overwrite it.

### The invite email

`appointmentInvite` is a cloud function, called from the success page with the patient's own JWT.
It will send the clinician their invitation; for now it only logs what it would send, so the shape
of the call and its access control could be settled before an email provider is chosen. It is also
the template for every function after it, because the access-control shape is the part that is easy
to get wrong:

**The hook is unauthenticated, and its `crud` runs as the account admin** — so access-control rules
do not apply inside a function at all. A function that writes based on unverified input is an open
door to the whole dataset. This one therefore verifies the bearer token first, then checks the
caller's `accountId` against the appointment's owners, and only then reads anything. The patient's
address comes from the Keycloak-verified `Account` record, never from a request parameter.

Sending mail needs a provider credential the frontend must not hold, which is what earns this a
function rather than a browser write. Run `create-cloud-functions.py`, then deploy — **the service
reads its functions only at startup**, so a code change with no deploy silently runs the old one.

## Reading a two-party record

`Appointment` belongs to two people, so `accountId` holds both ids and
`accessEnableMultipleOwners` lets either read the row. That covers reading the *record*. Reading a
**view** of those records is a separate check, and a sharper one:

> A view over a `restrict` model is only readable if one of its params is named after the model's
> `accessModelAuthField`.

That field is `accountId`. Saasufy compares the value passed in *that* param against the caller's
own token, and refusing anything else is what makes the view owner-scoped. A view keyed on
`patientAccountId` — the obvious name, matching the column it filters — is not owner-protected at
all, and every read of it fails with *"You do not have permission to perform the read operation on
the view"*, including the owner's own.

So both party views take the id in a param called `accountId`, and the **index** decides which
column it is matched against:

| View | Param | Index it is bound to |
| --- | --- | --- |
| `patientView` | `accountId` | `patientAccountIdStartAt` |
| `clinicianView` | `accountId` | `clinicianAccountIdStartAt` |

Nothing is given away by the rename: the param is still consumed by the index, so it filters as
before, and a caller can only ever ask for their own id. Verified against the live service with a
signed user token — a patient reading `patientView` for their own account succeeds, for anyone
else's is refused, and the fields the schema blocks stay blocked.

The frontend therefore passes nothing but its own account id; which side of the appointment it is
on is the view's business.

## Two dashboards, one route

`#/dashboard` renders either the practitioner dashboard or the patient one. The role test is the
existence of a `Clinician` record owned by the signed-in account, which is already a
`collection-viewer` on `accountView` — so it is that viewer's own `item` / `no-item` branch, and
there is no role flag anywhere to be kept in step.

`#/clinician` is now only the way *in* to being a practitioner: the pitch when signed out, the
create-profile form when signed in without one, and a pointer to the dashboard when there already
is one. The `/clinician/*` pages are unchanged.

The two dashboards share only what is genuinely the same for both — your own details, edited in
place, and which account you are signed in as. Everything else differs, because almost nothing a
patient does resembles what a practitioner does: a patient sees appointments coming up and past
ones; a practitioner sees an onboarding checklist, their listing status, their hours and the
bookings made against them.

## Accepting and declining

A practitioner answers a request from their own dashboard. No server-side code is involved in the
decision, because there is nothing privileged about it: the clinician is already an owner of both
rows, so both writes are theirs to make.

- **Accept** writes `status: 'confirmed'` and stamps `inviteRespondedAt`.
- **Decline** offers a reason, writes `status: 'declined'` with it, and then deletes the
  `TimeSlot` so the instant is bookable again.

**Status first, slot second, never the other way round.** Freeing the time before the decline is
recorded would let someone book an instant the practitioner is still committed to. If the release
fails the decline still stands and the time merely shows as taken — visible, fixable, and the safe
direction to fail in.

Both dashboards are bound to `status`, so the patient sees the answer appear on their own
dashboard, and the buttons disappear from any other tab the practitioner left open. Until email
exists, that realtime update *is* the notification.

What is deliberately missing is the refund. `paymentStatus` stays `none` — nothing was charged,
and writing `refunded` would be a lie — and the decline message says so rather than implying the
money has moved. A cloud function will issue it, and should take over the slot release at the same
time: a browser can re-read a status immediately before writing it, which this does, but it cannot
make the check and the write one atomic step. A function can, and it is the only writer that
should be deciding a refund.

## Repository

| Path | |
| --- | --- |
| `index.html` | The whole frontend — a single-page app built from Saasufy components, no build step |
| `create-schema.py` | Creates/updates the Saasufy collections, indexes, views and access rules. Idempotent — safe to re-run, then deploy |
| `create-cloud-functions.py` | Creates/updates the Saasufy cloud functions. Idempotent; a function's name cannot change, so it matches on name and replaces the code. Deploy afterwards — functions are read only at service startup |
| `migrate-credentials-to-documents.py` | Copies every `Credential` record into `Document`, preserving ids. Idempotent; `--dry-run` reports without writing. Kept until `Credential` is dropped |
| `migrate-qualificationname-to-documentname.py` | Copies `Document.qualificationName` into `documentName`. Idempotent; `--dry-run` reports without writing |
| `migrate-availability-to-minute-of-week.py` | Converts `Availability` rows still on the dropped `(dayOfWeek, startMinute, endMinute)` triple to `startMinuteOfWeek` / `endMinuteOfWeek`, and fills in `utcHourKeys`. Idempotent; `--dry-run` reports without writing |
| `seed-dev-data.py` | Seeds the topic vocabulary and a demo practitioner for local development |
| `seed-categories.py` | Seeds the `Category` rows behind the `/browse` dropdowns and the profile topic list. Wipes and reseeds, so it is the source of truth for that table |
| `seed-practitioners.py` | Seeds ten sample practitioners and their weekly availability, spread across regions, specialisations and times of day |
| `dev-access.py` | Temporarily relaxes write access for local development; `restore` puts the real rules back |
| `deploy.py` | Rewrites the dev URLs in `index.html` to the deployed URL and uploads it to Saasufy's file hosting. Minifies only if needed to stay under the API's 1,000,000-byte request-body limit, which base64 hits well before the field's own `max`; `--dry-run` reports without uploading |
| `config.json` | Deployment target — the source file, the dev URL to replace and the `files/` URL the app is served from |
| `.saasufy-api-key` | Saasufy admin credential (gitignored) |
| `.saasufy-service-url` | Deployed Saasufy service endpoint |

Start with [`index.html`](index.html) — the whole frontend is in it, and the sections above explain
the parts that are not obvious from reading it.

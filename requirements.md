# MyNaturalClinic — MVP Requirements

**Status:** Draft v7 · 2026-09-23 — search simplified: `listingStatus` is the only thing that controls visibility, and the derived search fields are gone (§6.4)
**Product:** A marketplace where patients find natural therapists and clinicians by name, location, ailment/specialisation and availability, and book a paid online consultation.

---

## 1. Product summary

MyNaturalClinic is a two-sided marketplace.

- **Clinicians** register, submit academic and professional credentials, are verified by the platform, and publish their availability (AirBnB-style calendar).
- **Patients** search and browse anonymously with no account, and only sign up at the moment they commit to a booking.
- The platform **intermediates the relationship**: it issues the meeting invitation, hosts the meeting link under its own domain, records attendance of both parties, holds the patient's payment up front, and releases funds to the clinician only after attendance is recorded.

The MVP goal is a complete, trustworthy booking loop for online (video) consultations only.

---

## 2. Architecture constraints — read this first

### 2.1 What Saasufy provides

Saasufy is a realtime datastore with declarative frontend web components. It gives us:

- Models, fields, indexes and **views** (parameterised, indexed queries) managed via the Admin HTTP API.
- Model- and field-level **access control** enforced on both the WebSocket and HTTP protocols.
- **Realtime sync** — clients subscribe to view and field changes over WebSocket.
- **OAuth**, including a default shared **Keycloak** instance at `https://auth.saasufy.com` for email signup.
- **File hosting** for `blob` fields, served at `/:serviceId/files/:model/:id/:field`.
- **Aggregation pipelines** that maintain derived collections in realtime.
- Declarative components: `socket-provider`, `app-router`, `collection-viewer`, `collection-adder(-form)`, `model-viewer`, `model-input`, `model-text`, `input-provider`, `input-combiner`, `input-transformer`, `if-group`, `switch-group`, `render-group`, `oauth-link`, `oauth-handler`, `log-out`, modals.

### 2.2 What Saasufy does NOT provide

Saasufy has **no server-side custom code**: no functions, no hooks, no webhook receivers, no outbound HTTP, no email sending, no scheduled jobs.

Therefore the following MVP behaviours **cannot** be implemented in Saasufy alone and require a companion backend:

| Capability | Why it can't be Saasufy-only |
| --- | --- |
| Sending email (invites, verification, reminders, receipts) | No SMTP / outbound calls |
| Pin Payments charges, refunds, transfers | Requires secret API key and a webhook endpoint |
| Creating Zoom meetings | Requires server-to-server OAuth and secret credentials |
| Time-based transitions (invite expiry, slot materialisation, reminders, settlement) | No scheduler |
| Authoritative attendance from Zoom participant reports | Requires polling Zoom API |
| Serving `https://mynaturalclinic.com/m/:token` meeting links | Needs an HTTP route under our domain |

### 2.3 Resulting shape: Saasufy + one companion worker

```
                 ┌───────────────────────────────────────────┐
  Patient /      │  Static frontend (HTML + Saasufy          │
  Clinician ────▶│  components, served from any static host) │
  browser        └──────────────┬────────────────────────────┘
                                │ WebSocket (socketcluster)
                                ▼
                 ┌───────────────────────────────────────────┐
                 │  Saasufy service  wss://saasufy.com/sid8016│
                 │  models · views · access control · files  │
                 └──────────────┬────────────────────────────┘
                                │ WebSocket subscribe + CRUD
                                │ (service API credential)
                                ▼
                 ┌───────────────────────────────────────────┐
                 │  clinic-worker — custom Node.js server,   │
                 │  self-hosted                              │
                 │  • watches Saasufy views for new work     │
                 │  • Pin Payments · Zoom · Postmark         │
                 │  • cron: slots, expiry, reminders, payout │
                 │  • HTTP: /m/:token, /pin/webhook,         │
                 │    /invite/:token                         │
                 └───────────────────────────────────────────┘
```

**Design rule:** the worker is a *reconciler*, not an API. The frontend never calls the worker directly for booking logic; it writes intent into Saasufy (e.g. an `Appointment` with `status = 'pending_payment'`), and the worker observes that state change and performs the side effect, writing the result back. This keeps the app declarative and keeps a single source of truth.

**REQ-ARCH-1** The worker authenticates to the Saasufy service with a dedicated service API credential (`allowRead` + `allowWrite`), never with the admin credential.
**REQ-ARCH-2** Every worker side effect must be idempotent and keyed on a field in the record (e.g. `zoomMeetingId` non-empty ⇒ do not create another meeting), because subscriptions can redeliver.
**REQ-ARCH-3** Pin Payments and Zoom secrets live only in the worker's environment. No secret may be present in any file served to a browser.

### 2.4 Worker deployment — confirmed

The worker is a **custom Node.js server, self-hosted**. This is settled, not an open question.

**REQ-ARCH-4 · Public surface.** The worker terminates TLS on `mynaturalclinic.com` (directly or behind a reverse proxy) and serves exactly four public routes:

| Route | Method | Purpose | Auth |
| --- | --- | --- | --- |
| `/m/:meetingToken` | GET | Record attendance, redirect to Zoom (§6.7) | Token only |
| `/invite/:inviteToken` | GET/POST | Clinician accept/decline (§6.6) | Token only |
| `/pin/webhook` | POST | Charge, refund and transfer events | `Pin-Signature` HMAC |
| `/healthz` | GET | Liveness, unauthenticated, no data | None |

Everything else — profile editing, search, dashboards — goes browser → Saasufy directly and must never be proxied through the worker.

**REQ-ARCH-5 · Pin Payments webhook endpoint** must be publicly reachable over HTTPS with a valid certificate. Every request carries a `Pin-Signature` header containing a timestamp `t` and signature `v1`; the worker must verify it by computing `HMAC-SHA256(signing_key, t + "." + raw_body)` against the endpoint's signing key **before parsing the body or changing any state**. Unverified requests are dropped, not retried. Note that Pin retains webhook records for only **30 days**, so reconciliation that depends on replaying them must happen well inside that window.

**REQ-ARCH-6 · Process model.** Single process for the MVP. Scheduled work (slot materialisation, invite expiry, reminders, completion evaluation, payout release) runs on an in-process timer, not system cron, so the schedule ships with the code.

**REQ-ARCH-7 · Single-writer constraint.** Slot transitions are safe because exactly one worker instance performs them (REQ-BOOK-2). **Running a second instance without adding a distributed lock reintroduces the double-booking race.** If the worker is ever scaled horizontally, slot transitions and the scheduler must move behind a lock or a leader election first. This constraint must be stated in the worker's README.

**REQ-ARCH-8 · Restart safety.** The worker holds no authoritative state in memory. On boot it reconciles from Saasufy: every `pending_payment` appointment past its hold TTL, every `pending_clinician` past its invite expiry, every `confirmed` appointment past `endAt + 30 min`, and every queued `Email`. A restart mid-booking must lose nothing.

**REQ-ARCH-9 · Configuration** is entirely environment variables — Saasufy service URL and service credential, Pin Payments secret API key + webhook signing key, Pin environment (test vs live base URL), Zoom credentials, email provider key, public base URL, platform fee. No secrets in the repository; `.env` is gitignored alongside `.saasufy-api-key`.

**REQ-ARCH-10 · Observability.** Structured JSON logs to stdout. Every side effect logs the appointment ID, the action, and the outcome. Failed Pin Payments, Zoom or email calls log at error level with the provider's error body — these are the records needed to answer "why wasn't this clinician paid?".

**REQ-ARCH-11 · Backup.** Saasufy holds the authoritative data; the worker is stateless and can be redeployed freely. A periodic export of `Appointment`, `Attendance` and `Clinician` via the Admin HTTP API is retained off-platform, since attendance and payment records may be needed for disputes long after the fact.

---

## 3. Actors

| Actor | Auth state | Capability |
| --- | --- | --- |
| **Visitor** | Anonymous | Search, filter, view clinician profiles and open availability. Cannot book. |
| **Patient** | Keycloak account | Everything a Visitor can do, plus book, pay, attend, view own appointments. |
| **Clinician** | Keycloak account, email-verified, credential-approved | Manage profile, credentials, availability; accept/reject invites; attend; receive payouts. |
| **Admin** | Saasufy dashboard / admin credential | Review and approve/reject clinician credentials; resolve disputes. MVP admin UI is the Saasufy dashboard plus a minimal internal page. |

---

## 4. Authentication and onboarding

### 4.1 Mechanism

**REQ-AUTH-1** Authentication uses the shared Saasufy Keycloak instance via an `OAuthProvider` record with `providerName = "keycloak"`. `providerClientId` and `providerClientSecret` are auto-generated; only `redirectURI` is set explicitly, pointing at the page hosting `<oauth-handler>`.

**REQ-AUTH-2** The frontend uses `<oauth-link provider="keycloak" ...>` to start the flow and `<oauth-handler provider="keycloak" redirect-uri="..." ...>` on the callback page. The `redirect-uri` attribute must match the `redirectURI` on the `OAuthProvider` record.

**REQ-AUTH-3** Registration uses the same `<oauth-link>` with `&prompt=create` appended to the authorize URL, which sends the user to the Keycloak registration flow instead of login.

**REQ-AUTH-4** A single Keycloak account maps to one Saasufy `accountId`. Role is determined by the existence of a `Clinician` record owned by that `accountId` — there is no separate role flag on the account.

### 4.2 Patient onboarding — low friction

**REQ-PAT-1** No account is required to search, filter, view profiles, or view available slots.
**REQ-PAT-2** The patient chooses a clinician and a slot, and may enter their consultation intake details (reason for visit, notes), all while anonymous. This selection is held in `sessionStorage`.
**REQ-PAT-3** Sign-up/login is triggered only when the patient presses **Confirm booking**. After the Keycloak round trip, the held selection is restored and the booking proceeds directly to payment.
**REQ-PAT-4** Email verification is **not** required for patients before booking. The patient's email comes from the verified Keycloak profile.

### 4.3 Clinician onboarding — high friction, deliberately

**REQ-CLIN-1** A clinician must register through Keycloak and **complete Keycloak email verification** before a `Clinician` can move beyond `draft`.
**REQ-CLIN-2** The clinician must supply, at minimum: display name, professional title, at least one `Topic`, country + state/region + city, timezone, consultation duration, price, a biography of ≥ 200 characters, and at least one credential record.
**REQ-CLIN-3** Each `Credential` requires: type (`degree` | `diploma` | `certification` | `registration` | `licence`), issuing institution, qualification name, year awarded, and a supporting document upload (PDF or image, stored in a Saasufy `blob` field).
**REQ-CLIN-4** A clinician's profile is only visible in patient search results when `listingStatus = 'listed'`, which requires **all** of: Keycloak email verified, profile complete per REQ-CLIN-2, at least one credential with `reviewStatus = 'approved'`, a verified payout recipient (REQ-CLIN-6), and at least one future availability slot.
**REQ-CLIN-5** Credential review is manual for the MVP. An admin sets `reviewStatus` to `approved` or `rejected` with a `reviewNote`. The clinician sees the status and the note on their dashboard in realtime.
**REQ-CLIN-6 · Payout details.** Pin Payments has **no hosted seller-onboarding product**. Clinicians do not hold their own Pin account; each is represented as a Pin **Recipient**, created from an email address plus Australian bank details (BSB and account number) that *we* collect in our own UI. Accordingly:
  - The bank details form is part of the clinician dashboard, submitted directly to Pin's `/recipients` endpoint via the worker. **Raw BSB and account numbers are never written to Saasufy** — only the returned `pinRecipientToken` and a masked last-four for display.
  - `payoutStatus` moves `none → pending → active` once Pin returns a recipient token; `restricted` if Pin rejects the details.
  - **Identity verification is our responsibility.** Stripe Connect would have performed KYC on the clinician; Pin does not. For the MVP, the manual credential review (REQ-CLIN-5) doubles as the identity check — the admin must confirm the name on the credentials matches the name on the payout account before approving. This is a deliberate, documented gap: it is a manual control, not an automated one.

---

## 5. Data model (Saasufy)

### 5.1 Conventions

- Saasufy field types are only `string`, `number`, `boolean`. **All timestamps are `number`, epoch milliseconds UTC.**
- **All money is `number`, integer minor units (cents), with a separate `currency` string.** Never floats.
- Where a record is shared between two parties, ownership uses Saasufy's multiple-owner support: `accessEnableMultipleOwners: true` with a comma-separated `accountId` field containing both parties' account IDs.
- Records the patient must read before authenticating (clinician profiles, slots) use `accessRead: "allow"` with sensitive fields locked down individually at field level.
- Fields only the worker may write are set to `accessUpdate: "restrict"` with no owner, so that only the service credential can modify them.

### 5.2 The collections, in plain English

**Design rule:** every collection must be explainable to a non-technical person in one sentence. There are no derived, denormalised or index-only collections — search performance is achieved with *fields and indexes* on the collections below, not with extra tables (§6.4).

| Collection | What it is |
| --- | --- |
| `Clinician` | A practitioner who offers consultations, and everything shown on their public listing. |
| `Credential` | A qualification a clinician has submitted for us to check. |
| `Availability` | When a clinician says they work — their weekly pattern, plus days off and one-off extra hours. |
| `TimeSlot` | A single bookable appointment time. |
| `Appointment` | A booking between a patient and a clinician. |
| `Attendance` | A record that someone turned up to a meeting. |
| `Review` | A patient's rating of a consultation. |
| `Topic` | A specialisation or health concern that people can search by. |
| `Email` | An email the system still needs to send. |

Nine collections, plus Saasufy's built-in `Group` / `GroupMembership` which the MVP does not use.

### 5.3 Collection detail

#### `Clinician`
One row per practitioner. This is both the profile **and** the search listing — see §6.4 for why they are not separate.

| Field | Type | Notes |
| --- | --- | --- |
| `accountId` | string | Owner. Auth field. |
| `displayName` | string | Public |
| `professionalTitle` | string | e.g. "Naturopath, BHSc" |
| `bio` | string | min 200 chars |
| `photo` | string | `blob` |
| `topics` | string | `multi` — slugs from `Topic`, `maxCardinality: 8`. Matched directly by the search query; there is no derived copy |
| `country`, `region`, `city` | string | Public |
| `timezone` | string | IANA name |
| `languages` | string | `multi` |
| `consultationMinutes` | number | 30 / 45 / 60 |
| `priceAmount` | number | integer cents |
| `priceCurrency` | string | MVP: `AUD` |
| **Listing** | | |
| `listingStatus` | string | enum `draft,pending_review,listed,suspended` — **worker/admin write only** |
| **Private fields — field-level `accessRead: restrict`** | | |
| `contactEmail` | string | Never public |
| `pinRecipientToken` | string | **worker write only** |
| `payoutAccountLast4` | string | Owner-read only. Raw BSB/account number is never stored in Saasufy |
| `payoutStatus` | string | enum `none,pending,active,restricted` — **worker write only** |
| `emailVerified` | boolean | Mirrored from Keycloak — **worker write only** |
| `ratingAverage`, `ratingCount` | number | Maintained by aggregation pipeline (§5.4) |
| `createdAt`, `updatedAt` | number | Automatic |

Access: `accessCreate: restrict`, `accessRead: allow`, `accessUpdate: restrict`, `accessDelete: block` (soft-delete via `listingStatus`), `accessTokenAuthField: accountId`, `accessModelAuthField: accountId`. Private fields are restricted individually at field level — which is exactly why the public listing does not need to be a separate collection.

#### `Credential`
`accountId` (owner), `clinicianId`, `type` (enum `degree,diploma,certification,registration,licence`), `institution`, `qualificationName`, `yearAwarded` (number), `registrationNumber`, `document` (`blob`), `reviewStatus` (enum `pending,approved,rejected`, **admin write only**), `reviewNote` (**admin write only**), `reviewedAt`.

Access: owner-only, **except** `type`, `institution`, `qualificationName`, `yearAwarded` which are `accessRead: allow` so approved credentials appear on the public profile. `document` is always `accessRead: restrict` — supporting documents are never public.

#### `Availability`
The clinician's calendar input, merged into one collection because to a clinician it is one idea: "when I work".

`accountId` (owner), `clinicianId`, `kind` (enum `weekly,dayOff,extra`), `dayOfWeek` (number 0–6, `weekly` only), `date` (number, local-midnight epoch, `dayOff`/`extra` only), `startMinute`, `endMinute` (minutes from local midnight), `effectiveFrom`, `effectiveUntil`, `active` (boolean).

*Trade-off:* one `kind` field means a few columns are unused per row, in exchange for one collection instead of two and a UI that maps to it directly. Worth it.

#### `TimeSlot`
A single bookable time, generated by the worker from `Availability` (§6.3).

`clinicianId`, `accountId` (clinician, for access control), `startAt`, `endAt` (number), `status` (enum `open,held,booked,expired,cancelled`), `holdExpiresAt`, `appointmentId`, and `priceAmount` + `consultationMinutes` denormalised so a slot renders without a join.

Access: `accessRead: allow` (visitors must see availability), create/delete worker-only, `accessUpdate: restrict` — see REQ-BOOK-2 for why holds are worker-mediated.

#### `Appointment`
| Field | Type | Notes |
| --- | --- | --- |
| `accountId` | string | **Comma-separated: patient + clinician.** `accessEnableMultipleOwners: true` |
| `patientAccountId`, `clinicianAccountId`, `clinicianId`, `timeSlotId` | string | |
| `startAt`, `endAt` | number | |
| `status` | string | enum per §6.5 |
| `intakeReason`, `intakeNotes` | string | Field-level read restricted to the two owners |
| `amount`, `platformFee`, `clinicianPayout` | number | integer cents |
| `currency` | string | |
| `paymentStatus` | string | enum `none,paid,refunded,partially_refunded,failed` — **worker write only** |
| `pinChargeToken`, `pinRefundToken`, `pinTransferToken` | string | **worker write only, read restricted** |
| `inviteToken`, `inviteRespondedAt`, `inviteExpiresAt` | | **worker write only, read restricted** |
| `meetingTokenPatient`, `meetingTokenClinician` | string | **worker write only; each readable only by its own party** |
| `zoomMeetingId`, `zoomJoinUrl`, `zoomStartUrl` | string | **worker write only, read restricted** |
| `patientAttendedAt`, `clinicianAttendedAt` | number | **worker write only** |
| `attendanceSource` | string | enum `link,zoom_report,manual` |
| `payoutStatus` | string | enum `pending,scheduled,paid,withheld` — **worker write only** |
| `createdAt`, `updatedAt` | number | |

#### `Attendance`
Append-only proof for disputes. `appointmentId`, `accountId` (both parties, for read access), `party` (enum `patient,clinician`), `source` (enum `link,zoom_report`), `occurredAt`, `ipHash`, `userAgent`, `zoomParticipantId`, `durationSeconds`. **Worker-write-only, owner-read. Never updated or deleted.**

#### `Review`
`accountId` (patient), `clinicianId`, `appointmentId`, `rating` (number 1–5), `comment`, `published` (boolean). Created only for `completed` appointments. Feeds the rating aggregation pipeline.

#### `Topic`
`name`, `slug`, `kind` (enum `specialisation,ailment`), `synonyms` (`multi`), `active`. Specialisations and ailments merged into one collection — to a patient they are the same thing ("what do you need help with?"), and the search treats them identically. Seeded by an admin script; `accessRead: allow`, writes blocked.

#### `Email`
The queue the worker drains. `toAccountId`, `toEmail`, `template`, `payload` (JSON string), `status` (enum `queued,sent,failed`), `sentAt`, `error`, `dedupeKey`. **Worker-only — `accessRead: block`.** The frontend never writes here.

### 5.4 Aggregation pipeline

**REQ-DATA-1** A Saasufy aggregation pipeline groups `Review` by `clinicianId` and maintains `ratingAverage` / `ratingCount` on the `Clinician` row, so no worker code maintains ratings.

---

## 6. Functional requirements

### 6.1 Clinician profile management

**REQ-PROF-1** The clinician dashboard uses `<model-input>` bound to `Clinician` fields so edits save and sync in realtime without a save button, wrapped in a `<render-group>` so the form appears only once fully loaded.
**REQ-PROF-2** The topic picker is an `<input-provider>` bound to a `<collection-viewer>` over `Topic`, combined via `<input-combiner>` into the `topics` multi field. Specialisations and ailments share one picker, filtered by `kind`. The picker must enforce the 8-topic cap (REQ-SEARCH-3).
**REQ-PROF-3** Profile edits take effect in search immediately — the query matches the practitioner's own fields, so there is nothing derived to rebuild and no consistency window. The worker's only remaining denormalisation here is copying `priceAmount` / `consultationMinutes` onto future `TimeSlot` records so a slot renders without a join.
**REQ-PROF-4** A profile-completeness checklist shows, via `<if-group>`, exactly which of the REQ-CLIN-4 conditions are still unmet.

### 6.2 Credentials

**REQ-CRED-1** Credentials are added with `<collection-adder-form>` including the `blob` document field.
**REQ-CRED-2** Submitting a credential sets `reviewStatus = 'pending'` and moves the profile to `listingStatus = 'pending_review'`.
**REQ-CRED-3** The public profile lists approved credentials only (institution, qualification, year). Documents are never publicly readable.
**REQ-CRED-4** On approval or rejection, the worker queues an email to the clinician.

### 6.3 Availability

**REQ-AVAIL-1** The clinician sets weekly recurring availability (`Availability`) in their local timezone, plus date-specific blackouts and one-off openings (`Availability`).
**REQ-AVAIL-2** The worker materialises `TimeSlot` records on a **rolling 60-day horizon**, running at least hourly:
  - expands active rules into concrete slots of `consultationMinutes` length, converting local time to UTC using the clinician's IANA timezone (DST-correct);
  - removes `blocked` exception ranges; adds `extra` ranges;
  - never deletes or modifies a slot whose status is `held` or `booked`;
  - marks past `open` slots as `expired`.
**REQ-AVAIL-3** Materialisation is idempotent and keyed on `(clinicianId, startAt)`.
**REQ-AVAIL-4** A minimum booking lead time of **2 hours** applies; slots starting sooner are not offered.
**REQ-AVAIL-5** The clinician's calendar view renders slots via `<collection-viewer>` and updates in realtime as bookings arrive.

> **Rationale for materialised slots:** Saasufy views filter with one indexed operation (`equals` or `between`) plus a second-phase query. Searching "clinicians free next Tuesday afternoon" against recurrence *rules* would require computation Saasufy cannot do. Concrete slot records make availability a `between` range query on an indexed `startAt`, which is exactly what the view engine is built for.

### 6.4 Search and discovery

#### 6.4.1 How search works

Saasufy filters a view in two phases: `transformIndex` + `transformIndexOperation`
narrows the corpus using a real index (one indexed operation per view), then
`transformFilterQuery` runs as an unindexed filter over what phase 1 returned.

**`searchView` indexes `listingStatus`.** Phase 1 selects the practitioners an
admin has marked `listed`; phase 2 filters those on ordinary fields — region,
topics, price, language, name:

```json
{
  "name": "searchView",
  "paramFields": "listingStatus,query,sortBy",
  "primaryFields": "listingStatus",
  "transformIndex": "listingStatus",
  "transformIndexOperation": "equals",
  "transformIndexOperationInputA": "$paramFields.listingStatus",
  "transformFilterType": "advanced",
  "transformFilterQuery": "$paramFields.query",
  "transformOrderByField": "$paramFields.sortBy",
  "maxOffset": 500
}
```

**REQ-SEARCH-1 · One field controls visibility.** Setting `listingStatus` to
`listed` puts a practitioner in search immediately; setting it to anything else
removes them immediately. There is nothing derived to rebuild and no window in
which the index disagrees with the record.

**REQ-SEARCH-2 · No derived search fields.** The query runs against the fields
practitioners actually edit (`region`, `country`, `topics`, `priceAmount`,
`languages`, `displayName`). Case-insensitive matching uses the query language's
`(?i)` prefix rather than a lowercased duplicate of each field.

**REQ-SEARCH-3 · Filters compose the phase-2 query.** Controls are
`input-provider` elements whose values are query fragments — `region = nsw`,
`topics contains (?i)naturopathy` — fed to `collection-view-params`. Query syntax
is whitespace-strict (exactly one space between terms), so it must be built by a
single tested helper rather than concatenated ad hoc.

**REQ-SEARCH-4 · An empty query is valid** and returns every listed practitioner.

**REQ-SEARCH-5 · Slots are fetched per result.** Each rendered card queries
`TimeSlot` by `clinicianId` for its availability, so the search corpus stays one
row per practitioner.

**REQ-SEARCH-6 · Sorting and pagination.** `transformOrderByField` takes a client
supplied `sortBy`; `maxOffset` is capped at 500 and the UI refines filters rather
than paging deeply. `auto-reset-page-offset` returns to offset 0 when a filter
changes.

**REQ-SEARCH-7 · Realtime scope.** `collection-view-primary-fields` is
`listingStatus`, so the view's realtime channel covers listed practitioners.

#### 6.4.2 What this trades away

`listingStatus` is deliberately low-cardinality, which is the one rule this design
breaks: phase 1 returns *every* listed practitioner and phase 2 scans them. Cost
is therefore proportional to the size of the listed directory rather than to the
number of matches.

That is the right trade at this stage — the directory is small, and correctness
and simplicity matter more than a scan that is measured in hundreds of rows. It
stops being the right trade somewhere in the low tens of thousands of listed
practitioners, and the signal will be search latency rising with directory size
rather than with result count.

**The escape hatch, when that day comes:** add a single indexed `multi` field
holding pre-joined lookup keys (`au-nsw`, `naturopathy`, `au-nsw|naturopathy`),
since Saasufy indexes `multi` fields element-wise, and point `transformIndex` at
it. That makes phase 1 proportional to matches. It also reintroduces derived data
that must be kept in sync with every profile edit — which is precisely the
complexity being removed here, and why it is deferred until the numbers justify
it rather than adopted up front.

#### 6.4.3 Who may change the listing status

`Clinician` carries a `groupId` pinned to the admin group by `defaultValue`, with
the field create- and update-blocked for clients so a practitioner cannot repoint
it. The model then declares **two** auth pairs for read and update:

```
accessTokenAuthField / accessModelAuthField              = accountId / accountId
accessReadTokenAuthField / accessReadModelAuthField      = groupMemberships / groupId
accessUpdateTokenAuthField / accessUpdateModelAuthField  = groupMemberships / groupId
```

An action-specific pair is an **alternative** to the general pair, not a
replacement — for a given action, access is granted if *either* matches. So a
practitioner continues to edit their own profile via `accountId`, and a member of
the admin group can edit any profile via `groupId`. The same applies to read,
which means reviewers can also see the fields marked field-level `restrict`
(`contactEmail`, payout details) that were previously owner-only.

**REQ-LIST-1** A reviewer sets `listingStatus` from the admin screen; the change
takes effect in search immediately (§6.4.1).

**REQ-LIST-2** Admin-group membership grants edit rights over the *whole*
`Clinician` record, not just `listingStatus`. Field-level `restrict` reuses the
same per-action check, so a single field cannot be given a narrower owner than the
rest of the record. If one field ever needs to be admin-only while the rest stay
owner-editable, it has to move to its own model.

### 6.5 Booking and payment

The booking state machine:

```
 [patient picks slot]
        │
        ▼  slot: open → held (worker, 15 min TTL)
  pending_payment ──── payment fails / hold expires ──▶ expired  (slot → open)
        │ payment succeeds
        ▼  slot: held → booked
  pending_clinician ── clinician declines ──▶ declined ──▶ full refund
        │            └─ 24h no response ───▶ expired  ──▶ full refund
        │ clinician accepts
        ▼
   confirmed ──── cancellation (see §6.9) ──▶ cancelled_*
        │ meeting time passes
        ▼
  completed | no_show_patient | no_show_clinician | disputed
```

**REQ-BOOK-0 · Card capture.** Card details are collected with **Pin.js hosted fields** on the `/checkout` page and exchanged in the browser for a single-use card token. **Raw card numbers never touch our frontend, our worker or Saasufy**, which keeps the platform in PCI DSS SAQ-A scope. Only the card token is sent to the worker, and only the resulting `pinChargeToken` is persisted.
**REQ-BOOK-1** The patient selects a slot and enters intake details while anonymous; pressing **Confirm booking** triggers the Keycloak flow if not authenticated (REQ-PAT-3).
**REQ-BOOK-2** On confirmation the frontend creates an `Appointment` with `status = 'pending_payment'` referencing the slot. The worker observes it, atomically transitions the slot `open → held` with a 15-minute `holdExpiresAt`, and creates a Pin Payments charge against the patient's card token. **If the slot is not `open`, the worker sets the appointment to `expired` and the UI shows "just taken".** The slot transition is worker-mediated precisely so two concurrent patients cannot both win.
**REQ-BOOK-3** Payment is **captured immediately** at booking, per the requirement that the patient pays up front. A decline or expiry before acceptance produces an automatic **full refund**.
  - *Noted alternative:* Pin supports authorisation-only charges via `capture: false`, captured later through `PUT /charges/:token/capture`. This would avoid refund churn, but **Pin authorisations expire after exactly seven days**, which fails for any booking made further ahead than that — and partial capture is not supported. Immediate capture remains the correct MVP choice.
**REQ-BOOK-4** The patient's card is charged `amount = priceAmount`. The platform retains `platformFee` (MVP: **15%**, configurable per-clinician later); `clinicianPayout = amount - platformFee`. Pin Payments' processing fees and any third-party transfer fee are borne by the platform out of its share, not deducted from the clinician.
**REQ-BOOK-5** On successful payment the worker sets `paymentStatus = 'paid'`, `status = 'pending_clinician'`, slot `→ booked`, generates `inviteToken` and both meeting tokens, sets `inviteExpiresAt = min(now + 24h, startAt - 2h)`, creates the Zoom meeting, and queues the invite email.
**REQ-BOOK-6** Pin Payments webhooks are the authoritative payment signal; the worker never infers success from a client-side callback.
**REQ-BOOK-7** All prices shown to patients are inclusive of the platform fee — the patient sees one number.

### 6.6 Clinician invitation, accept / reject

**REQ-INV-1** The clinician receives an email containing a single-use link `https://mynaturalclinic.com/invite/:inviteToken`, showing patient first name, reason for visit, date/time in the clinician's timezone, duration, and payout amount.
**REQ-INV-2** The clinician can **Accept** or **Decline**, with an optional decline reason. The same actions are available on the clinician dashboard for any `pending_clinician` appointment — the email link is a convenience, not the only path.
**REQ-INV-3** Accept ⇒ `status = 'confirmed'`; the worker queues confirmation emails with the meeting link to both parties and a calendar `.ics` attachment.
**REQ-INV-4** Decline or expiry ⇒ `status = 'declined'` / `'expired'`, full refund issued, slot released to `open`, Zoom meeting deleted, patient emailed with an apology and a link to similar clinicians.
**REQ-INV-5** Repeated declines are tracked; a clinician declining more than **3 of their last 10** invites is flagged for admin review (MVP: flag only, no automatic action).

### 6.7 Meeting link, attendance and video

**REQ-MEET-1** Each party receives a **distinct** link under the platform domain: `https://mynaturalclinic.com/m/:meetingToken`. Tokens are ≥ 128 bits of entropy, single-appointment, single-party, and are never shown to the other party. Attendance is attributable because the tokens differ.
**REQ-MEET-2** Opening the link:
  - **Before `startAt - 10 min`:** shows a countdown and the appointment details. No Zoom URL is revealed.
  - **Within the attendance window (`startAt - 10 min` to `endAt + 15 min`):** the worker records a `Attendance` record (`source = 'link'`), stamps `patientAttendedAt` / `clinicianAttendedAt` if not already set, and redirects to the Zoom join URL (`zoomStartUrl` for the clinician as host, `zoomJoinUrl` for the patient).
  - **After the window:** shows a summary and, for the patient, a review prompt.
**REQ-MEET-3** The link must work for a logged-out browser — the clinician may open it from an email on a phone. The token alone authorises the redirect. It grants nothing except joining that one meeting.
**REQ-MEET-4** **Link clicks prove the link was opened, not that the consultation happened.** For dispute resolution the worker additionally polls the Zoom past-participants report 30 minutes after `endAt` and writes `Attendance` records with `source = 'zoom_report'`, including actual join/leave durations. **Where the two disagree, the Zoom report is authoritative.** This is the record used to settle disputes.
**REQ-MEET-5** Completion rules evaluated 30 minutes after `endAt`:
  - both parties attended ⇒ `completed`
  - clinician attended, patient did not ⇒ `no_show_patient` (clinician is still paid — their time was reserved)
  - patient attended, clinician did not ⇒ `no_show_clinician` (patient fully refunded, no payout)
  - neither attended ⇒ `no_show_patient`, patient refunded 50%, no payout
**REQ-MEET-6** Zoom meetings are created server-to-server with `join_before_host` disabled, waiting room enabled, and a unique passcode embedded in the join URL. Meeting URLs are never publicly readable in Saasufy — field access is restricted to the two owners.

### 6.8 Payouts

Payouts use the Pin Payments **Recipients** and **Transfers** APIs. Patient card payments settle into the platform's own Pin merchant balance, and the platform then transfers each clinician's share to their bank account. This differs materially from a Connect-style model and imposes three obligations:

**REQ-PAY-1 · Manual settlement schedule.** Pin's automatic daily transfer of the merchant balance to the platform's own bank account **must be switched to Manual in the Pin dashboard**. Otherwise the balance is swept nightly and there are no funds left to transfer to clinicians. This is a one-time configuration step and a launch blocker — if it is missed, every payout fails.

**REQ-PAY-2 · We are the ledger.** Pin's own documentation is explicit that tracking what each seller is owed is the platform's responsibility. `Appointment.clinicianPayout` plus `payoutStatus` is that ledger, and it is authoritative. The worker must be able to reconstruct, for any clinician and any date range, the set of appointments that are unpaid, scheduled and paid.

**REQ-PAY-3 · Float.** Refunds and transfers both draw on the same Pin balance, and Pin returns **HTTP 402 when a refund exceeds the available balance**. The platform must therefore retain a working float rather than transferring out the full balance, and the worker must treat a 402 on a refund as a retryable alert-worthy condition, never as a permanent failure. Transfers are scheduled only against funds that have actually settled.

**REQ-PAY-4** A clinician cannot be listed without an `active` payout recipient (REQ-CLIN-6).
**REQ-PAY-5** An appointment becomes payable when `status ∈ {completed, no_show_patient}` **and** attendance for the clinician is recorded.
**REQ-PAY-6** Payout is released after a **24-hour settlement delay** from the meeting end, to allow disputes to be raised. `payoutStatus`: `pending → scheduled → paid`.
**REQ-PAY-7** Transfers are batched — the worker runs a payout pass on a fixed schedule (MVP: daily) rather than transferring per appointment, since Pin permits transfers as often as daily and batching reduces both fees and failure surface. Each transfer records its Pin transfer token against every appointment it covers.
**REQ-PAY-8** A dispute raised within the settlement window sets `status = 'disputed'` and `payoutStatus = 'withheld'`, pending manual admin resolution. MVP dispute resolution is manual, informed by `Attendance`.
**REQ-PAY-9** The clinician dashboard shows upcoming, scheduled and paid earnings, derived with `<collection-reducer>` over their appointments.

### 6.9 Cancellations and refunds

**REQ-CANCEL-1** Patient cancels **> 24 h** before start ⇒ full refund. **< 24 h** ⇒ 50% refund. Slot is released to `open` in both cases.
**REQ-CANCEL-2** Clinician cancels at any time ⇒ full refund to the patient, slot removed, patient emailed. Repeated clinician cancellations are flagged for admin review.
**REQ-CANCEL-3** Cancellation is confirmed through `<confirm-modal>` and always shows the exact refund amount before the patient commits.
**REQ-CANCEL-4** All refunds are executed by the worker through the Pin Payments refunds API and recorded in `paymentStatus`. Pin returns refunds as **`pending`** and confirms the outcome asynchronously by webhook, so a refund is never treated as succeeded on the API response alone.

### 6.10 Notifications

**REQ-NOTIF-1** Transactional emails required for the MVP: clinician credential approved/rejected; booking invite to clinician; booking confirmed (both parties, with `.ics`); booking declined/expired + refund notice to patient; reminder 24 h before (both); reminder 1 h before with the meeting link (both); post-meeting review request to patient; payout sent to clinician; cancellation notices.
**REQ-NOTIF-2** Every email is enqueued as an `Email` record with a `dedupeKey`, so a redelivered subscription event cannot send a duplicate.
**REQ-NOTIF-3** Sending failures retry with exponential backoff up to 5 attempts, then set `status = 'failed'` for admin visibility.
**REQ-NOTIF-4** SMS and push notifications are out of scope for the MVP.

---

## 7. Frontend

### 7.1 Structure

A static site. `index.html` holds a single `<socket-provider url="wss://saasufy.com/sid8016/socketcluster/">` wrapping one `<app-router>`; each page is a `<template slot="page">`. No build step, no framework — Saasufy components plus a small amount of vanilla JS confined to: query-string building (REQ-SEARCH-18), timezone rendering, and `sessionStorage` handling of the held booking selection.

### 7.2 Routes

| Route | Page | Access |
| --- | --- | --- |
| `` | Home / hero search | Public |
| `/search` | Results with filters | Public |
| `/clinician/:profileId` | Public profile, credentials, availability calendar | Public |
| `/book/:slotId` | Intake details + booking summary | Public until confirm |
| `/auth/callback` | `<oauth-handler>` | Public |
| `/checkout/:appointmentId` | Card payment via Pin.js hosted fields | `no-auth-redirect` |
| `/booking/:appointmentId` | Status, meeting link, cancel | `no-auth-redirect` |
| `/patient` | My appointments | `no-auth-redirect` |
| `/clinician` | Dashboard: profile, credentials, availability, invites, earnings | `no-auth-redirect` |
| `/clinician/onboarding` | Guided setup checklist | `no-auth-redirect` |
| `/admin/reviews` | Credential review queue | `no-auth-redirect`, admin-gated |

The meeting link `/m/:token` and invite link `/invite/:token` are **served by the worker, not by `app-router`**, because they must work without a Saasufy session and must perform a server-side redirect.

### 7.3 Component usage rules

**REQ-FE-1** Prefer declarative Saasufy components over JavaScript everywhere. Custom JS is permitted only for the three cases named in §7.1.
**REQ-FE-2** Wrap multi-source pages in `<render-group>` to avoid staged content flashes.
**REQ-FE-3** Use `<switch-group>` for the appointment status display so every state in §6.5 has an explicit branch, including error states.
**REQ-FE-4** Keep `collection-fields` on outer viewers minimal and delegate volatile fields to nested `<model-text>` / `<model-input>`, to avoid re-render churn and input focus loss.
**REQ-FE-5** Every `collection-viewer` must slot a `no-item`, a `loader` and an `error` template. No blank screens.
**REQ-FE-6** Set at most one `collection-view-primary-fields` entry per viewer for realtime channel efficiency.

---

## 8. Non-functional requirements

**REQ-NFR-1 · Security.** No secret may appear in client-served files. Pin Payments/Zoom/email credentials live only in the worker. The Saasufy admin API key stays in `.saasufy-api-key`, gitignored, never shipped.
**REQ-NFR-2 · Data protection.** Intake reason and notes are health information. They are readable only by the two appointment owners, are excluded from all public views, and are never placed in email bodies beyond the patient's stated reason in the clinician invite.
**REQ-NFR-3 · Credential documents** are never publicly readable and are served only through access-restricted `blob` fields.
**REQ-NFR-4 · Timezones.** Every stored instant is UTC epoch ms. Every displayed time is localised. DST correctness in slot materialisation is a release blocker.
**REQ-NFR-5 · Money.** Integer cents only, single currency (AUD) for the MVP.
**REQ-NFR-6 · Idempotency.** Every worker action is idempotent (REQ-ARCH-2). **Pin Payments does not offer idempotency keys**, so duplicate-charge protection must be enforced on our side: the worker only creates a charge when `pinChargeToken` is empty, and writes the token back before acknowledging the work. The same guard applies to refunds and transfers.
**REQ-NFR-7 · Auditability.** `Attendance` and `Email` are append-only.
**REQ-NFR-8 · Availability.** Worker downtime must degrade gracefully: browsing, searching and viewing continue to work; only side effects queue. Bookings created during worker downtime resolve when it returns, bounded by the 15-minute hold TTL.
**REQ-NFR-9 · Performance.** Search results render in < 1.5 s on a warm connection, and that figure must hold at **10× the projected clinician count** — search cost is proportional to matching clinicians and page size, never to platform size (§6.4). Indexes are inventoried in §6.4.6. A load test at 10× seeded data is a launch gate, not a nice-to-have.
**REQ-NFR-10 · Compliance.** Clinicians must accept terms asserting they hold valid registration and insurance. The platform displays a "not a substitute for emergency care — call 000" notice on booking and profile pages. The platform is explicitly an introduction service, not a provider of clinical care.

---

## 9. Out of scope for the MVP

In-person appointments · multi-currency · recurring appointment packages · in-app chat/messaging · clinician-to-clinician referrals · insurance/Medicare claiming · prescriptions or clinical records · mobile apps · automated credential verification against registries · admin UI beyond the credential queue · SMS/push · group sessions · waitlists · promo codes · calendar sync (Google/Outlook) beyond `.ics` attachments.

---

## 10. Assumptions made

1. **Companion worker: confirmed.** Nothing in Saasufy can send an email, charge a card, create a Zoom meeting or run on a schedule, so a companion backend is unavoidable. It will be a **custom Node.js server, self-hosted** — decided, see §2.4.
2. **Pin Payments** for card charges, refunds and clinician payouts (Recipients + Transfers). Pin serves **Australia and New Zealand only**, which is consistent with the single-market MVP but is a hard constraint on expansion.
3. **Zoom** server-to-server OAuth app, platform-owned; clinicians do not need their own Zoom accounts.
4. **Single currency AUD**, single market (Australia) for the MVP.
5. **15% platform fee.**
6. **Immediate capture with refund-on-decline** rather than authorisation-and-capture (REQ-BOOK-3).
7. **Manual credential review** by an admin.
8. `mynaturalclinic.com` is the production domain hosting the frontend and the worker's `/m`, `/invite` and `/pin/webhook` routes.
9. Consultations are **online video only**.

---

## 11. Open decisions needed

| # | Decision | Default if unanswered |
| --- | --- | --- |
| D-1 | Platform fee percentage | 15% |
| D-2 | Invite response window | 24 h, capped at `startAt - 2h` |
| D-3 | Late-cancellation refund | 50% within 24 h |
| ~~D-4~~ | ~~Payment provider~~ | **Resolved:** Pin Payments |
| D-5 | Email provider | Postmark (better transactional deliverability than SES for this volume) |
| ~~D-6~~ | ~~Worker hosting~~ | **Resolved:** custom Node.js server, self-hosted (§2.4) |
| D-7 | Should clinicians set per-slot pricing, or one price per clinician? | One price per clinician for the MVP |
| D-8 | Funds are necessarily held in the platform's own Pin merchant balance between charge and transfer (§6.8). Confirm with an Australian financial-services lawyer whether the delay and the platform's role require an AFSL or fall within an intermediary exemption. | Delay the transfer, hold no longer than necessary — but this needs legal sign-off, not a default |

---

## 12. Principal risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| **Double-booking** under concurrency | High | All slot transitions are worker-mediated and single-writer (REQ-BOOK-2); slot status is never written by the browser |
| **Attendance is forgeable by link click** | High — it gates payouts | Zoom participant report is authoritative for disputes (REQ-MEET-4) |
| **Worker is a single point of failure** for all side effects, and self-hosting means we own its uptime | High | Idempotent reconciler design; boot-time reconciliation (REQ-ARCH-8); graceful degradation (REQ-NFR-8); hold TTL bounds damage. Note REQ-ARCH-7 — the fix is *not* simply running a second instance |
| **Clinician declines after patient is charged** | Medium — poor patient experience | Automatic full refund within minutes; decline-rate flagging (REQ-INV-5) |
| **DST / timezone errors** in slot materialisation | High — missed appointments | IANA-timezone-aware expansion; explicit DST test cases as a release blocker |
| **Regulatory exposure** from health data and clinical claims | High | Field-level access control on intake data; explicit introduction-service positioning (REQ-NFR-10); legal review before launch |
| **Saasufy query syntax is whitespace-strict** | Medium — silent wrong results | Single tested query-builder helper (REQ-SEARCH-18) |
| **Search degrades as the marketplace grows** | Medium — accepted deliberately; the scan is proportional to the listed directory (§6.4.2) | Revisit at low tens of thousands of listed practitioners; the escape hatch is an indexed `multi` key field, documented in §6.4.2 |
| **Pin balance too low to refund** — refunds and clinician transfers draw on the same balance and Pin returns 402 | High — a patient owed a refund does not get one | Retain a working float (REQ-PAY-3); treat 402 as retryable and alert; never sweep the balance to zero |
| **Manual settlement schedule not set** in the Pin dashboard | High — every payout fails silently at launch | Launch checklist item (REQ-PAY-1); worker logs a loud startup warning if the balance is repeatedly zero |
| **We hold clinicians' funds** in our own merchant balance, and we perform their KYC | High — financial-services and fraud exposure | Minimise hold time (REQ-PAY-6); manual identity check at credential review (REQ-CLIN-6); legal sign-off on D-8 before launch |
| **No idempotency keys in the Pin API** — a retry can double-charge | High | Token-presence guards before every charge, refund and transfer (REQ-NFR-6) |
| **Pin is AU/NZ only** | Medium — caps expansion | Accepted for the MVP; isolate payment calls behind one module so the provider can be swapped |
| **Cold-start marketplace** (no clinicians ⇒ no patients) | High, commercial | Out of technical scope; seed supply before opening demand |

---

## 13. Definition of done for the MVP

A single end-to-end pass must succeed on production infrastructure:

1. A clinician registers via Keycloak, verifies email, completes their profile, uploads a credential, submits payout bank details, and publishes availability.
2. An admin approves the credential; the clinician becomes `listed`.
3. An anonymous visitor searches by ailment and date range and finds that clinician.
4. The visitor selects a slot, enters intake details, signs up via Keycloak at the confirm step, and pays.
5. The clinician receives the invite email and accepts.
6. Both parties receive confirmations with distinct platform meeting links.
7. Both open their links at the appointment time, are redirected into Zoom, and attendance is recorded for both.
8. 30 minutes later the appointment is `completed`, reconciled against the Zoom participant report.
9. 24 hours later the payout is transferred and the clinician is notified.
10. The patient leaves a review and the clinician's rating updates in realtime.

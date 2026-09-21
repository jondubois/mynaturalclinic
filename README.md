# MyNaturalClinic

A marketplace for booking online consultations with natural therapists and clinicians.

Patients search for practitioners by name, location, ailment or specialisation, and — most importantly — by when they are actually free. Browsing and searching require no account; a patient only signs up at the moment they commit to a booking. Clinicians go through a deliberately higher-friction path: they register, verify their email, submit academic and professional credentials for review, and publish their availability as a recurring weekly calendar with date-specific exceptions.

The platform intermediates the relationship rather than just introducing the two parties. It issues the appointment invitation to the clinician, hosts the meeting link under its own domain, records attendance for both sides, takes the patient's payment up front, and releases funds to the clinician only once the consultation has actually taken place. That attendance record is what settles disputes.

## How it's built

The frontend is a static site built almost entirely from [Saasufy](https://saasufy.com) components — `app-router`, `collection-viewer`, `model-input` and friends — talking directly to a Saasufy service over WebSocket, with realtime sync and access control enforced at the data layer. There is no build step and no framework. Accounts are handled by the shared Saasufy Keycloak instance via Saasufy's OAuth components.

Saasufy has no server-side code, so everything with an external side effect — sending email, charging cards through Pin Payments, creating Zoom meetings, expiring holds, materialising availability slots, releasing payouts — lives in a companion self-hosted Node.js server. It is written as a reconciler rather than an API: the frontend writes intent into Saasufy, and the worker observes the change and acts on it. The browser never calls the worker for booking logic.

## Repository

| Path | |
| --- | --- |
| `requirements.md` | Full MVP specification — data model, booking state machine, access control, open decisions |
| `.saasufy-api-key` | Saasufy admin credential (gitignored) |
| `.saasufy-service-url` | Deployed Saasufy service endpoint |

Start with [`requirements.md`](requirements.md).

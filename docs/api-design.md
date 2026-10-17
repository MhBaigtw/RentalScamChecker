# API design

FastAPI app in `apps/api/rentalscamchecker/`. The interactive OpenAPI docs are served at
`/docs` when the API is running (`http://127.0.0.1:8100/docs`).

## Error shape

Every error, from every endpoint, uses one shape (SPEC 9):

```json
{"error": {"code": "validation_error", "message": "Some fields are missing or invalid.",
           "details": {"fields": [{"field": "monthly_rent", "message": "Must be greater than zero."}]}}}
```

| Status | `code` | When |
|---|---|---|
| 404 | `not_found` | unknown check id, unknown route |
| 405 | `method_not_allowed` | wrong HTTP method |
| 422 | `validation_error` | any invalid input: form fields, photos, path parameters. `details.fields` lists **every** problem, not just the first |
| 500 | `internal_error` | unhandled exception (logged with traceback; nothing internal is returned) |
| 503 | `database_unavailable` | `/health` when the DB is down |

Implemented in `errors.py`: `ApiError` for errors we raise; handlers convert FastAPI's
`RequestValidationError`, Starlette's `HTTPException` and any other exception into the shape.

## `POST /checks`

`multipart/form-data`. Returns `202 {"check_id": "<uuid>"}`. 202 rather than 201 because the
check has been accepted but not processed: the signals run in the background (worker, M2). The
signal and aggregate jobs are enqueued in the same transaction as the check.

| Field | Required | Rules |
|---|---|---|
| `listing_text` | yes | trimmed, 1 to 20 000 chars. Phone numbers and emails in it are extracted (linked like the contact fields) and replaced by `[phone]` / `[email]` before storing (ED-016) |
| `monthly_rent` | yes | dollars; accepts `1850`, `1,850.00`, `$1850`; > 0, ≤ 20 000, ≤ 2 decimals; stored as cents |
| `bedrooms` | yes | integer 0 to 10 (0 = studio) |
| `unit_type` | no | `whole_unit` (default), `room` (a room in a shared place) or `basement`. Rooms aren't compared with CMHC prices (M3) |
| `address_text` | yes | 3 to 300 chars |
| `city` | yes | 2 to 100 chars |
| `submitted_url` | no | `http(s)://` URL, ≤ 2000 chars. Stored as text, **never fetched** |
| `contact_phone` | no | must parse as a valid number (country `CONTACT_DEFAULT_REGION`, default CA, if no `+` prefix); normalized to E.164 then hashed |
| `contact_email` | no | loose shape check; trimmed and lowercased, then hashed |
| `photos` | yes, 1 to 10 | JPEG, PNG or WebP; ≤ 10 MB each; each side ≤ 8000 px; ≤ 40 MP. Errors name the photo: `photos.2` |

Blank optional fields are treated as absent. Each photo is decoded, rotated upright per its EXIF
orientation, then re-encoded as a JPEG (quality 90) **without** EXIF/ICC metadata.

Order of work: validate every field and decode every photo (nothing written yet) → write the
re-encoded files to storage → insert the check, photos and contact hashes in one transaction.
If the insert fails, the files just written are deleted.

## `GET /checks/{check_id}`

`200` with:

```json
{
  "id": "…", "status": "pending", "created_at": "…", "risk_level": null, "scoring_version": null,
  "listing": {"submitted_url": null, "listing_text": "…", "rent_cents": 165000, "bedrooms": 2,
              "address_text": "1430 Trafalgar Rd", "city": "Oakville"},
  "photos": [{"id": "…", "position": 0, "width": 640, "height": 480}],
  "contacts": [{"kind": "phone", "source": "field"}, {"kind": "phone", "source": "text"}],
  "signals": [],
  "related": null
}
```

`status` moves `pending` (accepted, no signal started) → `running` (first signal job started) →
`complete` (aggregator finished). `signals` lists each finished signal's result; a signal that
failed permanently or didn't finish before the check timeout appears with `status: "error"`.
`risk_level` is `"incomplete"` when any signal errored, otherwise `null` until scoring exists (M7).
Clients poll this endpoint until `status` is `complete`.

Contacts expose only kind and source (`field` or `text`): the hash is internal and the raw value
no longer exists. `listing.listing_text` is the redacted text.
Photo bytes aren't served yet (the report UI in M7 decides whether it needs them).

`related` (M6, ED-026) is `null` until `status` is `complete`, then lists other checks linked to
this one through a matching photo or a shared phone/email, computed when the report is read (so an
earlier report also shows later checks):

```json
"related": {
  "explanation": "2 other checks share a photo, phone number or email with this one (2 for a different address). Following those links further, 3 checks are connected, including this one.",
  "next_step": "See the photo and contact results for what these links mean. Links at the same address are usually the same listing posted again.",
  "total": 2, "at_different_address": 2,
  "checks": [{"check": "3bf5864f09", "created_on": "2026-10-17", "city": "Brampton",
              "risk_level": null, "linked_by": ["phone", "photo"], "same_address": false}],
  "cluster_size": 3, "cluster_size_capped": false
}
```

Other checks are identified only by `check` (a `public_ref`), never by id: an id would let the
reader fetch that check. `checks` holds at most 50, newest first; `total` counts all.
`at_different_address` is counted over the listed checks. `cluster_size` stops at 500
(`cluster_size_capped: true` then means "at least").

## `GET /health`

`200 {"status": "ok", "database": "ok"}` or `503` `database_unavailable` (ED-008).

## `GET /internal/metrics` (M3, ED-023)

Operators only. With `INTERNAL_METRICS_TOKEN` set: `Authorization: Bearer <token>` required
(401 `unauthorized` otherwise). Without it: open in development/test, `404` in production.

```json
{"jobs_by_status": {"queued": 0, "running": 0, "done": 12, "failed": 0, "dead": 0},
 "workers_alive": 1, "abandoned_threads_alive": 0, "jobs_timed_out_total": 0,
 "workers": [{"worker_id": "host:1234:ab12cd34", "alive": true, "last_seen_seconds_ago": 2.1,
              "concurrency": 2, "jobs_done_total": 12, "jobs_failed_total": 0,
              "jobs_timed_out_total": 0, "abandoned_threads_alive": 0, "...": "..."}]}
```

## Not yet

Rate limits and request-size limits (M8), `POST /checks/{id}/outcome` and `/dispute` (M8),
versioning (unversioned until there is a second client).

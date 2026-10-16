# API design

FastAPI app in `apps/api/rentalscamchecker/`. The interactive OpenAPI docs are served at
`/docs` when the API is running (`http://localhost:8100/docs`).

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
  "signals": []
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

## `GET /health`

`200 {"status": "ok", "database": "ok"}` or `503` `database_unavailable` (ED-008).

## Not yet

Rate limits and request-size limits (M8), `POST /checks/{id}/outcome` and `/dispute` (M8),
`/internal/metrics` (later milestone), versioning (unversioned until there is a second client).

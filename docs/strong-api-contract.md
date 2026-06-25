# Strong → Garmin zero-touch sync — reverse-engineering notes

Goal: a scheduled job that pulls new Strong workouts (with full sets/reps/weights)
from Strong's private sync API and lands them on Garmin Connect, no manual export.

Source of the API contract below: the `strong-api-lib` Rust crate in
`tolik518/strong-api-workout-sync` (actively maintained into 2026; no cert-pinning /
"stopped working" issues; no forks). Only the **base host** is withheld there
(supplied via `STRONG_BACKEND` env var) — everything else is reproduced here.

## Strong API contract

Base URL: `https://<HOST>/`  — **UNKNOWN, must be captured once** (see capture step).

All requests mimic the Android app via headers:
- `user-agent: Strong Android`
- `x-client-platform: android`
- `x-client-build: 600013`   (may be stale — confirm during capture)
- `content-type: application/json`, `accept: application/json`

### Auth (JWT bearer)
- `POST {base}/auth/login`  body `{"usernameOrEmail": "...", "password": "..."}`
  → `{ accessToken, refreshToken, userId }`
- `POST {base}/auth/login/refresh`  body `{accessToken, refreshToken}` + `Authorization: Bearer <accessToken>`
  → new `{ accessToken, refreshToken }`

### Fetch workouts (paginated)
- `GET {base}/api/users/{userId}?continuation=<cursor>&limit=<n>&includes=log&includes=measurement`
  with `Authorization: Bearer <accessToken>`
- `includes` values: log, measurement, measuredValue, template, tag, folder, widget
- Need **log** (workouts) AND **measurement** (to resolve exercise names).

### Response shape (the parts we use)
```
UserResponse._embedded.log[]                      # each = one workout
  Log.name / startDate / endDate / timezoneId
  Log._embedded.cellSetGroup[]                    # each = one exercise
    CellSetGroup._links.measurement               # → exercise definition (name)
    CellSetGroup.cellSets[]                        # each = one set
      CellSet.isCompleted
      CellSet.cells[] -> Cell { cellType, value } # weight / reps / etc. as typed cells
```
Flattened (per `data_transformer.rs`):
`Workout{name,start_date,end_date,exercises[]}` → `Exercise{id,name,sets[]}` → `Set{weight,reps,rpe,is_completed}`

## Garmin side (low-risk, well-trodden)

`python-garminconnect` (cyberjunky):
- Mobile SSO auth (sso.garmin.com → diauth.garmin.com), token cache at
  `~/.garminconnect/garmin_tokens.json`, auto-refresh, self-heal on rejected tokens.
- MFA via `prompt_mfa` callback (one-time at setup; tokens persist after).
- Upload import-style FIT (`upload_activity`), download original FIT
  (`download_activity(..., ORIGINAL)`), `delete_activity(id)`.

## FIT encoding plan

Build a strength FIT: `sport=training`, `sub_sport=strength_training`,
session start = Strong `startDate`, elapsed = end−start. One `set` message per
Strong set: `set_type=active`, `repetitions`, `weight`, `category` +
`category_subtype`/`exercise_name` mapped from the Strong exercise name.
Python writer candidate: `fit_tool` (or Garmin FIT SDK Python).

Caveats:
- **Exercise-name mapping is lossy.** Garmin's `exercise_name`/`category` is a fixed
  enum; Strong allows custom/free-text exercises. Unmapped lifts fall back to a generic
  category — reps/weight still render, the specific name may not. Need a name→enum map
  with a sensible default.
- **RPE** has no native FIT field (tool had an "Add RPE back" issue) — drop or stuff in a note.

## Open decision (architectural) — THE SEAM

Garmin has **no API to merge** set data into an existing activity; an upload always
creates a NEW activity. So relative to any watch recording:
1. Standalone import — Strong is sole source, no live HR (simplest).
2. Replace — delete the watch's strength activity, upload Strong FIT in its place (loses HR).
3. Merge — splice watch HR (download original FIT) + Strong sets into one FIT, upload, delete original (full fidelity, most work).
→ Awaiting user choice. [[determines module layout]]

## Capture step (one-time, user device — collapses to "find the host")

Because the full contract is known, the only unknown is the base host. Route the phone
through mitmproxy, open Strong, trigger a sync, read the host of the `auth/login` /
`api/users/...` request (and confirm current `x-client-build`). Platform-specific cert
install (iOS vs Android) — TBD by user.

## Risk posture
- ToS-grey (personal data interop, own account, own credentials) — accepted by user.
- Lifetime license is the asset at risk → keep cron gentle (a few pulls/day, like the
  reference tool's cadence); never hammer the endpoint.
- Cert pinning not observed in the reference tool's issues, but confirm at capture.

## LIVE FINDINGS (2026-06-25) — verified against the real account

- **Host:** `https://<HOST>/` — not published here; discover it once via `docs/ios-capture.md`. Headers `user-agent: Strong Android`,
  `x-client-platform: android`, `x-client-build: 600013` are accepted (server doesn't
  gate on them); login + fetch both work. No MFA on Strong.
- **Logs query param is `include` (singular), repeated:** `?limit=&continuation=&include=log&include=measurement`.
- **Exercise names:** the user-endpoint embeds only a couple; fetch the full catalog from
  `GET api/measurements?page=N` (200/page, ~253 total, ~2 pages). Names are localized:
  `{"en": "Squat (Barbell)"}`. The log's `cellSetGroup._links.measurement.href` ends in the
  matching id. Custom (non-catalog) exercises stay unnamed → generic category (TODO: resolve
  via `api/users/{id}/measurements/{mid}`).
- **Set cells (UPPERCASE cellType):** `REPS`, `DURATION` (s), `BARBELL_WEIGHT`/`DUMBBELL_WEIGHT`/
  `OTHER_WEIGHT` (load), `WEIGHTED_BODYWEIGHT` (added), `ASSISTED_BODYWEIGHT` (assistance).
  `cellSetTag` = WARM_UP/DROP_SET/FAILURE (unused). **Weight unit = kg** (confirmed: every
  session opens with a 20.0 barbell warmup = the 20 kg Olympic bar).
- **Ordering: oldest-first, no sort param honored.** Pagination is forward via an opaque
  cursor in `_links.next` (`continuation=`). `api/logs/{id}` returns SPA HTML (dead).
  → Recent workouts = page forward with a **saved cursor** (incremental), filtered by lookback.

## ARCHITECTURE PIVOT (2026-06-25) — set-edit API, not FIT-merge

Garmin's `set_activity_exercise_sets(activity_id, payload)` (PUT
`/activity-service/activity/{id}/exerciseSets`, replace-all) edits a strength activity's sets
**in place**. The user's watch strength activities carry real per-set timing (ACTIVE/REST,
startTime, duration) but blank exercise data (`category:"UNKNOWN"`, `name:null`,
`repetitionCount:null`, `weight:null`) — ideal to enrich.

→ **Primary path:** match Strong workout ↔ watch activity, GET its exerciseSets, fill the
ACTIVE slots positionally with Strong's exercise (category/name STRINGS) + reps + weight,
PUT back. **No HR touched, no delete.** FIT-merge (`fit_merge.py`) is now only the fallback
for workouts with no matching watch activity (standalone import).

exerciseSets item shape: `{exercises:[{category, name, probability}], repetitionCount, weight,
setType:"ACTIVE"|"REST", startTime, duration, messageIndex, wktStepIndex}`.
**Weight unit in this payload = GRAMS** (confirmed 2026-06-25 by a reversible test PUT:
`weight:60000` displayed as 60.0 kg). So send `round(kg * 1000)`. Garmin validates
`category`/`name` against its FIT enum (400 "Invalid Sub-Category" on bad values; `name:null`
always OK under a known parent — `category:"BENCH_PRESS", name:null` verified accepted).
Strategy: send the mapped category (validated) + `name:null` for v1 reliability; enable
specific sub-category names later once the accepted string set is validated.

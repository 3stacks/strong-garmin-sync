# strong-garmin-sync

Bring your **[Strong](https://www.strong.app/)** workout data into **Garmin Connect** —
the exercises, sets, reps and weights — by enriching the strength activity your watch
already recorded, **in place**.

Garmin watches log a strength session's heart rate and per-set *timing*, but usually leave
the exercise, rep count and weight blank. Strong has all of that but no Garmin integration.
This tool fills the gaps: it matches a Strong workout to the corresponding Garmin activity
and writes the sets onto it via Garmin Connect's own "edit sets" API — **no second activity,
no deleting, heart rate and GPS untouched.**

> ⚠️ **Unofficial.** This uses Strong's private mobile API and an unofficial Garmin Connect
> client (see [Disclaimer](#disclaimer)). It's a personal-use data-portability tool. No
> affiliation with Strong or Garmin.

## How it works

```
Strong private API  ──fetch──▶  workouts (exercise, reps, weight)
Garmin Connect      ──list──▶   strength activities (HR + per-set timing)
                         │
                  match by start time
                         │
        GET the activity's exerciseSets ─▶ fill in exercise/reps/weight ─▶ PUT back
```

- **Match** a Strong workout to a Garmin activity by start-time proximity.
- **Enrich** in place with `set_activity_exercise_sets` (the API behind Connect's web "edit
  sets"). Two strategies, chosen automatically:
  - **Fill** — when the watch detected per-set slots, drop Strong's exercise/reps/weight
    into them (keeps the real per-set timing).
  - **Replace** — when the watch logged one undifferentiated block, synthesise one set per
    Strong set, spread across the session.
- **Exercise mapping** translates Strong exercise names to Garmin's FIT
  `category`/`name` enums (validated against the live API; falls back to category-only, or a
  generic category, when there's no exact match — reps/weight always render).
- Every write **backs up the activity's original sets first** and is fully reversible.

## Setup

Requires Python 3.10+.

```bash
git clone <this repo> && cd strong-garmin-sync
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then fill in your Strong + Garmin credentials
python garmin_login.py      # one-time: handles Garmin MFA, caches tokens
```

`STRONG_BACKEND` is **not included** — the Strong API host isn't published here. Discover it
yourself by intercepting the Strong app once (~10 min): see
**[docs/ios-capture.md](docs/ios-capture.md)**.

The scripts auto-relaunch under `./.venv` if present, so `python3 match_tui.py` works
without activating the venv.

### Configuration (`.env`)

| var | meaning |
|---|---|
| `STRONG_BACKEND` | Strong API base URL — not shipped; find it via [docs/ios-capture.md](docs/ios-capture.md) |
| `STRONG_USER` / `STRONG_PASS` | your Strong login |
| `STRONG_WEIGHT_UNIT` | `kg` or `lb` (the unit Strong stores) |
| `GARMIN_USER` / `GARMIN_PASS` | your Garmin Connect login |
| `MATCH_WINDOW_S` | max start-time gap to pair a workout↔activity (default 5400) |
| `LOOKBACK_DAYS` | how far back to scan (default 10) |

## Usage

### Matcher TUI (recommended)

```bash
python match_tui.py            # last 30 days   (--days 90 for more)
```

Two panes — **Strong workouts** (left) ↔ **Garmin strength activities** (right). Tab
switches panes, ↑/↓ move; selecting a Strong workout **auto-highlights the closest Garmin
activity** and shows the time delta. Press:

- **`m`** — enrich the cursored pair (backs up first)
- **`u`** — restore the cursored activity from its backup
- **`r`** — reload · **`q`** — quit

**Already-enriched rows are green with a ✓.**

### One-off CLI

```bash
python enrich_activity.py <activity_id>          # dry-run: show what would be written
python enrich_activity.py <activity_id> --put    # write it
python garmin_unit_test.py <activity_id> --restore   # revert from backup
python probe_strong.py        # sanity-check Strong fetch (prints recent workouts)
python garmin_inspect.py      # read-only: list recent strength activities + a set dump
```

## Layout

```
sgs/               reusable library
  strong_client.py   Strong private API client (login, paged fetch, parse)
  garmin_client.py   Garmin Connect wrapper (python-garminconnect)
  enrich.py          build the exerciseSets payload + enrich/restore helpers
  exercise_map.py    Strong name -> Garmin (category, name) enums
  matcher.py         workout <-> activity start-time matching
  models.py · config.py · state.py
  fit_merge.py       fallback: build a standalone strength .fit (no matching activity)
match_tui.py       the matcher TUI
docs/              reverse-engineering notes (Strong API contract, etc.)
```

## Limitations

- Strong's log endpoint returns oldest-first with no sort option, so reaching recent
  workouts pages forward each run (a cursor-resume optimisation is a TODO).
- A few exercises with no FIT equivalent map to a generic category (reps/weight still show).
- Weights are stored by Garmin in grams; the tool handles the conversion.

## Disclaimer

This project talks to Strong's **private, undocumented** API (reverse-engineered; the API
client structure is derived from [tolik518/strong-api-workout-sync](https://github.com/tolik518/strong-api-workout-sync))
and to Garmin Connect via the unofficial
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect). It's intended
for accessing **your own data** for personal interoperability. Using it may be against
Strong's or Garmin's Terms of Service, and either API may change or break without notice.
Provided as-is, no warranty. You are responsible for your own use. Not affiliated with,
endorsed by, or supported by Strong or Garmin.

## Credits

- [tolik518/strong-api-workout-sync](https://github.com/tolik518/strong-api-workout-sync) — Strong API reverse engineering
- [cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect) — Garmin Connect client
- [fit-tool](https://pypi.org/project/fit-tool/) — FIT encode/decode

## License

MIT

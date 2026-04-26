# Webinfo

A Django web app for running math-camp puzzlehunts. One instance can host
several concurrent hunts; organizers configure them through Django admin
and CSV imports, while teams play through a phone-friendly web UI.

For the full multi-phase delivery plan see [`PLAN.md`](PLAN.md). This
README is the operational snapshot — what exists today and how to use it.

## Tech stack

- **Python / Django 5** (single project, two apps)
- **SQLite** for development; PostgreSQL is the planned target for prod
- **Plain Django templates** with hand-written CSS in `templates/base.html`
  (no JS framework; one small admin helper script under
  `hunts/static/hunts/admin/`)

## Project layout

```
webinfo/         Django project (settings, root URLconf, WSGI)
accounts/        Team auth + Team model + login/logout views
hunts/           Game logic: hunts, puzzles, hints, attempts, scoring,
                 CSV import, admin customisations
templates/       Project-wide base template
csv_templates/   Example CSV files + their format docs
manage.py
db.sqlite3       Local dev database
```

Apps in detail:

- `accounts/` — `Team` model (one row per team-per-hunt, password
  hashed). Custom auth uses a signed cookie (see below); no Django
  `User` is involved on the team side.
- `hunts/` — `Puzzlehunt`, `Puzzle`, `Hint`, `Contact`, `PuzzleAttempt`,
  `AnswerAttempt`. Game rules live in `hunts/game.py`; scoring in
  `hunts/scoring.py`; CSV ingestion in `hunts/csv_import.py`.

## Running locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # only the first time
python manage.py runserver
```

URLs:

- `/` — team login
- `/play/active/`, `/play/history/`, `/play/contact/` — team game UI
- `/admin/` — Django admin for organizers

## Authentication design

There are **two separate auth systems** sharing one browser, by design:

| Side       | Identity stored as                | Cookie                  |
|------------|-----------------------------------|-------------------------|
| Team       | signed `team_id`                  | `webinfo_team` (custom) |
| Organizer  | Django `auth_user`                | Django session cookie   |

We deliberately do **not** put the team ID in `request.session`. Django's
admin login calls `auth.login()`, which rotates the session key and the
CSRF cookie. If the team's identity lived in the session, an organizer
opening admin in another tab would clobber the team's state. With the
custom signed cookie, the two are independent: an organizer can be
logged into both at once, and admin login/logout cannot dislodge the
team.

The relevant code is in `accounts/auth.py`:

- `get_team_id(request)` reads + validates the signed cookie
- `set_team_cookie(response, team_id)` / `clear_team_cookie(response)`
- `@team_required` decorator that loads `request.team` for views

> **Known limitation:** Django's CSRF cookie is still global. If an
> organizer logs into `/admin/` while a team page is already open in
> another tab, that tab's form will return a 403 on submit (CSRF
> mismatch). A page reload re-issues a fresh token. This is harmless
> for real users (teams never touch admin) and only affects the
> organizer's own browser.

## Organizer workflow (admin)

The Django admin has been customised in `hunts/admin.py` and
`accounts/admin.py`:

- **Puzzlehunts** — top-level configuration: scoring type
  (points/time), hint timing thresholds, `allow_skip`,
  `show_total_count`, `max_active_puzzles`. Inline-edit phone contacts
  for the Contact tab.
- **Puzzles** — full CRUD plus an **Import from CSV** action on the
  changelist toolbar. Prerequisites are filtered to puzzles in the same
  hunt and validated against cycles. Hint costs are labelled in the
  hunt's unit (points or minutes).
- **Teams** — full CRUD plus **Import from CSV**. Plaintext passwords
  in the CSV are hashed on insert.
- **Puzzle attempts / Answer attempts** — read-only history for
  troubleshooting.

CSV column formats and examples live under
[`csv_templates/`](csv_templates/).

## Team game flow

After login (team name + password + hunt picker on `/`):

1. **Active tab** — enter an *arrival code* you got physically at a
   puzzle location. Each active puzzle gets a card with an answer
   field, hint reveal buttons (gated by elapsed time), and an optional
   Skip button.
2. **History tab** — solved/skipped puzzles with timestamps and the
   `solve_message`.
3. **Contact tab** — phone numbers configured for the hunt.

Server-side rules enforced in `hunts/game.py`:

- 1-minute cooldown after a wrong answer (per team-per-puzzle)
- Hints unlock after configured minutes since arrival
- `max_active_puzzles` cap on simultaneously-active puzzles
- Prerequisites must be solved before a puzzle can be activated

Scoring is computed on read (`hunts/scoring.py`) — there is no counter
to keep in sync, so manual corrections in admin are safe.

## Status vs. the plan

Phases 0–5 of `PLAN.md` are implemented (skeleton, data model, team
auth + hunt selector, admin customisations, CSV import, player UI).
Remaining: phase 6 (centralised scoring/timing edge cases), phase 7
(stats + leaderboard export), phase 8 (hardening + deploy).

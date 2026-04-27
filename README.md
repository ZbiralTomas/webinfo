# Webinfo

A Django web app for running math-camp puzzlehunts. One instance can host
several concurrent hunts; organizers configure them through Django admin
and CSV imports, while teams play through a phone-friendly web UI.

For the original multi-phase delivery plan see [`PLAN.md`](PLAN.md). All
phases (0–8) are now implemented; this README is the operational
reference.

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
                 stats, CSV import, admin customisations
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
  `hunts/scoring.py`; aggregate stats in `hunts/stats.py`; CSV ingestion
  in `hunts/csv_import.py`.

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

## Admin workflow

The admin lives at `/admin/` and is a customised Django admin. The
sidebar groups the three things organizers actually edit:

- **Šifrovačky** (Puzzlehunts) — top-level configuration of a hunt
- **Šifry** (Puzzles) — the puzzles that belong to a hunt
- **Týmy** (Teams) — playing teams scoped to one hunt

Two read-only sections (**Pokusy o šifry**, **Odpovědi**) expose
`PuzzleAttempt` and `AnswerAttempt` rows for troubleshooting and
manual corrections.

### Puzzlehunts

A *Puzzlehunt* is the container for one event. Fields:

- **Název** — display name, unique across the instance.
- **Typ bodování** — scoring mode. Two systems are supported:
  - **Body (points)** — each solved puzzle awards `base_points` minus
    the cost of any hints revealed on it (floored at 0 per puzzle).
    Skipped puzzles contribute 0. Higher score wins; ties broken by
    hints used ASC, then team name.
  - **Čas (time)** — score is `elapsed + penalty` minutes, where
    *elapsed* runs from the team's first arrival until either the
    last finish event (when all puzzles are done) or "now". *Penalty*
    is the sum of hint costs (minutes for time hunts). Leaderboard is
    sorted by **# solved DESC**, then total time ASC, then hints used
    ASC, then name. Lower time wins among teams with the same solved
    count.
- **Časování nápověd** — three thresholds (`hint1_min_minutes`,
  `hint2_min_minutes`, `hint3_min_minutes`) controlling how long a
  team must wait after arriving at a puzzle before each hint can be
  revealed. There are at most 3 hints per puzzle and the form
  validates `hint1 ≤ hint2 ≤ hint3`.
- **Povolit přeskočení** — when on, teams see a "Přeskočit" button on
  active puzzles. Skipped puzzles count as finished but contribute no
  points. When off, the button is hidden everywhere.
- **Zobrazit celkový počet šifer** — when on, the player top bar shows
  `solved / total`; when off, only `solved`.
- **Max. počet aktivních šifer** — optional cap on how many puzzles a
  team can have open simultaneously. Blank means no cap.
- **Aktivní** — only active hunts appear on the team login dropdown.
- **Kontakty** — phone numbers are inline-edited on the hunt page.
  Each contact has *jméno*, *telefon*, *poznámka*, and *pořadí* (auto-
  filled if blank). These appear on the team Contact tab.

### Puzzles

Two ways to create puzzles:

1. **CSV import** — toolbar button **Naimportovat z CSV** on the puzzle
   list. Pick the target hunt, upload the file, and rows are validated
   in one pass before any are written. The format is documented in
   [`csv_templates/README.md`](csv_templates/README.md) with
   [`csv_templates/puzzles_template.csv`](csv_templates/puzzles_template.csv)
   as a starter.
2. **Manual** — standard admin add/change form.

Fields on a puzzle:

- **Šifrovačka** — which hunt this puzzle belongs to. On creation the
  picker only lists active hunts.
- **Identifikátor** (`display_id`) — a free-form short string (e.g.
  `P1`, `A`, `F03`). Unique within the hunt; used by CSV
  prerequisites.
- **Pořadí** (`order`) — integer position within the hunt. Leaving it
  blank auto-assigns the lowest unused positive integer (so a fresh
  hunt fills 1, 2, 3, … without bookkeeping).
- **Název** — display name shown to teams.
- **Příchozí kód** (arrival code) — the string a team types to *start*
  the puzzle (typically printed on the physical puzzle). Unique within
  the hunt; matched case-insensitively at play time.
- **Heslo** (solution code) — the answer the team submits. Matched
  case-insensitively against the team's submission, with a 1-minute
  cooldown after a wrong answer.
- **Základní body** (`base_points`) — points awarded when this puzzle
  is solved. Only used in *points* hunts; ignored in time hunts.
- **Zpráva po vyřešení** (`solve_message`) — free text shown to the
  team on a correct solve and on the History tab.
- **Předpoklady** (prerequisites) — many-to-many to other puzzles in
  the same hunt. The picker is filtered to the same hunt and the form
  rejects edges that would create a cycle. The CSV importer also
  refuses cycles. A puzzle can only be activated once *all* its
  prerequisites are solved or skipped.
- **Nápovědy** — up to 3 hints, edited inline. Each has a text and a
  cost. The cost field's label switches between "bodů" and "minut" to
  match the hunt's scoring type. Hints must be numbered 1..N without
  gaps (allowed sets: `{}`, `{1}`, `{1,2}`, `{1,2,3}`).

### Teams

Two ways to create teams:

1. **CSV import** — toolbar button on the team list. Format in
   [`csv_templates/README.md`](csv_templates/README.md), starter at
   [`csv_templates/teams_template.csv`](csv_templates/teams_template.csv).
   Passwords are accepted as plaintext in the CSV and **hashed on
   insert** (delete the file from your machine afterwards).
2. **Manual** — admin add form asks for hunt, name, and an initial
   password. On the change form there's a "Nové heslo" field; leaving
   it blank keeps the existing password.

A team only stores **name** and **password**. The name is unique
within a hunt (the same string can be reused across different hunts),
and the password is hashed with Django's default hasher (PBKDF2). The
team detail view lists the team's active puzzles, finished puzzles,
and submitted answers as read-only inlines.

### Statistics

Each puzzlehunt has a **Statistiky** button on its admin change form.
That page shows three things:

- **Žebříček** (leaderboard) — every team ranked by the hunt's
  scoring rule, plus a **Stáhnout CSV** link
  (`/admin/.../leaderboard.csv`) that exports the same table for
  post-camp analysis.
- **Po šifrách** (per-puzzle stats) — for each puzzle: how many teams
  arrived, how many solved, how many skipped, average solve time, and
  total hints revealed. Each puzzle name is a link to a drill-down
  page that shows, for every team in the hunt, that team's status on
  that one puzzle (not arrived / active / solved / skipped), arrival
  and solve times, minutes spent, hints taken, and wrong-answer count.
- **Matice týmy × šifry** — a grid of every team against every puzzle,
  one cell per pair, summarising status and timing.

All of these are computed on read from `PuzzleAttempt` / `AnswerAttempt`
rows (see `hunts/stats.py`), so manual corrections in the admin take
effect immediately with no counters to keep in sync.

## User (team) workflow

A team logs in at `/` with name + password + hunt picker (only active
hunts are listed). After login, every page in the player UI shares the
same header and tab strip and then renders one of three tabs.

### Head (top bar)

Always visible across all three tabs. Contents, in order:

- **Logo** on the left.
- **Team name** and the **hunt name**.
- **Score** — the format depends on the hunt's scoring type:
  - *Time* hunts: `Čas: <elapsed> + <penalty> min` so teams see how
    much of their score came from playing vs. from hint penalties.
  - *Points* hunts: `Skóre: <value> bodů`.
- **Finished count** — `Dokončeno: <n> / <total>` if the hunt has
  **show_total_count** enabled, otherwise just `Dokončeno: <n>` so
  teams can't infer the puzzle count from the UI.
- **Solved / skipped split** — `<solved> / <skipped>` shown only when
  the hunt has **allow_skip** enabled (otherwise `skipped` is always 0
  and the line is redundant).
- **Odhlásit se** button (POST to logout, clears the team cookie).

### Tabs

Three tabs, switched by a nav strip below the head:

1. **Aktivní** — the working tab.
   - At the top, an **arrival code form**. Submitting a valid code
     activates that puzzle (subject to prerequisites and the
     `max_active_puzzles` cap).
   - Below it, one **card per active puzzle**. Each card shows the
     puzzle's display id and name, how many minutes it has been open,
     an answer field with submit button, and any hints. Wrong answers
     trigger a 1-minute cooldown that disables the answer field and
     shows a countdown. Hints are gated by the hunt's per-hint
     thresholds: locked hints show "odemkne se za ~N min", unlocked
     hints show a reveal button (with the cost), revealed hints show
     their text. If the hunt allows skipping, a confirmation-prompted
     **Přeskočit** button appears at the bottom of the card.
   - When all puzzles in the hunt are finished, a **"Šifrovačka
     dokončena!"** banner is shown above the form with the team's
     final score.

2. **Historie** — read-only list of finished puzzles, newest first.
   Each entry shows the puzzle id and name, a `[vyřešeno]` or
   `[přeskočeno]` tag, the timestamp (and elapsed minutes for solved
   puzzles), and the puzzle's `solve_message` if any. This is where
   teams reread the post-solve flavour text.

3. **Kontakty** — list of organizer phone numbers configured on the
   hunt, with a `tel:` link on each. The intro line tells teams to
   call here if something is broken or they're stuck.

### Server-side rules enforced in `hunts/game.py`

- 1-minute cooldown after a wrong answer (per team-per-puzzle).
- Hints unlock after the hunt's configured minutes since arrival, and
  must be taken in order (1 → 2 → 3).
- `max_active_puzzles` cap on simultaneously-active puzzles.
- Prerequisites must be solved or skipped before a puzzle can be
  activated.
- Arrival code and answer comparisons are case-insensitive after
  trimming whitespace.

Scoring is computed on read (`hunts/scoring.py`) — there is no counter
to keep in sync, so manual corrections in admin are safe.

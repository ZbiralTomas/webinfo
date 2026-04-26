# Webinfo — Puzzlehunt Web App Plan

A Django-based web application for running math-camp puzzlehunts. Supports
multiple concurrent puzzlehunts on a single instance (max 2 at once in
practice), an organizer/admin side for setup, and a player side for teams.

## Tech stack

- **Backend:** Django (Python)
- **DB:** SQLite for development, PostgreSQL for production
- **Frontend:** Django templates + light HTMX/vanilla JS for live updates
- **Deployment:** Gunicorn + Nginx on a small VPS, or Railway/Fly.io
- **IDE:** PyCharm (Professional has best Django support; Community works too)

## Phase 0 — Project skeleton (½ day)

- Initialize Django project inside `Webinfo/`
- Two apps: `hunts` (game logic) and `accounts` (auth/teams)
- Set up `.gitignore`, `requirements.txt`, basic settings, dev DB
- Confirm we can run the server and log in to `/admin`

## Phase 1 — Data model (1 day)

Core tables:

- **Puzzlehunt**: name, scoring_type (points/time), hint1_min_minutes,
  hint2_min_minutes, hint3_min_minutes, allow_skip, show_total_count,
  is_active
- **Puzzle**: puzzlehunt (FK), display_id, name, arrival_code, password,
  hint1_text, hint2_text, hint3_text, hint1_cost, hint2_cost, hint3_cost,
  solve_message
- **PuzzlePrerequisite**: puzzle (FK), required_puzzle (FK) — many-to-many
- **Team**: puzzlehunt (FK), name, password (hashed via Django's default
  hasher — protects against password reuse on other services)
- **PuzzleAttempt**: team (FK), puzzle (FK), arrived_at, solved_at,
  hints_taken (1/2/3), skipped, score_delta
- **AnswerAttempt**: team (FK), puzzle (FK), submitted_answer, correct,
  submitted_at (used for the 1-min spam timeout)

This phase is the most important — getting it right makes the rest mechanical.

## Phase 2 — Authentication & puzzlehunt selector (½ day)

- Login page: Name + Password + puzzlehunt dropdown (only `is_active` hunts)
- Custom auth backend that validates `(name, password, puzzlehunt)` together
  so the same team name can exist in different hunts
- Session stores the active hunt

## Phase 3 — Organizer side, manual CRUD (1–2 days)

- Use Django's built-in admin for puzzlehunts, puzzles, teams
- Customize forms so prerequisites render as a list of checkboxes (one per
  puzzle in the hunt) instead of an ID list — fewer typos
- Hint cost field labels switch between "points" and "minutes" based on
  hunt type
- Validation: arrival codes unique per hunt, prerequisites don't form cycles

## Phase 4 — CSV import (1 day)

- Upload form per entity (puzzles, teams) in admin
- Parse, validate, show a preview, then commit
- Row-by-row error report on malformed CSV
- Team passwords accepted as plaintext in CSV and hashed on insert
- Document CSV column format in a small README

## Phase 5 — Player game UI (2–3 days)

- **Top bar (always visible):** team name, score, solved/total (respecting
  `show_total_count`)
- **Arrival code form** → activates puzzle, starts hint timer
- **Answer submission:** dropdown of currently-active puzzles + answer field
  - Correct → show `solve_message`, mark solved, unlock dependents
  - Incorrect → 1-min cooldown enforced server-side per team
- **Hints** button per active puzzle; only visible after the configured
  minimum time elapsed since arrival
- **Skip** button if `allow_skip` is true on the hunt
- **History tab:** list of solved puzzles with their solve_messages
- **Contact tab:** list of phone numbers, editable from admin

## Phase 6 — Scoring, timing, prerequisites (1 day)

- Centralize scoring in one module so points/time modes share logic
- Compute score on read rather than mutating counters, so corrections are
  trivial
- Prerequisite check before a puzzle can be activated

## Phase 7 — Stats & export (½–1 day)

- Per-puzzle stats: solve count, average solve time, hint usage
- Leaderboard export (CSV) sorted by hunt's scoring type
- "Download everything" button for post-camp analysis

## Phase 8 — Hardening & deploy (1 day)

- HTTPS, password hashing (Django default is fine), CSRF on all forms
- Rate-limit login attempts
- Backup script (cron'd `pg_dump`)
- Deploy to chosen host, smoke test with two parallel hunts
- **Rotate `SECRET_KEY`.** The current key in `webinfo/settings.py` is the
  throwaway one Django generated at `startproject` (prefix
  `django-insecure-`) and has been committed to the repo. Before
  deploying to anything real, generate a fresh key, move it to an env
  var (e.g. `DJANGO_SECRET_KEY` read in `settings.py`), and never let
  the prod key touch git history. Same treatment for `DEBUG`,
  `ALLOWED_HOSTS`, and DB credentials.

---

**Total estimate:** 8–12 focused days of work, broken into independently
shippable phases.

"""Game-level operations performed by team players.

These functions take a `team` and apply business rules on top of the raw
ORM (cooldowns, prerequisites, hint-thresholds, max-active limits).
Each returns a small result struct or raises `GameError` with a message
that's safe to show the player.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from hunts.models import AnswerAttempt, Hint, Puzzle, PuzzleAttempt


COOLDOWN_SECONDS = 60


def _normalize(s: str) -> str:
    """Lenient form for arrival codes: ignores case and diacritics."""
    s = (s or "").strip().casefold()
    # NFD splits accented chars into base + combining marks; drop the marks so
    # "Příklad" matches "priklad".
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _normalize_password(s: str) -> str:
    """Strict form for passwords (answers, start code): ignores case but
    keeps diacritics. NFC makes composed and decomposed accents equal, so
    "é" typed on any keyboard/OS compares the same."""
    return unicodedata.normalize("NFC", (s or "").strip()).casefold()


class GameError(Exception):
    pass


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_active_attempts(team):
    return (
        team.puzzle_attempts
        .filter(solved_at__isnull=True, skipped=False)
        .select_related("puzzle")
        .order_by("arrived_at")
    )


def get_finished_attempts(team):
    """Solved + skipped, newest finish event first."""
    return (
        team.puzzle_attempts
        .exclude(solved_at__isnull=True, skipped=False)
        .select_related("puzzle")
        .order_by("-solved_at", "-arrived_at")
    )


def cooldown_remaining(team, puzzle) -> int:
    """Seconds remaining before this team can submit another answer for this puzzle."""
    last_wrong = (
        AnswerAttempt.objects
        .filter(team=team, puzzle=puzzle, correct=False)
        .order_by("-submitted_at")
        .first()
    )
    if last_wrong is None:
        return 0
    elapsed = (timezone.now() - last_wrong.submitted_at).total_seconds()
    return max(0, int(COOLDOWN_SECONDS - elapsed))


# ---------------------------------------------------------------------------
# Arrive
# ---------------------------------------------------------------------------


def get_unlock_notices(team) -> list[Puzzle]:
    """Puzzles the team has unlocked (all prerequisites finished) but not yet
    arrived at, and that carry an `unlock_message` to show them."""
    attempted = set(team.puzzle_attempts.values_list("puzzle_id", flat=True))
    finished = set(
        team.puzzle_attempts
        .exclude(solved_at__isnull=True, skipped=False)
        .values_list("puzzle_id", flat=True)
    )
    notices = []
    puzzles = (
        team.puzzlehunt.puzzles
        .exclude(unlock_message="")
        .prefetch_related("prerequisites")
    )
    for p in puzzles:
        if p.pk in attempted:
            continue
        prereq_ids = {q.pk for q in p.prerequisites.all()}
        if prereq_ids and prereq_ids <= finished:
            notices.append(p)
    return notices


def _start_hunt(team) -> list[PuzzleAttempt]:
    """Open every `opens_at_start` puzzle at once. Not subject to
    `max_active_puzzles`; this is what starts the team's clock."""
    if team.puzzle_attempts.exists():
        raise GameError("Hra už začala.")
    start_puzzles = list(team.puzzlehunt.puzzles.filter(opens_at_start=True))
    if not start_puzzles:
        raise GameError("Tato šifrovačka nemá žádné úvodní šifry.")
    with transaction.atomic():
        return [PuzzleAttempt.objects.create(team=team, puzzle=p) for p in start_puzzles]


def enter_arrival_code(team, code: str) -> list[PuzzleAttempt]:
    """Handle the code field on the Active tab.

    Returns the attempts that were opened: one for a normal arrival code,
    several for the hunt's start code.
    """
    code_norm = _normalize(code)
    if not code_norm:
        raise GameError("Zadej příchozí kód.")

    start_code = team.puzzlehunt.start_code
    if start_code and _normalize_password(start_code) == _normalize_password(code):
        return _start_hunt(team)

    # Find the puzzle in this hunt with a matching arrival_code. Case is
    # always ignored; diacritics only unless the hunt makes them count.
    if team.puzzlehunt.arrival_codes_diacritics:
        same = _normalize_password
    else:
        same = _normalize
    typed = same(code)
    puzzle = next(
        (
            p for p in team.puzzlehunt.puzzles.all()
            if not p.opens_at_start
            and p.arrival_code
            and same(p.arrival_code) == typed
        ),
        None,
    )
    if puzzle is None:
        raise GameError("Tento příchozí kód nebyl rozpoznán.")

    existing = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if existing is not None:
        if existing.solved_at is not None:
            raise GameError(f"„{puzzle.name}“ jste již vyřešili.")
        if existing.skipped:
            raise GameError(f"„{puzzle.name}“ jste již přeskočili.")
        raise GameError(f"Na „{puzzle.name}“ už pracujete.")

    # Prerequisite check: every prerequisite puzzle must be solved or skipped.
    prereq_ids = list(puzzle.prerequisites.values_list("pk", flat=True))
    if prereq_ids:
        finished_prereq_ids = set(
            team.puzzle_attempts.filter(
                puzzle_id__in=prereq_ids
            ).filter(
                # solved_at not null OR skipped=True
            ).exclude(solved_at__isnull=True, skipped=False)
            .values_list("puzzle_id", flat=True)
        )
        if set(prereq_ids) - finished_prereq_ids:
            raise GameError(
                "Tato šifra ještě není odemčena — některé předpoklady nejsou splněné."
            )

    # max_active_puzzles limit
    max_active = team.puzzlehunt.max_active_puzzles
    if max_active is not None:
        active_count = get_active_attempts(team).count()
        if active_count >= max_active:
            raise GameError(
                f"Najednou můžete mít otevřeno nejvýše {max_active} šifer. "
                "Před otevřením další jednu vyřeš nebo přeskoč."
            )

    return [PuzzleAttempt.objects.create(team=team, puzzle=puzzle)]


# ---------------------------------------------------------------------------
# Submit answer
# ---------------------------------------------------------------------------


@dataclass
class AnswerResult:
    correct: bool
    puzzle: Puzzle
    solve_message: str = ""


def submit_answer(team, puzzle_id: int, answer: str) -> AnswerResult:
    puzzle = team.puzzlehunt.puzzles.filter(pk=puzzle_id).first()
    if puzzle is None:
        raise GameError("Vyber šifru, na kterou chceš odpovědět.")
    attempt = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if attempt is None or not attempt.is_active:
        raise GameError(f"„{puzzle.name}“ není pro tvůj tým aktuálně aktivní.")
    if not (answer or "").strip():
        raise GameError("Nejprve napiš odpověď.")

    remaining = cooldown_remaining(team, puzzle)
    if remaining > 0:
        raise GameError(
            f"Počkej ještě {remaining} s, "
            f"než znovu odpovíš na „{puzzle.name}“."
        )

    correct = _normalize_password(answer) == _normalize_password(puzzle.password)
    # Save the AnswerAttempt — model save() will auto-flip the PuzzleAttempt
    # to solved if `correct=True`.
    AnswerAttempt.objects.create(
        team=team, puzzle=puzzle, submitted_answer=answer.strip(), correct=correct
    )
    return AnswerResult(
        correct=correct,
        puzzle=puzzle,
        solve_message=puzzle.solve_message if correct else "",
    )


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------


def hint_status_for(team, attempt) -> list[dict]:
    """Returns a list of dicts describing each hint slot for an active puzzle.

    Each dict has: order, exists, taken, available_in_seconds, text, cost.
    Used to render the hint UI: revealed hints show their text; locked
    hints show countdown; non-existent slots are omitted from rendering.
    """
    hunt = team.puzzlehunt
    now = timezone.now()
    hints_by_order = {h.order: h for h in attempt.puzzle.hints.all()}
    out = []
    for order in (1, 2, 3):
        h = hints_by_order.get(order)
        if h is None:
            out.append({"order": order, "exists": False})
            continue
        threshold_min = hunt.hint_threshold_minutes(order)
        unlock_at = attempt.arrived_at + timedelta(minutes=threshold_min)
        secs_to_unlock = max(0, int((unlock_at - now).total_seconds()))
        taken = order <= attempt.hints_taken
        out.append({
            "order": order,
            "exists": True,
            "taken": taken,
            "available_in_seconds": secs_to_unlock,
            "available_in_minutes": (secs_to_unlock + 59) // 60,
            "text": h.text,
            "cost": h.cost,
        })
    return out


def reveal_hint(team, puzzle_id: int, hint_order: int) -> Hint:
    if hint_order not in (1, 2, 3):
        raise GameError("Neplatné číslo nápovědy.")
    puzzle = team.puzzlehunt.puzzles.filter(pk=puzzle_id).first()
    if puzzle is None:
        raise GameError("Neznámá šifra.")
    attempt = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if attempt is None or not attempt.is_active:
        raise GameError(f"„{puzzle.name}“ není aktuálně aktivní.")

    hint = Hint.objects.filter(puzzle=puzzle, order=hint_order).first()
    if hint is None:
        raise GameError(f"Nápověda {hint_order} pro tuto šifru neexistuje.")

    # Sequential: must take previous hints first
    if hint_order > attempt.hints_taken + 1:
        raise GameError(
            f"Nejprve si vezmi nápovědu {attempt.hints_taken + 1}."
        )
    if hint_order <= attempt.hints_taken:
        # Already revealed — just return it (idempotent).
        return hint

    threshold_min = team.puzzlehunt.hint_threshold_minutes(hint_order)
    unlock_at = attempt.arrived_at + timedelta(minutes=threshold_min)
    if timezone.now() < unlock_at:
        secs = int((unlock_at - timezone.now()).total_seconds())
        mins = (secs + 59) // 60
        raise GameError(
            f"Nápověda {hint_order} ještě není dostupná. Zkus to za ~{mins} min."
        )

    with transaction.atomic():
        attempt.hints_taken = hint_order
        attempt.save(update_fields=["hints_taken"])
    return hint


# ---------------------------------------------------------------------------
# Skip
# ---------------------------------------------------------------------------


@dataclass
class IntroSkipStatus:
    available: int                    # skips the team can use right now
    next_in_seconds: int | None       # until the next skip is earned; None = no more to come

    @property
    def next_in_minutes(self) -> int | None:
        if self.next_in_seconds is None:
            return None
        return (self.next_in_seconds + 59) // 60


def uses_intro_skip_timer(hunt, puzzle) -> bool:
    return puzzle.opens_at_start and bool(hunt.intro_skip_schedule)


def intro_skip_status(team) -> IntroSkipStatus | None:
    """Skip credit for the intro puzzles, or None if no schedule is set or the
    team hasn't started yet. One skip is earned at each scheduled minute since
    the start; unused ones accumulate."""
    schedule = team.puzzlehunt.intro_skip_schedule
    if not schedule:
        return None
    intro_attempts = team.puzzle_attempts.filter(puzzle__opens_at_start=True)
    start = intro_attempts.order_by("arrived_at").values_list("arrived_at", flat=True).first()
    if start is None:
        return None
    elapsed = max(0, int((timezone.now() - start).total_seconds()))
    earned = sum(1 for t in schedule if t * 60 <= elapsed)
    used = intro_attempts.filter(skipped=True).count()
    upcoming = [t * 60 - elapsed for t in schedule if t * 60 > elapsed]
    return IntroSkipStatus(
        available=max(0, earned - used),
        next_in_seconds=min(upcoming) if upcoming else None,
    )


def skip_wait_seconds(hunt, attempt) -> int:
    """Seconds until the hunt's time-gated skip opens for this attempt (0 = open now)."""
    unlock_at = attempt.arrived_at + timedelta(minutes=hunt.skip_min_minutes)
    return max(0, int((unlock_at - timezone.now()).total_seconds()))


def can_skip(team, attempt) -> bool:
    """Whether the Skip button should be offered for this active attempt."""
    hunt = team.puzzlehunt
    if uses_intro_skip_timer(hunt, attempt.puzzle):
        status = intro_skip_status(team)
        return status is not None and status.available > 0
    return hunt.allow_skip and skip_wait_seconds(hunt, attempt) == 0


def skip_puzzle(team, puzzle_id: int) -> PuzzleAttempt:
    hunt = team.puzzlehunt
    puzzle = hunt.puzzles.filter(pk=puzzle_id).first()
    if puzzle is None:
        raise GameError("Neznámá šifra.")
    timed_intro = uses_intro_skip_timer(hunt, puzzle)
    if timed_intro:
        status = intro_skip_status(team)
        if status is None or status.available < 1:
            mins = status.next_in_minutes if status else None
            if mins is None:
                raise GameError("Už nemáte k dispozici žádné další přeskočení úvodní šifry.")
            raise GameError(
                f"Přeskočení úvodní šifry zatím není dostupné. Další bude za ~{mins} min."
            )
    elif not hunt.allow_skip:
        raise GameError("Přeskakování není v této šifrovačce povoleno.")
    attempt = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if attempt is None or not attempt.is_active:
        raise GameError(f"„{puzzle.name}“ není aktuálně aktivní.")
    if not timed_intro:
        wait = skip_wait_seconds(hunt, attempt)
        if wait > 0:
            raise GameError(
                f"Přeskočení bude dostupné za ~{(wait + 59) // 60} min."
            )
    attempt.skipped = True
    attempt.save(update_fields=["skipped"])
    return attempt

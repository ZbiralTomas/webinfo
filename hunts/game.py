"""Game-level operations performed by team players.

These functions take a `team` and apply business rules on top of the raw
ORM (cooldowns, prerequisites, hint-thresholds, max-active limits).
Each returns a small result struct or raises `GameError` with a message
that's safe to show the player.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from hunts.models import AnswerAttempt, Hint, Puzzle, PuzzleAttempt


COOLDOWN_SECONDS = 60


def _normalize(s: str) -> str:
    return (s or "").strip().casefold()


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


def enter_arrival_code(team, code: str) -> PuzzleAttempt:
    code_norm = _normalize(code)
    if not code_norm:
        raise GameError("Enter an arrival code.")

    # Find the puzzle in this hunt with a matching arrival_code (case-insensitive).
    puzzle = next(
        (p for p in team.puzzlehunt.puzzles.all() if _normalize(p.arrival_code) == code_norm),
        None,
    )
    if puzzle is None:
        raise GameError("That arrival code isn't recognized.")

    existing = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if existing is not None:
        if existing.solved_at is not None:
            raise GameError(f"You've already solved “{puzzle.name}”.")
        if existing.skipped:
            raise GameError(f"You've already skipped “{puzzle.name}”.")
        raise GameError(f"You're already working on “{puzzle.name}”.")

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
                "You haven't unlocked this puzzle yet — its prerequisites aren't all done."
            )

    # max_active_puzzles limit
    max_active = team.puzzlehunt.max_active_puzzles
    if max_active is not None:
        active_count = get_active_attempts(team).count()
        if active_count >= max_active:
            raise GameError(
                f"You can have at most {max_active} active puzzles at a time. "
                "Solve or skip one before arriving at another."
            )

    return PuzzleAttempt.objects.create(team=team, puzzle=puzzle)


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
        raise GameError("Pick a puzzle to answer.")
    attempt = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if attempt is None or not attempt.is_active:
        raise GameError(f"“{puzzle.name}” isn't currently active for your team.")
    if not (answer or "").strip():
        raise GameError("Type an answer first.")

    remaining = cooldown_remaining(team, puzzle)
    if remaining > 0:
        raise GameError(
            f"Wait {remaining} more second{'s' if remaining != 1 else ''} "
            f"before submitting another answer for “{puzzle.name}”."
        )

    correct = _normalize(answer) == _normalize(puzzle.password)
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
        raise GameError("Invalid hint number.")
    puzzle = team.puzzlehunt.puzzles.filter(pk=puzzle_id).first()
    if puzzle is None:
        raise GameError("Unknown puzzle.")
    attempt = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if attempt is None or not attempt.is_active:
        raise GameError(f"“{puzzle.name}” isn't currently active.")

    hint = Hint.objects.filter(puzzle=puzzle, order=hint_order).first()
    if hint is None:
        raise GameError(f"Hint {hint_order} doesn't exist for this puzzle.")

    # Sequential: must take previous hints first
    if hint_order > attempt.hints_taken + 1:
        raise GameError(
            f"Take hint {attempt.hints_taken + 1} first."
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
            f"Hint {hint_order} isn't available yet. Try again in ~{mins} min."
        )

    with transaction.atomic():
        attempt.hints_taken = hint_order
        attempt.save(update_fields=["hints_taken"])
    return hint


# ---------------------------------------------------------------------------
# Skip
# ---------------------------------------------------------------------------


def skip_puzzle(team, puzzle_id: int) -> PuzzleAttempt:
    if not team.puzzlehunt.allow_skip:
        raise GameError("Skipping is disabled for this puzzlehunt.")
    puzzle = team.puzzlehunt.puzzles.filter(pk=puzzle_id).first()
    if puzzle is None:
        raise GameError("Unknown puzzle.")
    attempt = PuzzleAttempt.objects.filter(team=team, puzzle=puzzle).first()
    if attempt is None or not attempt.is_active:
        raise GameError(f"“{puzzle.name}” isn't currently active.")
    attempt.skipped = True
    attempt.save(update_fields=["skipped"])
    return attempt

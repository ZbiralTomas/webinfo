"""Score computation for a team within its puzzlehunt.

Scoring depends on the hunt's `scoring_type`:

- POINTS: each solved puzzle contributes max(0, base_points - hint_cost),
  where hint_cost is the sum of costs of hints revealed on that puzzle.
  Skipped puzzles contribute 0 (the per-puzzle floor prevents hint cost
  on a skipped puzzle from dragging the team's total down).
- TIME: minutes from the team's first arrival until their last finished
  event (solved or skipped), or "now" if still playing. Plus the sum of
  hint costs (which are minutes for time-based hunts). A team that has
  never arrived at any puzzle has score 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from hunts.models import Hint, Puzzlehunt, PuzzleAttempt


@dataclass
class TeamScore:
    value: int          # numeric score (points or minutes; total)
    unit: str           # "points" or "minutes"
    elapsed: int        # time-only: minutes elapsed (0 for points hunts)
    penalty: int        # time-only: minutes added by hints (0 for points hunts)
    solved: int         # count of solved puzzles
    skipped: int        # count of skipped puzzles
    finished: int       # solved + skipped (used for "is the hunt over for me?")
    total: int          # total puzzles in the hunt

    def display(self) -> str:
        return f"{self.value} {self.unit}"


def _hint_cost_by_attempt(attempts) -> dict[int, int]:
    """Map of attempt.pk -> sum of revealed hint costs for that attempt.

    `PuzzleAttempt.hints_taken` is an integer N meaning the team has revealed
    hints 1..N for that puzzle.
    """
    costs: dict[int, int] = {}
    for a in attempts:
        if a.hints_taken <= 0:
            costs[a.pk] = 0
            continue
        agg = Hint.objects.filter(
            puzzle=a.puzzle, order__lte=a.hints_taken
        ).aggregate(s=Sum("cost"))
        costs[a.pk] = agg["s"] or 0
    return costs


def compute_score(team) -> TeamScore:
    hunt: Puzzlehunt = team.puzzlehunt
    attempts = list(team.puzzle_attempts.select_related("puzzle").all())
    solved_count = sum(1 for a in attempts if a.solved_at is not None and not a.skipped)
    skipped_count = sum(1 for a in attempts if a.skipped)
    finished_count = solved_count + skipped_count
    total_count = hunt.puzzles.count()

    hint_cost_by_attempt = _hint_cost_by_attempt(attempts)

    if hunt.scoring_type == Puzzlehunt.SCORING_POINTS:
        earned = 0
        for a in attempts:
            base = a.puzzle.base_points if (a.solved_at is not None and not a.skipped) else 0
            earned += max(0, base - hint_cost_by_attempt[a.pk])
        return TeamScore(
            value=earned,
            unit="bodů",
            elapsed=0,
            penalty=0,
            solved=solved_count,
            skipped=skipped_count,
            finished=finished_count,
            total=total_count,
        )

    # TIME-based scoring
    arrivals = [a.arrived_at for a in attempts if a.arrived_at is not None]
    if not arrivals:
        return TeamScore(
            value=0, unit="minut",
            elapsed=0, penalty=0,
            solved=solved_count, skipped=skipped_count,
            finished=finished_count, total=total_count,
        )
    first_arrival = min(arrivals)

    finished_events = [
        a.solved_at for a in attempts if a.solved_at is not None
    ]
    # Skipped puzzles also count as a "finish event" for time tracking; we
    # don't have a skipped_at timestamp on the model, so we approximate with
    # arrived_at (skipping happens during the active session, which is close
    # enough for time scoring purposes).
    finished_events += [a.arrived_at for a in attempts if a.skipped]

    if finished_count == total_count and finished_events:
        end = max(finished_events)
    else:
        end = timezone.now()

    elapsed_minutes = max(0, int((end - first_arrival).total_seconds() // 60))
    penalty_minutes = sum(hint_cost_by_attempt.values())

    return TeamScore(
        value=elapsed_minutes + penalty_minutes,
        unit="minut",
        elapsed=elapsed_minutes,
        penalty=penalty_minutes,
        solved=solved_count,
        skipped=skipped_count,
        finished=finished_count,
        total=total_count,
    )

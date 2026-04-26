"""Score computation for a team within its puzzlehunt.

Scoring depends on the hunt's `scoring_type`:

- POINTS: each solved puzzle is worth 1 point. Sum of hint costs taken on
  any attempt is subtracted. Skipped puzzles count as 0.
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
    value: int          # numeric score (points or minutes)
    unit: str           # "points" or "minutes"
    solved: int         # count of solved puzzles
    finished: int       # solved + skipped (used for "is the hunt over for me?")
    total: int          # total puzzles in the hunt

    def display(self) -> str:
        return f"{self.value} {self.unit}"


def _hint_cost_total(team) -> int:
    """Sum of `cost` of every hint the team has revealed across all puzzles.

    `PuzzleAttempt.hints_taken` is an integer N meaning the team has revealed
    hints 1..N for that puzzle. So we sum hint.cost for hints whose order
    is <= the team's hints_taken on that puzzle.
    """
    total = 0
    attempts = team.puzzle_attempts.select_related("puzzle").all()
    for attempt in attempts:
        if attempt.hints_taken <= 0:
            continue
        agg = Hint.objects.filter(
            puzzle=attempt.puzzle, order__lte=attempt.hints_taken
        ).aggregate(s=Sum("cost"))
        total += agg["s"] or 0
    return total


def compute_score(team) -> TeamScore:
    hunt: Puzzlehunt = team.puzzlehunt
    attempts = list(team.puzzle_attempts.select_related("puzzle").all())
    solved_count = sum(1 for a in attempts if a.solved_at is not None and not a.skipped)
    finished_count = sum(1 for a in attempts if a.solved_at is not None or a.skipped)
    total_count = hunt.puzzles.count()

    hint_cost = _hint_cost_total(team)

    if hunt.scoring_type == Puzzlehunt.SCORING_POINTS:
        earned = sum(
            a.puzzle.base_points
            for a in attempts
            if a.solved_at is not None and not a.skipped
        )
        value = max(0, earned - hint_cost)
        return TeamScore(
            value=value,
            unit="points",
            solved=solved_count,
            finished=finished_count,
            total=total_count,
        )

    # TIME-based scoring
    arrivals = [a.arrived_at for a in attempts if a.arrived_at is not None]
    if not arrivals:
        return TeamScore(
            value=0, unit="minutes",
            solved=solved_count, finished=finished_count, total=total_count,
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
    value = elapsed_minutes + hint_cost

    return TeamScore(
        value=value,
        unit="minutes",
        solved=solved_count,
        finished=finished_count,
        total=total_count,
    )

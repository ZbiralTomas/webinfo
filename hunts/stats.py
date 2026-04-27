"""Aggregate statistics for organizers: per-puzzle stats and team leaderboards.

Read-only helpers that re-derive everything from `PuzzleAttempt` rows; nothing
is cached or stored. The leaderboard sorting matches what organizers expect:

- POINTS hunts: score DESC, hints used ASC, team name ASC
                (hints used is the tiebreaker among teams with the same score).
- TIME hunts:   solved DESC, total time ASC, hints used ASC, team name ASC
                (primary metric is # solved, time is the tiebreaker, hints
                used is the second tiebreaker).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Sum
from django.utils import timezone

from .models import AnswerAttempt, Puzzle, Puzzlehunt, PuzzleAttempt
from .scoring import TeamScore, compute_score


@dataclass
class PuzzleStat:
    puzzle: object       # hunts.Puzzle
    arrived: int
    solved: int
    skipped: int
    avg_solve_minutes: float | None  # None if no team solved it yet
    hints_revealed: int  # total across all teams' attempts on this puzzle


@dataclass
class LeaderboardRow:
    rank: int
    team: object         # accounts.Team
    score: TeamScore
    hints_used: int


@dataclass
class PuzzleTeamRow:
    """One row in the per-puzzle drill-down: how a single team did on it."""
    team: object              # accounts.Team
    status: str               # "not_arrived" | "active" | "solved" | "skipped"
    arrived_at: object | None
    solved_at: object | None
    minutes_spent: int | None  # None for not_arrived/skipped (no skipped_at on model)
    hints_taken: int
    wrong_answers: int


@dataclass
class MatrixCell:
    status: str               # "not_arrived" | "active" | "solved" | "skipped"
    arrived_at: object | None
    solved_at: object | None
    minutes_spent: int | None
    hints_taken: int


@dataclass
class TeamProgressMatrix:
    puzzles: list                          # ordered list of Puzzle
    rows: list[tuple[object, list[MatrixCell]]]  # (team, [cell per puzzle])


def puzzle_stats(hunt: Puzzlehunt) -> list[PuzzleStat]:
    rows: list[PuzzleStat] = []
    for puzzle in hunt.puzzles.all():
        attempts = list(puzzle.attempts.all())
        arrived = len(attempts)
        skipped = sum(1 for a in attempts if a.skipped)
        solved_attempts = [
            a for a in attempts if a.solved_at is not None and not a.skipped
        ]
        solved = len(solved_attempts)
        hints_revealed = sum(a.hints_taken for a in attempts)

        if solved_attempts:
            total_seconds = sum(
                (a.solved_at - a.arrived_at).total_seconds() for a in solved_attempts
            )
            avg_minutes: float | None = total_seconds / 60 / solved
        else:
            avg_minutes = None

        rows.append(
            PuzzleStat(
                puzzle=puzzle,
                arrived=arrived,
                solved=solved,
                skipped=skipped,
                avg_solve_minutes=avg_minutes,
                hints_revealed=hints_revealed,
            )
        )
    return rows


def leaderboard(hunt: Puzzlehunt) -> list[LeaderboardRow]:
    teams = list(hunt.teams.all())
    enriched = []
    for team in teams:
        score = compute_score(team)
        hints_used = (
            team.puzzle_attempts.aggregate(s=Sum("hints_taken"))["s"] or 0
        )
        enriched.append((team, score, hints_used))

    if hunt.scoring_type == Puzzlehunt.SCORING_POINTS:
        # Higher score is better; fewer hints is better; tiebreak by name.
        enriched.sort(key=lambda t: (-t[1].value, t[2], t[0].name.lower()))
    else:
        # More solved is better; less time is better; fewer hints is better.
        enriched.sort(
            key=lambda t: (-t[1].solved, t[1].value, t[2], t[0].name.lower())
        )

    return [
        LeaderboardRow(rank=i + 1, team=team, score=score, hints_used=hints_used)
        for i, (team, score, hints_used) in enumerate(enriched)
    ]


def _attempt_status(attempt) -> str:
    if attempt.skipped:
        return "skipped"
    if attempt.solved_at is not None:
        return "solved"
    return "active"


def _minutes_spent(attempt, now) -> int | None:
    """Minutes the team has spent on this puzzle.

    - solved:  solved_at − arrived_at
    - active:  now − arrived_at
    - skipped: None (no skipped_at on the model)
    """
    if attempt.skipped:
        return None
    end = attempt.solved_at if attempt.solved_at is not None else now
    return max(0, int((end - attempt.arrived_at).total_seconds() // 60))


def puzzle_team_stats(puzzle: Puzzle) -> list[PuzzleTeamRow]:
    """One row per team in the hunt — covers teams that haven't arrived yet too."""
    now = timezone.now()
    teams = list(puzzle.puzzlehunt.teams.all())
    attempts_by_team = {
        a.team_id: a for a in puzzle.attempts.select_related("team").all()
    }

    wrongs_by_team: dict[int, int] = {}
    for w in AnswerAttempt.objects.filter(puzzle=puzzle, correct=False).values("team_id"):
        wrongs_by_team[w["team_id"]] = wrongs_by_team.get(w["team_id"], 0) + 1

    rows: list[PuzzleTeamRow] = []
    for team in teams:
        attempt = attempts_by_team.get(team.id)
        if attempt is None:
            rows.append(PuzzleTeamRow(
                team=team, status="not_arrived",
                arrived_at=None, solved_at=None,
                minutes_spent=None, hints_taken=0,
                wrong_answers=wrongs_by_team.get(team.id, 0),
            ))
            continue
        rows.append(PuzzleTeamRow(
            team=team,
            status=_attempt_status(attempt),
            arrived_at=attempt.arrived_at,
            solved_at=attempt.solved_at,
            minutes_spent=_minutes_spent(attempt, now),
            hints_taken=attempt.hints_taken,
            wrong_answers=wrongs_by_team.get(team.id, 0),
        ))
    rows.sort(key=lambda r: r.team.name.lower())
    return rows


def team_progress_matrix(hunt: Puzzlehunt) -> TeamProgressMatrix:
    """All teams × all puzzles, one cell summarizing each team's state per puzzle."""
    now = timezone.now()
    puzzles = list(hunt.puzzles.all())
    teams = list(hunt.teams.all())

    attempts_by_pair: dict[tuple[int, int], PuzzleAttempt] = {
        (a.team_id, a.puzzle_id): a
        for a in PuzzleAttempt.objects.filter(team__puzzlehunt=hunt)
    }

    rows: list[tuple[object, list[MatrixCell]]] = []
    for team in teams:
        cells = []
        for puzzle in puzzles:
            attempt = attempts_by_pair.get((team.id, puzzle.id))
            if attempt is None:
                cells.append(MatrixCell(
                    status="not_arrived", arrived_at=None, solved_at=None,
                    minutes_spent=None, hints_taken=0,
                ))
            else:
                cells.append(MatrixCell(
                    status=_attempt_status(attempt),
                    arrived_at=attempt.arrived_at,
                    solved_at=attempt.solved_at,
                    minutes_spent=_minutes_spent(attempt, now),
                    hints_taken=attempt.hints_taken,
                ))
        rows.append((team, cells))

    rows.sort(key=lambda tr: tr[0].name.lower())
    return TeamProgressMatrix(puzzles=puzzles, rows=rows)

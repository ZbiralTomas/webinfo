from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.auth import team_required

from . import game, scoring


def _common_context(team):
    score = scoring.compute_score(team)
    return {
        "team": team,
        "hunt": team.puzzlehunt,
        "score": score,
        "show_total_count": team.puzzlehunt.show_total_count,
        "now": timezone.now(),
    }


@team_required
def play_view(request):
    return redirect("hunts:active")


@team_required
def active_view(request):
    team = request.team
    active_attempts = list(game.get_active_attempts(team))
    rendered_attempts = []
    for a in active_attempts:
        rendered_attempts.append({
            "attempt": a,
            "elapsed_minutes": int((timezone.now() - a.arrived_at).total_seconds() // 60),
            "cooldown_seconds": game.cooldown_remaining(team, a.puzzle),
            "hints": game.hint_status_for(team, a),
        })
    return render(
        request,
        "hunts/play_active.html",
        {
            **_common_context(team),
            "active_tab": "active",
            "rendered_attempts": rendered_attempts,
            "max_active": team.puzzlehunt.max_active_puzzles,
            "active_count": len(active_attempts),
        },
    )


@team_required
def history_view(request):
    team = request.team
    finished = list(game.get_finished_attempts(team))
    rendered = []
    for a in finished:
        if a.solved_at is not None:
            status = "solved"
            stamp = a.solved_at
            duration = int((a.solved_at - a.arrived_at).total_seconds() // 60)
        else:
            status = "skipped"
            stamp = a.arrived_at
            duration = None
        rendered.append({
            "attempt": a,
            "status": status,
            "stamp": stamp,
            "duration_minutes": duration,
        })
    return render(
        request,
        "hunts/play_history.html",
        {**_common_context(team), "active_tab": "history", "history": rendered},
    )


@team_required
def contact_view(request):
    team = request.team
    contacts = list(team.puzzlehunt.contacts.all())
    return render(
        request,
        "hunts/play_contact.html",
        {**_common_context(team), "active_tab": "contact", "contacts": contacts},
    )


# ---------------------------------------------------------------------------
# POST endpoints
# ---------------------------------------------------------------------------


@team_required
@require_POST
def arrive_view(request):
    code = request.POST.get("arrival_code", "")
    try:
        attempt = game.enter_arrival_code(request.team, code)
    except game.GameError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"Dorazili jste k „{attempt.puzzle.name}“.")
    return redirect("hunts:active")


@team_required
@require_POST
def answer_view(request):
    puzzle_id = request.POST.get("puzzle_id")
    answer = request.POST.get("answer", "")
    try:
        result = game.submit_answer(request.team, int(puzzle_id), answer)
    except (TypeError, ValueError):
        messages.error(request, "Vyber šifru, na kterou chceš odpovědět.")
    except game.GameError as e:
        messages.error(request, str(e))
    else:
        if result.correct:
            messages.success(
                request,
                f"Správně u „{result.puzzle.name}“! {result.solve_message}".strip(),
            )
        else:
            messages.warning(
                request,
                f"To není správně. Počkej 1 minutu, než znovu zkusíš odpovědět na „{result.puzzle.name}“.",
            )
    return redirect("hunts:active")


@team_required
@require_POST
def hint_view(request):
    try:
        puzzle_id = int(request.POST.get("puzzle_id", ""))
        hint_order = int(request.POST.get("hint_order", ""))
    except (TypeError, ValueError):
        messages.error(request, "Špatný požadavek na nápovědu.")
        return redirect("hunts:active")
    try:
        hint = game.reveal_hint(request.team, puzzle_id, hint_order)
    except game.GameError as e:
        messages.error(request, str(e))
    else:
        messages.info(request, f"Nápověda {hint.order} odemčena: {hint.text}")
    return redirect("hunts:active")


@team_required
@require_POST
def skip_view(request):
    try:
        puzzle_id = int(request.POST.get("puzzle_id", ""))
    except (TypeError, ValueError):
        messages.error(request, "Špatný požadavek na přeskočení.")
        return redirect("hunts:active")
    try:
        attempt = game.skip_puzzle(request.team, puzzle_id)
    except game.GameError as e:
        messages.error(request, str(e))
    else:
        msg = f"Šifra „{attempt.puzzle.name}“ přeskočena."
        if attempt.puzzle.solve_message:
            msg += " " + attempt.puzzle.solve_message
        messages.success(request, msg)
    return redirect("hunts:active")
